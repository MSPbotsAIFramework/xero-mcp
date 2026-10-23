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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import re
import uuid
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ._arguments import EnvelopeFastMCP
from .api_client import XeroClient, XeroCredentials
from .config import Settings

HEADER_ACCESS_TOKEN = "X-Xero-Access-Token"
HEADER_TENANT_ID = "X-Xero-Tenant-Id"
REQUIRED_HEADERS = [HEADER_ACCESS_TOKEN, HEADER_TENANT_ID]

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_BEARER = re.compile(r"^bearer(\s+|$)", re.IGNORECASE)

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level.
_credentials_var: contextvars.ContextVar[XeroCredentials | None] = contextvars.ContextVar(
    "xero_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("xero_request_id", default="-")

INSTRUCTIONS = (
    "Xero is cloud accounting software. This server works on one Xero "
    "organisation: contacts (customers and suppliers), invoices (ACCREC = sales "
    "invoices, ACCPAY = bills), payments, the chart of accounts and financial "
    "reports.\n"
    "Usage:\n"
    "- Ids are UUIDs. Find a contact with xero_contacts_search(term), then "
    "xero_invoices_list or xero_invoices_get for its invoices.\n"
    "- Lists return 100 records per page; has_more tells you to ask for the "
    "next page.\n"
    "- Dates are YYYY-MM-DD. Reports: xero_reports_profit_and_loss(fromDate, "
    "toDate), xero_reports_balance_sheet(date), and the aged receivables / "
    "payables reports (one contact, or an ageing summary of all open "
    "invoices).\n"
    "- xero_status checks the connection and names the organisation.\n"
    "Creating contacts, invoices or payments and changing an invoice's status "
    "change the books (an AUTHORISED invoice can be sent to a customer; "
    "VOIDED cannot be undone): call those tools only when the user explicitly "
    "asks, with the exact values given."
)


def get_client_from_context() -> XeroClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return XeroClient(credentials)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


class GatewayCredentialsMiddleware:
    """Reads X-Xero-Access-Token and X-Xero-Tenant-Id on /mcp requests; 401 when either is missing."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = _clean(request.headers.get(HEADER_ACCESS_TOKEN))
        token = _clean(_BEARER.sub("", token)) if token else None
        tenant_id = _clean(request.headers.get(HEADER_TENANT_ID))
        if not token or not tenant_id:
            missing = [name for name, value in ((HEADER_ACCESS_TOKEN, token), (HEADER_TENANT_ID, tenant_id)) if not value]
            await JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": f"Missing header(s): {', '.join(missing)}",
                    "required_headers": REQUIRED_HEADERS,
                },
                status_code=401,
            )(scope, receive, send)
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(XeroCredentials(access_token=token, tenant_id=tenant_id))
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = EnvelopeFastMCP(
        name="xero-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], XeroClient | None] = get_client_from_context

    from .tools import accounts, contacts, invoices, navigation, payments, reports

    for module in (navigation, contacts, invoices, payments, accounts, reports):
        module.register(mcp, client_factory)

    return mcp
