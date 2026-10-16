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
"""Async client for the Xero Accounting API (2.0)."""

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from ._json import error_envelope
from .config import API_BASE_URL

logger = logging.getLogger("xero_mcp.api")

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 20.0
# Whole-call budget including retries; the gateway times a tool call out at 120s.
_CALL_BUDGET_SECONDS = 100.0

# One connection pool for the process. It never holds credentials: the
# Authorization header is built per request from the caller's own token, so
# sharing the pool across tenants is safe.
_shared_http_client: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _shared_http_client


# status_code -> (error code, retryable). 0 means no HTTP response at all
# (connection error or timeout).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    if status_code >= 400:
        return "invalid_argument", False
    return "upstream_error", True


class XeroError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Xero API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = classify_status(self.status_code)
        return error_envelope(code, self.message, retryable)


@dataclass(frozen=True)
class XeroCredentials:
    access_token: str
    tenant_id: str

    def __repr__(self) -> str:  # never leak the token into logs or tracebacks
        return f"XeroCredentials(tenant_id={self.tenant_id!r}, access_token=<redacted>)"


class XeroClient:
    """Thin wrapper over the Xero Accounting API for one caller's token and organisation."""

    def __init__(self, credentials: XeroCredentials, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self.base_url = API_BASE_URL

    @property
    def tenant_id(self) -> str:
        return self._credentials.tenant_id

    def _client(self) -> httpx.AsyncClient:
        return self._http or _get_shared_http_client()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def put(self, path: str, body: Any) -> Any:
        return await self.request("PUT", path, body=body)

    async def post(self, path: str, body: Any) -> Any:
        return await self.request("POST", path, body=body)

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        query = {k: _param_value(v) for k, v in (params or {}).items() if v is not None}
        headers = {
            "Authorization": f"Bearer {self._credentials.access_token}",
            "xero-tenant-id": self._credentials.tenant_id,
            "Accept": "application/json",
        }
        if method != "GET":
            # Xero de-duplicates writes by this key, so a retried write cannot
            # create a second invoice or payment.
            headers["Idempotency-Key"] = uuid.uuid4().hex
        started = time.monotonic()
        attempt = 0
        while True:
            t0 = time.monotonic()
            resp: httpx.Response | None = None
            error: XeroError | None = None
            try:
                resp = await self._client().request(method, url, params=query, json=body, headers=headers)
                status = resp.status_code
            except httpx.TimeoutException:
                status, error = 0, XeroError(0, f"Timed out calling Xero ({method} {path})")
            except httpx.RequestError as e:
                status = 0
                error = XeroError(0, f"Network error calling Xero ({method} {path}): {type(e).__name__}")

            logger.info(
                "xero_api method=%s path=%s status=%s ms=%d attempt=%d",
                method, path, status, (time.monotonic() - t0) * 1000, attempt,
            )

            if (status == 0 or status == 429 or status >= 500) and attempt < _MAX_RETRIES:
                delay = _retry_delay(resp, attempt)
                if time.monotonic() - started + delay < _CALL_BUDGET_SECONDS:
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
            if error is not None:
                raise error
            assert resp is not None
            if resp.status_code >= 400:
                raise XeroError(resp.status_code, _error_message(resp, path))
            return normalize_dates(_parse_body(resp))


def _param_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_BACKOFF_SECONDS))
            except ValueError:
                pass
    return min(float(2**attempt), _MAX_BACKOFF_SECONDS)


def _parse_body(resp: httpx.Response) -> Any:
    if not resp.content or not resp.content.strip():
        return None
    try:
        return resp.json()
    except ValueError:
        return {"raw_response": resp.text[:2000]}


_MS_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


def _iso(match: re.Match) -> str:
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(match.group(1)))
    offset = match.group(2)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        moment = moment.astimezone(timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))))
    return moment.isoformat().replace("+00:00", "Z")


def normalize_dates(value: Any) -> Any:
    """Turn Xero's legacy "/Date(1700000000000+0000)/" strings into ISO 8601."""
    if isinstance(value, dict):
        return {k: normalize_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_dates(v) for v in value]
    if isinstance(value, str):
        match = _MS_DATE.match(value)
        if match:
            return _iso(match)
    return value


def _error_message(resp: httpx.Response, path: str) -> str:
    fallback = f"Xero returned HTTP {resp.status_code} for {path}"
    problem = resp.headers.get("X-Rate-Limit-Problem")
    if resp.status_code == 429 and problem:
        fallback += f" ({problem} rate limit reached)"
    try:
        payload = resp.json()
    except ValueError:
        text = resp.text.strip()
        return f"{fallback}: {text[:300]}" if text else fallback
    if not isinstance(payload, dict):
        return fallback
    parts: list[str] = []
    for key in ("Message", "Detail", "Title"):
        if isinstance(payload.get(key), str) and payload[key]:
            parts.append(payload[key])
            break
    for element in payload.get("Elements") or []:
        for problem_item in (element or {}).get("ValidationErrors") or []:
            message = (problem_item or {}).get("Message")
            if message and message not in parts:
                parts.append(message)
    return f"{fallback}: {' - '.join(parts)[:500]}" if parts else fallback
