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
"""tools/list snapshot and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from xero_mcp.api_client import XeroError
from xero_mcp.server import INSTRUCTIONS, create_mcp_server

# Tool names are a public contract: existing agents and tenant connections
# depend on them. Changing this snapshot is a breaking change.
EXPECTED_TOOLS: dict[str, set[str]] = {
    "xero_navigate": {"domain"},
    "xero_status": set(),
    "xero_back": set(),
    "xero_contacts_list": set(),
    "xero_contacts_get": {"contactId"},
    "xero_contacts_create": {"Name"},
    "xero_contacts_search": {"term"},
    "xero_invoices_list": set(),
    "xero_invoices_get": {"invoiceId"},
    "xero_invoices_create": {"Type", "ContactID", "LineItems"},
    "xero_invoices_update_status": {"invoiceId", "Status"},
    "xero_payments_list": set(),
    "xero_payments_get": {"paymentId"},
    "xero_payments_create": {"InvoiceID", "AccountID", "Amount", "Date"},
    "xero_accounts_list": set(),
    "xero_accounts_get": {"accountId"},
    "xero_reports_profit_and_loss": {"fromDate", "toDate"},
    "xero_reports_balance_sheet": {"date"},
    "xero_reports_aged_receivables": {"date"},
    "xero_reports_aged_payables": {"date"},
}

WRITE_TOOLS = {"xero_contacts_create", "xero_invoices_create", "xero_invoices_update_status", "xero_payments_create"}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_required_params(tools, name):
    assert set(tools[name].inputSchema.get("required", [])) == EXPECTED_TOOLS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_annotations_match_side_effects(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is (name not in WRITE_TOOLS)
    assert bool(annotations.destructiveHint) is (name == "xero_invoices_update_status")


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_say_when_to_use_them(tools, name):
    assert "explicitly asks" in (tools[name].description or "")


def test_line_item_schema(tools):
    schema = tools["xero_invoices_create"].inputSchema
    line = schema["$defs"]["LineItem"]
    assert set(line["required"]) == {"Description", "Quantity", "UnitAmount", "AccountCode"}
    assert "TaxType" in line["properties"]


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(XeroError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


def test_tool_without_credentials_returns_not_configured():
    content, _ = asyncio.run(create_mcp_server().call_tool("xero_status", {}))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"


def test_navigate_needs_no_credentials():
    content, _ = asyncio.run(create_mcp_server().call_tool("xero_navigate", {"domain": "payments"}))
    assert json.loads(content[0].text)["tools"] == ["xero_payments_list", "xero_payments_get", "xero_payments_create"]
