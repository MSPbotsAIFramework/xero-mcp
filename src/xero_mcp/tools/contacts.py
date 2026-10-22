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
"""Contacts: the organisation's customers and suppliers."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import XeroClient
from ._common import (
    READ_ONLY,
    WRITES,
    ClientFactory,
    Offset,
    Page,
    compact,
    first,
    guid,
    locate,
    page_result,
    run_tool,
    write_result,
)

Text = Annotated[str | None, Field(description="Optional.")]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def xero_contacts_list(
        page: Page = None,
        where: Annotated[
            str | None, Field(description='Xero filter expression, e.g. ContactStatus=="ACTIVE" or IsSupplier==true.')
        ] = None,
        offset: Offset = None,
    ) -> str:
        """List contacts (customers and suppliers): name, email, phones, addresses, status; 100 per page."""

        async def action(client: XeroClient) -> dict:
            number, start = locate(offset, page)
            params = compact({"page": number, "where": (where or "").strip() or None, "order": "Name ASC"})
            return page_result("Contacts", await client.get("Contacts", params), number, start)

        return await run_tool("xero_contacts_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_contacts_get(contactId: Annotated[str, Field(description="Contact id (UUID).")]) -> str:
        """Get one contact's full profile: addresses, phones, emails, tax number, balances and payment terms."""

        async def action(client: XeroClient) -> Any:
            return first(await client.get(f"Contacts/{guid(contactId, 'contactId')}"), "Contacts")

        return await run_tool("xero_contacts_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def xero_contacts_create(
        Name: Annotated[str, Field(description="Contact (company or person) name; must be unique in Xero.", min_length=1)],
        EmailAddress: Text = None,
        FirstName: Text = None,
        LastName: Text = None,
        Phone: Annotated[str | None, Field(description="Default phone number.")] = None,
        AccountNumber: Annotated[str | None, Field(description="Your account number for the contact.")] = None,
        TaxNumber: Annotated[str | None, Field(description="Tax number (ABN, GST or VAT number).")] = None,
        IsCustomer: Annotated[bool | None, Field(description="Mark as a customer.")] = None,
        IsSupplier: Annotated[bool | None, Field(description="Mark as a supplier.")] = None,
    ) -> str:
        """Create a contact (customer or supplier). Only when the user explicitly asks."""

        async def action(client: XeroClient) -> Any:
            name = Name.strip()
            if not name:
                raise ValueError("Name must not be blank.")
            contact = compact(
                {
                    "Name": name,
                    "EmailAddress": EmailAddress or None,
                    "FirstName": FirstName or None,
                    "LastName": LastName or None,
                    "Phones": [{"PhoneType": "DEFAULT", "PhoneNumber": Phone.strip()}] if Phone and Phone.strip() else None,
                    "AccountNumber": AccountNumber or None,
                    "TaxNumber": TaxNumber or None,
                    "IsCustomer": IsCustomer,
                    "IsSupplier": IsSupplier,
                }
            )
            return write_result(await client.put("Contacts", {"Contacts": [contact]}), "Contacts")

        return await run_tool("xero_contacts_create", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_contacts_search(
        term: Annotated[str, Field(description="Text to find in the name, first / last name, contact number or email.", min_length=1)],
    ) -> str:
        """Search contacts by name, contact person, account number or email (case-insensitive)."""

        async def action(client: XeroClient) -> dict:
            text = term.strip()
            if not text:
                raise ValueError("term must not be blank.")
            return page_result("Contacts", await client.get("Contacts", {"searchTerm": text, "page": 1}), 1)

        return await run_tool("xero_contacts_search", client_factory, action)
