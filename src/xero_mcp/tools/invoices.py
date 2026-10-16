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
"""Invoices: sales invoices (ACCREC) and supplier bills (ACCPAY)."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..api_client import XeroClient
from ._common import (
    READ_ONLY,
    STATUS_CHANGE,
    WRITES,
    ClientFactory,
    OptionalIsoDate,
    Page,
    compact,
    first,
    guid,
    literal,
    page_result,
    run_tool,
    write_result,
)

InvoiceType = Literal["ACCREC", "ACCPAY"]
InvoiceId = Annotated[str, Field(description="Invoice id (UUID).")]


class LineItem(BaseModel):
    Description: str = Field(description="Line description.")
    Quantity: float = Field(description="Quantity.")
    UnitAmount: float = Field(description="Unit price.")
    AccountCode: str = Field(description="Account code from xero_accounts_list, e.g. 200.")
    TaxType: str | None = Field(default=None, description="Tax type, e.g. OUTPUT, INPUT or NONE (default: the account's).")


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def xero_invoices_list(
        page: Page = None,
        Status: Annotated[
            Literal["DRAFT", "SUBMITTED", "AUTHORISED", "PAID", "VOIDED", "DELETED"] | None,
            Field(description="Only invoices in this status."),
        ] = None,
        Type: Annotated[InvoiceType | None, Field(description="ACCREC = sales invoices, ACCPAY = bills.")] = None,
    ) -> str:
        """List invoices and bills, newest first, 100 per page: number, contact, dates, totals, status.

        Line items are left out; use xero_invoices_get for one invoice in full.
        """

        async def action(client: XeroClient) -> dict:
            params = compact(
                {
                    "page": page or 1,
                    "Statuses": Status,
                    "where": f"Type=={literal(Type)}" if Type else None,
                    "order": "Date DESC",
                    "summaryOnly": True,
                }
            )
            return page_result("Invoices", await client.get("Invoices", params), page)

        return await run_tool("xero_invoices_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_invoices_get(invoiceId: InvoiceId) -> str:
        """Get one invoice or bill in full: line items, tax, totals, payments, amount due and status."""

        async def action(client: XeroClient) -> Any:
            return first(await client.get(f"Invoices/{guid(invoiceId, 'invoiceId')}"), "Invoices")

        return await run_tool("xero_invoices_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def xero_invoices_create(
        Type: Annotated[InvoiceType, Field(description="ACCREC = sales invoice, ACCPAY = bill.")],
        ContactID: Annotated[str, Field(description="Contact id (UUID) to invoice or to bill from.")],
        LineItems: Annotated[list[LineItem], Field(description="At least one line.", min_length=1)],
        Date: OptionalIsoDate = None,
        DueDate: OptionalIsoDate = None,
        Reference: Annotated[str | None, Field(description="Reference / PO number.")] = None,
        Status: Annotated[
            Literal["DRAFT", "SUBMITTED", "AUTHORISED"] | None, Field(description="Initial status (default DRAFT).")
        ] = None,
    ) -> str:
        """Create a sales invoice or bill (DRAFT unless told otherwise). Only when the user explicitly asks."""

        async def action(client: XeroClient) -> Any:
            invoice = compact(
                {
                    "Type": Type,
                    "Contact": {"ContactID": guid(ContactID, "ContactID")},
                    "LineItems": [item.model_dump(exclude_none=True) for item in LineItems],
                    "Date": Date,
                    "DueDate": DueDate,
                    "Reference": Reference or None,
                    "Status": Status,
                }
            )
            return write_result(await client.put("Invoices", {"Invoices": [invoice]}), "Invoices")

        return await run_tool("xero_invoices_create", client_factory, action)

    @mcp.tool(annotations=STATUS_CHANGE)
    async def xero_invoices_update_status(
        invoiceId: InvoiceId,
        Status: Annotated[Literal["SUBMITTED", "AUTHORISED", "VOIDED"], Field(description="New status.")],
    ) -> str:
        """Submit, authorise or void an invoice. VOIDED is permanent: only when the user explicitly asks.

        Only AUTHORISED invoices without payments can be voided; drafts are
        deleted in Xero rather than voided.
        """

        async def action(client: XeroClient) -> Any:
            invoice_id = guid(invoiceId, "invoiceId")
            body = {"Invoices": [{"InvoiceID": invoice_id, "Status": Status}]}
            return write_result(await client.post(f"Invoices/{invoice_id}", body), "Invoices")

        return await run_tool("xero_invoices_update_status", client_factory, action)
