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
"""Credential middleware: 401 on missing headers, per-request isolation."""

import asyncio

import pytest
from starlette.testclient import TestClient

from xero_mcp.__main__ import build_http_app
from xero_mcp.server import GatewayCredentialsMiddleware, _credentials_var, create_mcp_server, get_client_from_context

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
CREDS = {"X-Xero-Access-Token": "your-access-token", "X-Xero-Tenant-Id": "your-tenant-id"}


def _client() -> TestClient:
    return TestClient(build_http_app(create_mcp_server()))


def test_health_is_local():
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers,missing",
    [
        ({}, "X-Xero-Access-Token"),
        ({"X-Xero-Access-Token": "t"}, "X-Xero-Tenant-Id"),
        ({"X-Xero-Tenant-Id": "x"}, "X-Xero-Access-Token"),
        ({"X-Xero-Access-Token": "Bearer ", "X-Xero-Tenant-Id": "x"}, "X-Xero-Access-Token"),
        ({"X-Xero-Access-Token": "${XERO_TOKEN}", "X-Xero-Tenant-Id": "x"}, "X-Xero-Access-Token"),
    ],
)
def test_missing_credentials_return_401(headers, missing):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers)
    assert resp.status_code == 401
    body = resp.json()
    assert body["required_headers"] == ["X-Xero-Access-Token", "X-Xero-Tenant-Id"]
    assert missing in body["message"]


def test_tools_list_over_http():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS)
    assert resp.status_code == 200
    for name in ("xero_navigate", "xero_invoices_create", "xero_reports_aged_payables"):
        assert name in resp.text


def _run_middleware(headers: list[tuple[bytes, bytes]]) -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["credentials"] = _credentials_var.get()
        seen["client"] = get_client_from_context()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(GatewayCredentialsMiddleware(app)({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    return seen


def test_headers_reach_request_context_and_are_reset():
    seen = _run_middleware([(b"x-xero-access-token", b"Bearer token-123"), (b"x-xero-tenant-id", b" tenant-1 ")])
    assert seen["credentials"].access_token == "token-123"
    assert seen["client"].tenant_id == "tenant-1"
    assert _credentials_var.get() is None


def test_concurrent_requests_do_not_share_credentials():
    observed: list[tuple[str, str]] = []

    async def app(scope, receive, send):
        before = _credentials_var.get().tenant_id
        await asyncio.sleep(0.01)
        observed.append((before, _credentials_var.get().tenant_id))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = GatewayCredentialsMiddleware(app)

    def scope(tenant: bytes) -> dict:
        return {"type": "http", "path": "/mcp", "headers": [(b"x-xero-access-token", b"t"), (b"x-xero-tenant-id", tenant)]}

    async def both():
        await asyncio.gather(middleware(scope(b"tenant-a"), receive, send), middleware(scope(b"tenant-b"), receive, send))

    asyncio.run(both())
    assert sorted(observed) == [("tenant-a", "tenant-a"), ("tenant-b", "tenant-b")]


def test_credentials_repr_hides_token():
    seen = _run_middleware([(b"x-xero-access-token", b"very-secret-token"), (b"x-xero-tenant-id", b"t")])
    assert "very-secret-token" not in repr(seen["credentials"])
