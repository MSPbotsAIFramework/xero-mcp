# PRD-21293 · Code review

Status: frozen @ 3c2f1ed

## Round 1 · 2026-10-30 · reviewed dev/PRD-21293 @ 3c2f1ed (base main @ eb0ee03)

Verdict: approved

### Scope

Single MCP repo (`MSPbotsAIFramework/xero-mcp`), purely additive: new `src/xero_mcp/tools/bank.py` module (6 tools: `xero_bank_transactions_{list,get,create}`, `xero_bank_transfers_{list,get,create}`), wired into `navigation.py` (`"bank"` domain) and `server.py` (registration tuple + `INSTRUCTIONS`), plus matching tests, `README.md`, `docs/tickets/PRD-21293.{md,ac.json}`. No existing tool's name, parameters, or behavior changed.

### Findings

No must-fix findings.

1. [suggestion] `src/xero_mcp/tools/bank.py:171-182` (`xero_bank_transfers_create`) — does not reject `FromBankAccountID == ToBankAccountID` before the Xero call. Not a correctness gap: Xero's `BankTransfers` endpoint rejects a same-account transfer and the response is a `ValidationErrors` entry, which `write_result` (`_common.py:134-141`) already turns into a structured `invalid_argument`-shaped error via `XeroError` — same path every other write tool in this repo relies on for server-side validation (e.g. `xero_payments_create` doesn't pre-check `Amount` against the invoice balance either). Consistent with the repo's existing pattern of leaning on Xero's own validation rather than duplicating it client-side; nothing to fix.
2. [suggestion] `tests/test_api_client.py` new tests don't include a case for `FromBankAccountID == ToBankAccountID` reaching Xero and surfacing as `ValidationErrors` (the general `test_bank_transaction_validation_error_is_reported` only covers `BankTransactions`, not `BankTransfers`). Would tighten confidence around finding 1 above but isn't required — the general validation-error-surfacing path is already covered once for the shared `write_result` helper both resources go through.

### Verification performed (beyond reading the diff)

- `uv sync && uv run pytest -q` on `dev/PRD-21293 @ 3c2f1ed`: **228 passed, 0 failed** — matches the Self-test section in `docs/tickets/PRD-21293.md` exactly (`contacts`/`invoices`/`payments`/`accounts`/`reports`/`middleware`/`contract_types` suites unchanged, 9 new bank tests + 18 new generic-table parametrizations).
- Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`): no hits outside `docs/tickets/PRD-21293.md` itself (quoted wiki text and `[已假设]`/`[不阻塞]` design-leg tags) — `src/`, `tests/`, `README.md` clean.
- README `## MSPbots Connector` section (lines 16-87) diffed byte-for-byte against `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md`: identical (the only `diff` output was a trailing-blank-line artifact of my own line-range extraction, not a real content difference).
- `tests/contract_types.json`: confirmed it has zero references to any `xero_bank_*` tool, matching the design's note that this file only needs updating once the new tools' shape is stable and is not required for this change (it only iterates tools already present in it).
- Local smoke test (`uv run python -m xero_mcp`, port 8080): `GET /health` → `200`; `POST /mcp` with no credential headers → `401`; `POST /mcp initialize` with placeholder `X-Xero-Access-Token`/`X-Xero-Tenant-Id` → `200` — matches the Self-test claims.
- Read `server.py:31-138` (`GatewayCredentialsMiddleware`, `_credentials_var` contextvar, `create_mcp_server`) directly: confirmed the new `bank` module is wired through the exact same `client_factory()`/`get_client_from_context()` path every existing tool uses, with no new module-level or shared state — backs AC10 (multi-tenant isolation) and the "no new credential surface" non-functional claim.
- Read `api_client.py` and `_common.py` logging call sites: none log request bodies, tokens, or tenant ids — backs the "nothing is logged" security claim.
- Confirmed `bank.py`'s shape is a direct structural mirror of `payments.py` (same `READ_ONLY`/`WRITES` annotations, same `run_tool`/`page_result`/`first`/`write_result`/`guid`/`literal`/`compact` helpers, `invoices.LineItem` reused rather than redefined) — no duplicated logic, no new dependency.

### Code quality checklist (`SKILL.md` global constraints, §7a adjustment for MCP repos)

- Reuse: all shared helpers and the existing `LineItem` model reused; no new HTTP/retry/pagination logic written. Pass.
- Scope: touches exactly the files the tech design named; the five pre-existing tool modules are untouched except the two additive wiring points (`navigation.py`, `server.py`). Pass.
- Credentials: Header-only, no persistence, no logging, no new shared state (checked above). Pass.
- Structured errors: all three error paths (`XeroError`, `ValueError`, generic `Exception`) go through the same `run_tool` envelope every existing tool uses; no raw exception can escape. Pass.
- Tests follow changes: 11 new resource-specific tests + the two generic snapshot tables (`EXPECTED_TOOLS`, `WRITE_TOOLS`) updated; Self-test section has evidence per test case, not just "pass". Pass.
- README Connector section: byte-identical to the reference (checked above). Pass.
- No Chinese in the repo (checked above). Pass.
- No cross-repo coupling: `Release order: n/a` is correct — this is a single, purely additive MCP repo change with no platform-side dependency to sequence against (§1.2 of `mcp-track.md` doesn't apply, there's only one repo in play).
- `gitleaks detect`: not installed in this review environment either; same as design/dev's self-test, not a blocker (tool-availability gap, not a secret found).

### Fixes

N/A — approved round 1, no must-fix findings to address.
