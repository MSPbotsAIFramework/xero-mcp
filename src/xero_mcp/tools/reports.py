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
"""Financial reports: profit and loss, balance sheet, aged receivables / payables."""

from datetime import date
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import XeroClient
from ._common import (
    PAGE_SIZE,
    READ_ONLY,
    ClientFactory,
    IsoDate,
    compact,
    first,
    guid,
    parse_date,
    run_tool,
    where_date,
)

OptionalContactId = Annotated[
    str | None, Field(description="Contact id (UUID) for Xero's per-contact report; omit for an ageing summary of all contacts.")
]
_BUCKETS = ("current", "1_30", "31_60", "61_90", "over_90")
_MAX_PAGES = 20  # 2,000 open invoices


def _cells(row: dict) -> list:
    return [cell.get("Value") for cell in row.get("Cells") or [] if isinstance(cell, dict)]


def flatten_report(payload: Any) -> dict:
    """Xero's nested Rows/Cells report as {columns, sections: [{title, rows, total}]}."""
    report = first(payload, "Reports")
    if not isinstance(report, dict):
        return {"report": payload}
    columns: list = []
    sections: list[dict] = []
    for row in report.get("Rows") or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("RowType")
        if kind == "Header":
            columns = _cells(row)[1:]
        elif kind == "Section":
            lines, total = [], None
            for child in row.get("Rows") or []:
                if not isinstance(child, dict):
                    continue
                values = _cells(child)
                entry = {"label": values[0] if values else None, "values": values[1:]}
                if child.get("RowType") == "SummaryRow":
                    total = entry
                else:
                    lines.append(entry)
            if lines or total or row.get("Title"):
                sections.append(compact({"title": row.get("Title") or None, "rows": lines, "total": total}))
        elif kind in ("Row", "SummaryRow"):
            values = _cells(row)
            sections.append({"rows": [{"label": values[0] if values else None, "values": values[1:]}]})
    return compact(
        {
            "name": report.get("ReportName"),
            "titles": report.get("ReportTitles"),
            "date": report.get("ReportDate"),
            "updated": report.get("UpdatedDateUTC"),
            "columns": columns,
            "sections": sections,
        }
    )


def _bucket(due: date | None, as_of: date) -> str:
    if due is None or due >= as_of:
        return "current"
    days = (as_of - due).days
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "over_90"


def _to_date(value: Any) -> date | None:
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


async def ageing_summary(client: XeroClient, invoice_type: str, as_of: date) -> dict:
    """Bucket every open (AUTHORISED, amount due) invoice of a type by days past due."""
    where = f'Type=="{invoice_type}" AND AmountDue>0 AND {where_date("Date", "<=", as_of)}'
    invoices: list = []
    truncated = False
    for page in range(1, _MAX_PAGES + 1):
        payload = await client.get("Invoices", {"Statuses": "AUTHORISED", "where": where, "page": page, "summaryOnly": True})
        batch = (payload or {}).get("Invoices") or []
        invoices.extend(i for i in batch if isinstance(i, dict))
        if len(batch) < PAGE_SIZE:
            break
    else:
        truncated = True

    contacts: dict[str, dict] = {}
    totals = dict.fromkeys(_BUCKETS, 0.0)
    for invoice in invoices:
        amount = float(invoice.get("AmountDue") or 0)
        bucket = _bucket(_to_date(invoice.get("DueDate")), as_of)
        contact = invoice.get("Contact") or {}
        key = contact.get("ContactID") or "unknown"
        row = contacts.setdefault(
            key,
            {"contact_id": contact.get("ContactID"), "name": contact.get("Name"), **dict.fromkeys(_BUCKETS, 0.0), "total": 0.0, "invoices": 0},
        )
        row[bucket] += amount
        row["total"] += amount
        row["invoices"] += 1
        totals[bucket] += amount
    rounded = [{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()} for row in contacts.values()]
    rounded.sort(key=lambda r: -r["total"])
    return compact(
        {
            "as_of": as_of.isoformat(),
            "source": "open invoices",
            "buckets": "days past due date: current (not yet due), 1_30, 31_60, 61_90, over_90",
            "totals": {k: round(v, 2) for k, v in totals.items()} | {"total": round(sum(totals.values()), 2)},
            "invoice_count": len(invoices),
            "contacts": rounded,
            "truncated": truncated or None,
            "note": (
                "Built from currently open invoices dated on or before as_of; amounts are today's amount due. "
                "Pass contactId for Xero's own aged report of one contact."
            ),
        }
    )


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def xero_reports_profit_and_loss(fromDate: IsoDate, toDate: IsoDate) -> str:
        """Get the Profit and Loss (income statement) for a date range: income, expenses and net profit."""

        async def action(client: XeroClient) -> dict:
            if parse_date(fromDate, "fromDate") > parse_date(toDate, "toDate"):
                raise ValueError("fromDate must be on or before toDate.")
            return flatten_report(await client.get("Reports/ProfitAndLoss", {"fromDate": fromDate, "toDate": toDate}))

        return await run_tool("xero_reports_profit_and_loss", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_reports_balance_sheet(date: IsoDate) -> str:
        """Get the Balance Sheet as at a date: assets, liabilities and equity."""

        async def action(client: XeroClient) -> dict:
            parse_date(date, "date")
            return flatten_report(await client.get("Reports/BalanceSheet", {"date": date}))

        return await run_tool("xero_reports_balance_sheet", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_reports_aged_receivables(date: IsoDate, contactId: OptionalContactId = None) -> str:
        """Get money owed by customers, aged by days overdue (current, 1-30, 31-60, 61-90, 90+).

        With contactId: Xero's aged receivables report for that customer.
        Without: a per-customer ageing summary built from open sales invoices.
        """

        async def action(client: XeroClient) -> dict:
            as_of = parse_date(date, "date")
            if contactId:
                params = {"contactId": guid(contactId, "contactId"), "date": date}
                return flatten_report(await client.get("Reports/AgedReceivablesByContact", params))
            return await ageing_summary(client, "ACCREC", as_of)

        return await run_tool("xero_reports_aged_receivables", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_reports_aged_payables(date: IsoDate, contactId: OptionalContactId = None) -> str:
        """Get money owed to suppliers, aged by days overdue (current, 1-30, 31-60, 61-90, 90+).

        With contactId: Xero's aged payables report for that supplier.
        Without: a per-supplier ageing summary built from open bills.
        """

        async def action(client: XeroClient) -> dict:
            as_of = parse_date(date, "date")
            if contactId:
                params = {"contactId": guid(contactId, "contactId"), "date": date}
                return flatten_report(await client.get("Reports/AgedPayablesByContact", params))
            return await ageing_summary(client, "ACCPAY", as_of)

        return await run_tool("xero_reports_aged_payables", client_factory, action)
