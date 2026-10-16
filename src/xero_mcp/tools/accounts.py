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
"""Chart of accounts."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import XeroClient
from ._common import READ_ONLY, ClientFactory, first, guid, literal, run_tool

_TYPE = r"^[A-Z]+$"


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def xero_accounts_list(
        Type: Annotated[
            str | None,
            Field(
                description="Account type, e.g. BANK, REVENUE, SALES, EXPENSE, OVERHEADS, DIRECTCOSTS, CURRENT, FIXED, CURRLIAB, EQUITY.",
                pattern=_TYPE,
            ),
        ] = None,
        Class: Annotated[
            Literal["ASSET", "EQUITY", "EXPENSE", "LIABILITY", "REVENUE"] | None, Field(description="Account class.")
        ] = None,
    ) -> str:
        """List the chart of accounts (code, name, type, class, tax type, status), optionally by type or class."""

        async def action(client: XeroClient) -> dict:
            clauses = [f"Type=={literal(Type)}" if Type else None, f"Class=={literal(Class)}" if Class else None]
            where = " AND ".join(c for c in clauses if c) or None
            params = {"where": where, "order": "Code ASC"}
            items = (await client.get("Accounts", params) or {}).get("Accounts") or []
            return {"Accounts": items, "count": len(items)}

        return await run_tool("xero_accounts_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def xero_accounts_get(accountId: Annotated[str, Field(description="Account id (UUID).")]) -> str:
        """Get one account by id: code, name, type, class, tax type, bank details and status."""

        async def action(client: XeroClient) -> Any:
            return first(await client.get(f"Accounts/{guid(accountId, 'accountId')}"), "Accounts")

        return await run_tool("xero_accounts_get", client_factory, action)
