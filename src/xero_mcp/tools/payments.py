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
"""Payments applied to invoices and bills."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import XeroClient
from ._common import (
    READ_ONLY,
    WRITES,
    ClientFactory,
    IsoDate,
    Offset,
    Page,
    compact,
    first,
    guid,
    literal,
    locate,
    page_result,
    run_tool,
    write_result,
)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def xero_payments_list(
        page: Page = None,
        Status: Annotated[Literal["AUTHORISED", "DELETED"] | None, Field(description="Only payments in this status.")] = None,
        offset: Offset = None,
    ) -> str:
        """List payments, newest first, 100 per page: date, amount, invoice, bank account and status."""

        async def action(client: XeroClient) -> dict:
            number, start = locate(offset, page)
            params = compact({"page": number, "where": f"Status=={literal(Status)}" if Status else None, "order": "Date DESC"})
            return page_result("Payments", await client.get("Payments", params), number, start)

        return await run_tool("xero_payments_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_payments_get(paymentId: Annotated[str, Field(description="Payment id (UUID).")]) -> str:
        """Get one payment: date, amount, currency rate, invoice, bank account and reference."""

        async def action(client: XeroClient) -> Any:
            return first(await client.get(f"Payments/{guid(paymentId, 'paymentId')}"), "Payments")

        return await run_tool("xero_payments_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def xero_payments_create(
        InvoiceID: Annotated[str, Field(description="Invoice or bill id (UUID); it must be AUTHORISED.")],
        AccountID: Annotated[str, Field(description="Bank account id (UUID) the money went into or out of.")],
        Amount: Annotated[float, Field(description="Amount paid, at most the amount due.", gt=0)],
        Date: IsoDate,
        Reference: Annotated[str | None, Field(description="Payment reference.")] = None,
    ) -> str:
        """Record a payment against an authorised invoice or bill. Only when the user explicitly asks."""

        async def action(client: XeroClient) -> Any:
            payment = compact(
                {
                    "Invoice": {"InvoiceID": guid(InvoiceID, "InvoiceID")},
                    "Account": {"AccountID": guid(AccountID, "AccountID")},
                    "Amount": Amount,
                    "Date": Date,
                    "Reference": Reference or None,
                }
            )
            return write_result(await client.put("Payments", {"Payments": [payment]}), "Payments")

        return await run_tool("xero_payments_create", client_factory, action)
