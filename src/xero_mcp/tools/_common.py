# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Shared argument types, filter / paging helpers and the error-handling wrapper."""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import XeroClient, XeroError

logger = logging.getLogger("xero_mcp.tools")

ClientFactory = Callable[[], XeroClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
# Voiding an invoice cannot be undone.
STATUS_CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured",
    "Xero credentials are missing for this request (X-Xero-Access-Token and X-Xero-Tenant-Id).",
    False,
)

PAGE_SIZE = 100
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

Page = Annotated[int | None, Field(description="Page number from 1 (100 records per page).", ge=1)]
IsoDate = Annotated[str, Field(description="Date as YYYY-MM-DD.", pattern=r"^\d{4}-\d{2}-\d{2}$")]
OptionalIsoDate = Annotated[str | None, Field(description="Date as YYYY-MM-DD.", pattern=r"^\d{4}-\d{2}-\d{2}$")]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def guid(value: str, name: str) -> str:
    text = value.strip()
    if not _UUID.match(text):
        raise ValueError(f"{name} must be a Xero id (UUID), got {value!r}.")
    return text


def literal(value: str) -> str:
    """A string literal for a Xero where clause."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"{name} must be a real date as YYYY-MM-DD, got {value!r}.") from e


def where_date(field: str, op: str, value: date) -> str:
    return f"{field} {op} DateTime({value.year},{value.month:02d},{value.day:02d})"


def page_result(key: str, payload: Any, page: int | None) -> dict:
    items = payload.get(key) if isinstance(payload, dict) else None
    items = items if isinstance(items, list) else []
    number = page or 1
    more = len(items) >= PAGE_SIZE
    return compact(
        {
            key: items,
            "count": len(items),
            "page": number,
            "has_more": more,
            "next_page": number + 1 if more else None,
        }
    )


def first(payload: Any, key: str) -> Any:
    """The single record of a Xero {Key: [record]} envelope."""
    items = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(items, list) and items:
        return items[0]
    return payload


def write_result(payload: Any, key: str) -> Any:
    """Created / updated records, surfacing per-record validation errors Xero returns with 200."""
    record = first(payload, key)
    errors = record.get("ValidationErrors") if isinstance(record, dict) else None
    if errors:
        messages = [e.get("Message") for e in errors if isinstance(e, dict) and e.get("Message")]
        raise XeroError(400, "Xero rejected the record: " + " - ".join(messages or ["validation error"]))
    return record


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[XeroClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        result = await action(client)
    except XeroError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"result": None, "note": "Xero returned an empty response."}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
