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
"""Headers, retries, date handling and tool request shapes against a mocked Xero API."""

import json

import httpx
import pytest

from xero_mcp import api_client
from xero_mcp.api_client import XeroClient, XeroCredentials, XeroError, normalize_dates
from xero_mcp.server import _credentials_var, create_mcp_server

CREDS = XeroCredentials(access_token="token-123", tenant_id="tenant-1")
CONTACT = "11111111-2222-3333-4444-555555555555"
INVOICE = "66666666-7777-8888-9999-000000000000"
ACCOUNT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


class Recorder:
    """Mock transport: replies by "METHOD path" or path below /api.xro/2.0/."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api.xro/2.0/")
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder) -> XeroClient:
    return XeroClient(CREDS, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


# ---- client ----------------------------------------------------------------


async def test_headers_and_no_idempotency_key_on_reads():
    rec = Recorder()
    await _client(rec).get("Contacts", {"page": 2, "summaryOnly": True})
    request = rec.requests[0]
    assert request.headers["Authorization"] == "Bearer token-123"
    assert request.headers["xero-tenant-id"] == "tenant-1"
    assert "Idempotency-Key" not in request.headers
    assert str(request.url) == "https://api.xero.com/api.xro/2.0/Contacts?page=2&summaryOnly=true"


async def test_write_retry_reuses_one_idempotency_key():
    replies = iter([httpx.Response(503), httpx.Response(200, json={"Invoices": [{"InvoiceID": INVOICE}]})])
    rec = Recorder({"PUT Invoices": lambda r: next(replies)})
    await _client(rec).put("Invoices", {"Invoices": []})
    keys = {r.headers["Idempotency-Key"] for r in rec.requests}
    assert len(rec.requests) == 2 and len(keys) == 1


async def test_429_names_the_rate_limit():
    rec = Recorder({"Contacts": lambda r: httpx.Response(429, headers={"Retry-After": "1", "X-Rate-Limit-Problem": "minute"})})
    with pytest.raises(XeroError) as err:
        await _client(rec).get("Contacts")
    assert err.value.status_code == 429
    assert "minute rate limit" in err.value.message
    assert len(rec.requests) == 3


async def test_validation_errors_are_collected():
    error = {
        "ErrorNumber": 10,
        "Type": "ValidationException",
        "Message": "A validation exception occurred",
        "Elements": [{"ValidationErrors": [{"Message": "Account code '999' is not a valid code"}]}],
    }
    rec = Recorder({"PUT Invoices": lambda r: httpx.Response(400, json=error)})
    with pytest.raises(XeroError) as err:
        await _client(rec).put("Invoices", {})
    assert "Account code '999' is not a valid code" in err.value.message


def test_normalize_dates():
    payload = {"Date": "/Date(1780272000000+0000)/", "Nested": [{"Updated": "/Date(1780272000000+1000)/"}], "Name": "x"}
    assert normalize_dates(payload) == {
        "Date": "2026-06-01T00:00:00Z",
        "Nested": [{"Updated": "2026-06-01T10:00:00+10:00"}],
        "Name": "x",
    }


async def test_status_names_the_organisation(monkeypatch):
    org = {"Organisations": [{"Name": "Example Ltd", "CountryCode": "NZ", "BaseCurrency": "NZD", "FinancialYearEndDay": 31, "FinancialYearEndMonth": 3}]}
    result = await _call(monkeypatch, Recorder({"Organisation": org}), "xero_status", {})
    assert result["organisation"] == {"name": "Example Ltd", "country": "NZ", "base_currency": "NZD", "financial_year_end": "31/3"}
    assert result["tenant_id"] == "tenant-1"


async def test_bad_token_is_unauthorized(monkeypatch):
    rec = Recorder({"Organisation": lambda r: httpx.Response(401, json={"Title": "Unauthorized", "Detail": "TokenExpired"})})
    result = await _call(monkeypatch, rec, "xero_status", {})
    assert result["error"]["code"] == "unauthorized"
    assert "token-123" not in json.dumps(result)


# ---- contacts --------------------------------------------------------------


async def test_contacts_list_pages(monkeypatch):
    rec = Recorder({"Contacts": {"Contacts": [{"ContactID": str(i)} for i in range(100)]}})
    result = await _call(monkeypatch, rec, "xero_contacts_list", {"page": 2, "where": 'ContactStatus=="ACTIVE"'})
    params = rec.requests[0].url.params
    assert (params["page"], params["where"]) == ("2", 'ContactStatus=="ACTIVE"')
    assert (result["count"], result["has_more"], result["next_page"]) == (100, True, 3)


async def test_contacts_search_uses_search_term(monkeypatch):
    rec = Recorder({"Contacts": {"Contacts": [{"Name": "O'Neil \"Bros\""}]}})
    result = await _call(monkeypatch, rec, "xero_contacts_search", {"term": ' O\'Neil "Bros" '})
    params = rec.requests[0].url.params
    assert params["searchTerm"] == 'O\'Neil "Bros"'
    assert "where" not in params
    assert result["has_more"] is False


async def test_contact_create_body(monkeypatch):
    rec = Recorder({"PUT Contacts": {"Contacts": [{"ContactID": CONTACT, "Name": "Example Ltd"}]}})
    result = await _call(monkeypatch, rec, "xero_contacts_create", {"Name": "Example Ltd", "Phone": "021 555", "IsCustomer": True})
    assert body(rec.requests[0]) == {
        "Contacts": [{"Name": "Example Ltd", "Phones": [{"PhoneType": "DEFAULT", "PhoneNumber": "021 555"}], "IsCustomer": True}]
    }
    assert result == {"ContactID": CONTACT, "Name": "Example Ltd"}


async def test_record_level_validation_error_is_reported(monkeypatch):
    rec = Recorder({"PUT Contacts": {"Contacts": [{"Name": "Dup", "HasValidationErrors": True, "ValidationErrors": [{"Message": "The contact name Dup is already assigned to another contact."}]}]}})
    result = await _call(monkeypatch, rec, "xero_contacts_create", {"Name": "Dup"})
    assert result["error"]["code"] == "invalid_argument"
    assert "already assigned" in result["error"]["message"]


async def test_contacts_get_rejects_non_uuid(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "xero_contacts_get", {"contactId": "../Invoices"})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


# ---- invoices / payments / accounts ------------------------------------------


async def test_invoices_list_filters(monkeypatch):
    rec = Recorder({"Invoices": {"Invoices": []}})
    await _call(monkeypatch, rec, "xero_invoices_list", {"Status": "AUTHORISED", "Type": "ACCREC"})
    params = rec.requests[0].url.params
    assert params["Statuses"] == "AUTHORISED"
    assert params["where"] == 'Type=="ACCREC"'
    assert params["summaryOnly"] == "true"
    assert params["order"] == "Date DESC"


async def test_invoice_get_unwraps(monkeypatch):
    rec = Recorder({f"Invoices/{INVOICE}": {"Invoices": [{"InvoiceID": INVOICE, "Date": "/Date(1780272000000+0000)/"}]}})
    result = await _call(monkeypatch, rec, "xero_invoices_get", {"invoiceId": INVOICE})
    assert result == {"InvoiceID": INVOICE, "Date": "2026-06-01T00:00:00Z"}


async def test_invoice_create_body(monkeypatch):
    rec = Recorder({"PUT Invoices": {"Invoices": [{"InvoiceID": INVOICE, "Status": "DRAFT"}]}})
    args = {
        "Type": "ACCREC",
        "ContactID": CONTACT,
        "LineItems": [{"Description": "Support", "Quantity": 2, "UnitAmount": 150, "AccountCode": "200"}],
        "DueDate": "2026-07-01",
    }
    await _call(monkeypatch, rec, "xero_invoices_create", args)
    assert body(rec.requests[0]) == {
        "Invoices": [
            {
                "Type": "ACCREC",
                "Contact": {"ContactID": CONTACT},
                "LineItems": [{"Description": "Support", "Quantity": 2.0, "UnitAmount": 150.0, "AccountCode": "200"}],
                "DueDate": "2026-07-01",
            }
        ]
    }
    assert "Idempotency-Key" in rec.requests[0].headers


async def test_invoice_update_status(monkeypatch):
    rec = Recorder({f"POST Invoices/{INVOICE}": {"Invoices": [{"InvoiceID": INVOICE, "Status": "VOIDED"}]}})
    result = await _call(monkeypatch, rec, "xero_invoices_update_status", {"invoiceId": INVOICE, "Status": "VOIDED"})
    assert body(rec.requests[0]) == {"Invoices": [{"InvoiceID": INVOICE, "Status": "VOIDED"}]}
    assert result["Status"] == "VOIDED"


async def test_payment_create_body(monkeypatch):
    rec = Recorder({"PUT Payments": {"Payments": [{"PaymentID": "p"}]}})
    args = {"InvoiceID": INVOICE, "AccountID": ACCOUNT, "Amount": 300, "Date": "2026-06-15", "Reference": "EFT"}
    await _call(monkeypatch, rec, "xero_payments_create", args)
    assert body(rec.requests[0]) == {
        "Payments": [{"Invoice": {"InvoiceID": INVOICE}, "Account": {"AccountID": ACCOUNT}, "Amount": 300.0, "Date": "2026-06-15", "Reference": "EFT"}]
    }


async def test_payments_list_status_filter(monkeypatch):
    rec = Recorder({"Payments": {"Payments": []}})
    await _call(monkeypatch, rec, "xero_payments_list", {"Status": "AUTHORISED"})
    assert rec.requests[0].url.params["where"] == 'Status=="AUTHORISED"'


async def test_accounts_list_filters(monkeypatch):
    rec = Recorder({"Accounts": {"Accounts": [{"Code": "090"}]}})
    result = await _call(monkeypatch, rec, "xero_accounts_list", {"Type": "BANK", "Class": "ASSET"})
    assert rec.requests[0].url.params["where"] == 'Type=="BANK" AND Class=="ASSET"'
    assert result == {"Accounts": [{"Code": "090"}], "count": 1}


async def test_accounts_type_cannot_inject_into_the_filter(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    rec = Recorder()
    with pytest.raises(ToolError):
        await _call(monkeypatch, rec, "xero_accounts_list", {"Type": 'BANK" OR 1==1'})
    assert rec.requests == []


# ---- reports ---------------------------------------------------------------

REPORT = {
    "Reports": [
        {
            "ReportName": "Profit and Loss",
            "ReportTitles": ["Profit & Loss", "Example Ltd", "1 June 2026 to 30 June 2026"],
            "ReportDate": "16 October 2026",
            "Rows": [
                {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "30 Jun 26"}]},
                {
                    "RowType": "Section",
                    "Title": "Income",
                    "Rows": [
                        {"RowType": "Row", "Cells": [{"Value": "Sales"}, {"Value": "1000.00"}]},
                        {"RowType": "SummaryRow", "Cells": [{"Value": "Total Income"}, {"Value": "1000.00"}]},
                    ],
                },
                {"RowType": "Section", "Title": "", "Rows": [{"RowType": "Row", "Cells": [{"Value": "Net Profit"}, {"Value": "400.00"}]}]},
            ],
        }
    ]
}


async def test_profit_and_loss_is_flattened(monkeypatch):
    rec = Recorder({"Reports/ProfitAndLoss": REPORT})
    result = await _call(monkeypatch, rec, "xero_reports_profit_and_loss", {"fromDate": "2026-06-01", "toDate": "2026-06-30"})
    assert rec.requests[0].url.params["fromDate"] == "2026-06-01"
    assert result["columns"] == ["30 Jun 26"]
    assert result["sections"][0] == {
        "title": "Income",
        "rows": [{"label": "Sales", "values": ["1000.00"]}],
        "total": {"label": "Total Income", "values": ["1000.00"]},
    }
    assert result["sections"][1] == {"rows": [{"label": "Net Profit", "values": ["400.00"]}]}


async def test_reversed_date_range_is_rejected(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "xero_reports_profit_and_loss", {"fromDate": "2026-07-01", "toDate": "2026-06-01"})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_aged_receivables_for_one_contact_uses_xero_report(monkeypatch):
    rec = Recorder({"Reports/AgedReceivablesByContact": REPORT})
    await _call(monkeypatch, rec, "xero_reports_aged_receivables", {"date": "2026-06-30", "contactId": CONTACT})
    params = rec.requests[0].url.params
    assert (params["contactId"], params["date"]) == (CONTACT, "2026-06-30")


async def test_aged_payables_summary_buckets_open_bills(monkeypatch):
    bills = {
        "Invoices": [
            {"Contact": {"ContactID": "s1", "Name": "Supplier A"}, "AmountDue": 100, "DueDate": "/Date(1782691200000+0000)/"},  # 2026-06-29: 1 day
            {"Contact": {"ContactID": "s1", "Name": "Supplier A"}, "AmountDue": 50, "DueDate": "2026-03-01T00:00:00"},  # 121 days
            {"Contact": {"ContactID": "s2", "Name": "Supplier B"}, "AmountDue": 20.5, "DueDate": "2026-07-15T00:00:00"},  # not due
        ]
    }
    rec = Recorder({"Invoices": bills})
    result = await _call(monkeypatch, rec, "xero_reports_aged_payables", {"date": "2026-06-30"})
    params = rec.requests[0].url.params
    assert params["Statuses"] == "AUTHORISED"
    assert params["where"] == 'Type=="ACCPAY" AND AmountDue>0 AND Date <= DateTime(2026,06,30)'
    assert result["totals"] == {"current": 20.5, "1_30": 100.0, "31_60": 0.0, "61_90": 0.0, "over_90": 50.0, "total": 170.5}
    first = result["contacts"][0]
    assert (first["name"], first["total"], first["invoices"], first["over_90"]) == ("Supplier A", 150.0, 2, 50.0)
    assert result["invoice_count"] == 3
    assert "truncated" not in result


# ---- Page-number paging that survives truncation (offset / next_offset) -----------------------------
# Self-test checklist: a cut page continues right after the last record returned (from page 1 and
# from a later page); offset aligned / not aligned to a page; offset 0; offset past the end; offset
# wins over page; page-only calls send the same request as before; no retry hint for a page size.
# Xero pages by number only, 100 records per page (PAGE_SIZE); the tools have no page-size parameter.

TOTAL = 337
ROWS = [{"id": i, "name": f"Row {i}", **{f"f{k}": f"value {k}" for k in range(40)}} for i in range(TOTAL)]
SMALL = [{"id": i} for i in range(TOTAL)]

PAGED_TOOLS = [
    ("xero_contacts_list", "Contacts"),
    ("xero_invoices_list", "Invoices"),
    ("xero_payments_list", "Payments"),
]


def _paged_api(key, rows):
    def reply(request):
        number = int(request.url.params["page"])
        return httpx.Response(200, json={key: rows[(number - 1) * 100 : number * 100]})

    return Recorder({key: reply})


def _sent_page(rec, i=0):
    return int(rec.requests[i].url.params["page"])


async def _walk(monkeypatch, rec, tool, key, args):
    ids, calls, cut = [], 0, 0
    while True:
        result = await _call(monkeypatch, rec, tool, args)
        calls += 1
        assert "retry_page_size" not in result, result.get("note")
        cut += bool(result.get("truncated"))
        ids += [r["id"] for r in result[key]]
        if result.get("next_offset") is None:
            return ids, calls, cut
        args = {k: v for k, v in args.items() if k != "page"} | {"offset": result["next_offset"]}
        assert calls < 200


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
async def test_cut_pages_continue_without_gaps_from_page_1(monkeypatch, tool, key):
    ids, _, cut = await _walk(monkeypatch, _paged_api(key, ROWS), tool, key, {})
    assert cut > 0  # pages really were cut
    assert ids == list(range(TOTAL))  # no gaps, no duplicates


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
async def test_cut_pages_continue_without_gaps_from_a_later_page(monkeypatch, tool, key):
    rec = _paged_api(key, ROWS)
    ids, _, cut = await _walk(monkeypatch, rec, tool, key, {"page": 3})
    assert cut > 0 and _sent_page(rec) == 3
    assert ids == list(range(200, TOTAL))


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
@pytest.mark.parametrize("offset,page,next_offset", [(130, 2, 200), (200, 3, 300), (0, 1, 100), (330, 4, None)])
async def test_offset_reads_the_page_that_holds_it(monkeypatch, tool, key, offset, page, next_offset):
    rec = _paged_api(key, SMALL)
    result = await _call(monkeypatch, rec, tool, {"offset": offset})
    assert _sent_page(rec) == page and "offset" not in rec.requests[0].url.params
    assert result[key][0]["id"] == offset and result["offset"] == offset
    assert result.get("next_offset") == next_offset  # rest of the page returned, next page starts after it


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
async def test_offset_past_the_end_is_empty(monkeypatch, tool, key):
    result = await _call(monkeypatch, _paged_api(key, SMALL), tool, {"offset": 1000})
    assert (result["count"], result["has_more"], result.get("next_offset")) == (0, False, None)


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
async def test_offset_wins_over_page(monkeypatch, tool, key):
    rec = _paged_api(key, SMALL)
    result = await _call(monkeypatch, rec, tool, {"offset": 150, "page": 5})
    assert _sent_page(rec) == 2 and result[key][0]["id"] == 150


@pytest.mark.parametrize("tool,key", PAGED_TOOLS)
@pytest.mark.parametrize("args,page", [({"page": 2}, 2), ({}, 1)])
async def test_page_only_sends_the_same_request_as_before(monkeypatch, tool, key, args, page):
    rec = _paged_api(key, SMALL)
    result = await _call(monkeypatch, rec, tool, args)
    assert _sent_page(rec) == page and "offset" not in rec.requests[0].url.params
    assert [r["id"] for r in result[key]] == list(range((page - 1) * 100, page * 100))
    assert (result["page"], result["next_page"], result["offset"], result["next_offset"], result["has_more"]) == (
        page,
        page + 1,
        (page - 1) * 100,
        page * 100,
        True,
    )


async def test_contacts_search_paging_is_unchanged(monkeypatch):
    rec = Recorder({"Contacts": {"Contacts": [{"ContactID": str(i)} for i in range(100)]}})
    result = await _call(monkeypatch, rec, "xero_contacts_search", {"term": "acme"})
    assert "offset" not in result and "next_offset" not in result and result["next_page"] == 2
