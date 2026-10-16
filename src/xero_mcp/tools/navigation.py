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
"""Tool discovery (kept for compatibility) and the connection check."""

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import compact as to_json
from ..api_client import XeroClient
from ._common import LOCAL, READ_ONLY, ClientFactory, compact, first, run_tool

DOMAINS: dict[str, tuple[str, list[str]]] = {
    "contacts": (
        "Customers and suppliers: list, get, search, create.",
        ["xero_contacts_list", "xero_contacts_get", "xero_contacts_create", "xero_contacts_search"],
    ),
    "invoices": (
        "Sales invoices (ACCREC) and bills (ACCPAY): list, get, create, change status.",
        ["xero_invoices_list", "xero_invoices_get", "xero_invoices_create", "xero_invoices_update_status"],
    ),
    "payments": (
        "Payments against invoices: list, get, record.",
        ["xero_payments_list", "xero_payments_get", "xero_payments_create"],
    ),
    "accounts": ("Chart of accounts: list by type or class, get.", ["xero_accounts_list", "xero_accounts_get"]),
    "reports": (
        "Profit and loss, balance sheet, aged receivables and aged payables.",
        [
            "xero_reports_profit_and_loss",
            "xero_reports_balance_sheet",
            "xero_reports_aged_receivables",
            "xero_reports_aged_payables",
        ],
    ),
}


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=LOCAL)
    async def xero_navigate(
        domain: Annotated[
            Literal["contacts", "invoices", "payments", "accounts", "reports"], Field(description="Area to describe.")
        ],
    ) -> str:
        """Describe the tools of one Xero area. All tools are already callable; this changes nothing."""
        summary, tools = DOMAINS[domain]
        return to_json({"domain": domain, "summary": summary, "tools": tools})

    @mcp.tool(annotations=LOCAL)
    async def xero_back() -> str:
        """List every Xero area and its tools. All tools are already callable; this changes nothing."""
        return to_json({"domains": {name: {"summary": s, "tools": t} for name, (s, t) in DOMAINS.items()}})

    @mcp.tool(annotations=READ_ONLY)
    async def xero_status() -> str:
        """Check the Xero connection: organisation name, country, base currency and the tool areas."""

        async def action(client: XeroClient) -> dict:
            org = first(await client.get("Organisation"), "Organisations")
            org = org if isinstance(org, dict) else {}
            return {
                "connected": True,
                "tenant_id": client.tenant_id,
                "organisation": compact(
                    {
                        "name": org.get("Name"),
                        "legal_name": org.get("LegalName"),
                        "country": org.get("CountryCode"),
                        "base_currency": org.get("BaseCurrency"),
                        "financial_year_end": (
                            f"{org.get('FinancialYearEndDay')}/{org.get('FinancialYearEndMonth')}"
                            if org.get("FinancialYearEndDay")
                            else None
                        ),
                        "is_demo_company": org.get("IsDemoCompany"),
                    }
                ),
                "domains": {name: summary for name, (summary, _tools) in DOMAINS.items()},
            }

        return await run_tool("xero_status", client_factory, action)
