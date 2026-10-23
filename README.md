# Xero MCP Server

An [MCP](https://modelcontextprotocol.io) server for the [Xero](https://www.xero.com)
Accounting API (2.0). It lets an AI agent read one Xero organisation's contacts, invoices and
bills, payments, chart of accounts and financial reports (profit and loss, balance sheet, aged
receivables and payables) and, on explicit request, create contacts, invoices and payments or
change an invoice's status.

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: the access token and organisation id arrive on every request as
  HTTP headers and are never stored; every tool is listed from the first `tools/list`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Xero-Access-Token` | Yes | string | none | - | Xero OAuth 2.0 access token (a leading `Bearer ` is accepted and ignored) | `your-access-token` |
| `X-Xero-Tenant-Id` | Yes | string | none | - | Id of the Xero organisation (tenant) to work on | `your-tenant-id` |

Where to get them:

- Create an app in the Xero developer portal (developer.xero.com, **My Apps -> New app**) and
  authorise it for the organisation with the OAuth 2.0 authorisation-code flow. Request the
  scopes the tools need: `accounting.contacts`, `accounting.transactions`,
  `accounting.settings` and `accounting.reports.read` (the `.read` variants for read-only use).
- The token response holds a short-lived access token; whoever stores the connection must
  refresh it with the refresh token. This server never refreshes or stores
  tokens.
- The tenant id comes from `GET https://api.xero.com/connections` with that token; each
  organisation the user authorised has its own `tenantId`.

How credentials are sent to Xero: every call goes to `https://api.xero.com/api.xro/2.0/...`
with `Authorization: Bearer <token>` and `xero-tenant-id: <tenant id>`. Writes also carry a
fresh `Idempotency-Key` header, so a retried write cannot create a second record. Values that
look like unresolved templates (`${...}`) are treated as missing.

A request to `/mcp` without both headers gets HTTP 401 naming the missing one.

## Tools

All tools return a JSON string. Ids are UUIDs. List tools return 100 records per page with
`{count, page, has_more, next_page}`. Dates are `YYYY-MM-DD`; Xero's legacy `/Date(...)/`
values in responses are converted to ISO 8601.

`xero_contacts_list`, `xero_invoices_list` and `xero_payments_list` also take `offset` (0-based
record position, overrides `page`) and return `offset` / `next_offset`: pass `next_offset` to continue. It
starts right after the last record returned, also when a result was cut to fit the response size limit,
where the next page number would skip the rest of the cut page.

| Tool | Purpose | Parameters (required in **bold**) |
|---|---|---|
| `xero_navigate` | Describe the tools of one Xero area (changes nothing) | **domain** (`contacts`, `invoices`, `payments`, `accounts`, `reports`) |
| `xero_back` | List every Xero area and its tools (changes nothing) | - |
| `xero_status` | Check the connection: organisation name, country, base currency, financial year end | - |
| `xero_contacts_list` | List contacts (customers and suppliers), 100 per page | page, where |
| `xero_contacts_get` | Get one contact's full profile | **contactId** |
| `xero_contacts_create` | Create a contact (**write**) | **Name**, EmailAddress, FirstName, LastName, Phone, AccountNumber, TaxNumber, IsCustomer, IsSupplier |
| `xero_contacts_search` | Search contacts by name, contact person, account number or email | **term** |
| `xero_invoices_list` | List invoices and bills, newest first, without line items | page, Status (`DRAFT`, `SUBMITTED`, `AUTHORISED`, `PAID`, `VOIDED`, `DELETED`), Type (`ACCREC`, `ACCPAY`) |
| `xero_invoices_get` | Get one invoice or bill in full (line items, tax, payments, amount due) | **invoiceId** |
| `xero_invoices_create` | Create a sales invoice or bill, DRAFT by default (**write**) | **Type** (`ACCREC`, `ACCPAY`), **ContactID**, **LineItems** (Description, Quantity, UnitAmount, AccountCode, TaxType), Date, DueDate, Reference, Status (`DRAFT`, `SUBMITTED`, `AUTHORISED`) |
| `xero_invoices_update_status` | Submit, authorise or void an invoice (**write**, voiding is permanent) | **invoiceId**, **Status** (`SUBMITTED`, `AUTHORISED`, `VOIDED`) |
| `xero_payments_list` | List payments, newest first | page, Status (`AUTHORISED`, `DELETED`) |
| `xero_payments_get` | Get one payment | **paymentId** |
| `xero_payments_create` | Record a payment against an authorised invoice or bill (**write**) | **InvoiceID**, **AccountID**, **Amount**, **Date**, Reference |
| `xero_accounts_list` | List the chart of accounts, optionally by type or class | Type, Class (`ASSET`, `EQUITY`, `EXPENSE`, `LIABILITY`, `REVENUE`) |
| `xero_accounts_get` | Get one account | **accountId** |
| `xero_reports_profit_and_loss` | Profit and Loss for a date range | **fromDate**, **toDate** |
| `xero_reports_balance_sheet` | Balance Sheet as at a date | **date** |
| `xero_reports_aged_receivables` | Money owed by customers, aged by days overdue | **date**, contactId |
| `xero_reports_aged_payables` | Money owed to suppliers, aged by days overdue | **date**, contactId |

Reports come back flattened as `{name, titles, date, columns, sections: [{title, rows:
[{label, values}], total}]}` instead of Xero's nested rows and cells.

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "invalid_argument", "message": "Xero returned HTTP 400 for Invoices: A validation exception occurred - Account code '999' is not a valid code", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`; Xero's validation messages are included. Xero allows 60
calls per minute and 5,000 per day per organisation; 429, 5xx and network errors are retried
with backoff (honouring `Retry-After`), and writes are safe to retry because of the idempotency
key.

## Run locally

```bash
uv sync
uv run python -m xero_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t xero-mcp:dev .
docker run --rm -p 8080:8080 xero-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). Credentials are never read
from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Xero-Access-Token: your-access-token" \
  -H "X-Xero-Tenant-Id: your-tenant-id" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# unpaid sales invoices
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Xero-Access-Token: your-access-token" \
  -H "X-Xero-Tenant-Id: your-tenant-id" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"xero_invoices_list","arguments":{"Type":"ACCREC","Status":"AUTHORISED"}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **Tool count (20).** Kept as is because the tool names are an existing contract.
- **Aged reports.** Xero's aged receivables / payables reports require a contact
  (`contactId`), and the API has no all-contacts version. With `contactId` the tools return
  Xero's report; without it they return a per-contact ageing summary (current, 1-30, 31-60,
  61-90, over 90 days past due) built from open authorised invoices dated on or before `date`,
  using today's amount due.
- **Searching.** `xero_contacts_search` uses Xero's `searchTerm` parameter, so names with quotes
  are found and cannot break the filter; other filters escape their values.
- **Write tools.** `xero_contacts_create`, `xero_invoices_create`, `xero_payments_create` and
  `xero_invoices_update_status` change the books; their descriptions tell the agent to use them
  only on explicit request. Status changes are annotated destructive because voiding cannot be
  undone. Per-record validation errors that Xero returns with HTTP 200 are reported as errors.
- **No interactive prompts.** Older versions asked the user for filters before unfiltered
  lists, which a stateless gateway cannot relay; lists now page instead.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
