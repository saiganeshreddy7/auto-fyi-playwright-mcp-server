# AutoFYI MCP

A standalone, safety-first MCP server over the existing AutoFYI REST API at
`https://autofyi.kellyautomations.com`.

It does not import, modify, or replace the current AutoFYI backend or frontend. Claude runs
this project locally over stdio; this project calls the deployed API over HTTPS.

## Safety contract

- Financial writes are disabled by default.
- Search scores rank candidates only. Claude compares names, proposes a likely non-exact
  match, and asks the user to confirm its stable client ID.
- `Ltd`, `Limited`, punctuation, case, and extra spaces are normalized for comparison.
- Every allocation request contains exactly one complete service/job type. It never mixes
  monthly, weekly, VAT, annual, or subscription work.
- Live months are classified before writes; partially allocated months are never re-split.
- Every write is a two-stage operation: `prepare_*` then `execute_confirmed_action`.
- Preparing reads current FYI jobs/interims, validates the exact payload, and returns a
  single-use confirmation ID plus the exact phrase the user must provide.
- The executor accepts only the stored payload; it cannot be changed during execution.
- Confirmations expire after 10 minutes by default and cannot be replayed.
- Draft invoices are the default. Approval uses a stronger confirmation phrase.
- Write failures and timeouts are never automatically retried.

Prepared `allocations.lines` are the operational source of truth for service NET amounts,
matching the existing frontend. Raw `xero_invoices` are supporting information and a source
for warnings; the server does not blindly overwrite prepared allocations from raw Xero data.

## Install

```bash
cd /Users/saiganeshreddykodekandla/Documents/automatefyi/AutoFYI-MCP
cp .env.example .env
uv sync --extra dev
uv run autofyi-mcp-smoke
uv run pytest
```

The smoke command reads only `/health` and `/openapi.json`. It never calls a write endpoint.

Start the MCP manually:

```bash
uv run autofyi-mcp
```

An stdio MCP normally appears to wait silently. That is correct: it is waiting for an MCP
client and writes protocol data only to stdout. Logs go to stderr.

## Connect Claude Code

```bash
claude mcp add --transport stdio --scope user \
  --env AUTOFYI_API_BASE=https://autofyi.kellyautomations.com \
  --env AUTOFYI_ENABLE_WRITES=false \
  autofyi -- /opt/homebrew/bin/uv --directory \
  /Users/saiganeshreddykodekandla/Documents/automatefyi/AutoFYI-MCP \
  run autofyi-mcp
```

Verify with:

```bash
claude mcp get autofyi
claude mcp list
```

Inside Claude Code, run `/mcp` and ask:

```text
Check AutoFYI health, then search for client Example Limited.
Do not make any financial changes.
```

## Connect Claude Desktop

Add this entry to the Claude Desktop MCP configuration and restart Claude Desktop:

```json
{
  "mcpServers": {
    "autofyi": {
      "type": "stdio",
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/Users/saiganeshreddykodekandla/Documents/automatefyi/AutoFYI-MCP",
        "run",
        "autofyi-mcp"
      ],
      "env": {
        "AUTOFYI_API_BASE": "https://autofyi.kellyautomations.com",
        "AUTOFYI_ENABLE_WRITES": "false"
      }
    }
  }
}
```

Use the full `uv` path because desktop applications may not inherit the terminal `PATH`.

## Host on Render (shared team server)

The MCP server can run on Render over Streamable HTTP so the whole team connects to one URL.
The AutoFYI backend (Playwright + FYI browser session) stays on the local machine behind the
Cloudflare tunnel; Render only hosts this MCP layer in front of it.

```text
Team Claude clients ──HTTPS──> Render (this MCP) ──HTTPS──> Cloudflare tunnel ──> local backend ──> FYI
```

Deploy with the [render.yaml](render.yaml) blueprint; full steps, team connection
instructions, operating rules, and troubleshooting are in
[docs/DEPLOY.md](docs/DEPLOY.md). The team URL is
`https://<service>.onrender.com/mcp/<AUTOFYI_MCP_PATH_SECRET>` — the full URL is the
credential, so share it privately and rotate the secret in Render to revoke.

## Enabling financial writes

Do this only after read-only testing and access protection are complete:

```env
AUTOFYI_ENABLE_WRITES=true
```

Restart the MCP process after changing the environment. A confirmation prepared before the
restart is intentionally lost and must be prepared again from fresh FYI information.

If Cloudflare Access is added later, the server supports service-token headers:

```env
AUTOFYI_CF_ACCESS_CLIENT_ID=...
AUTOFYI_CF_ACCESS_CLIENT_SECRET=...
```

It also supports an optional backend bearer token through `AUTOFYI_API_TOKEN`.

## Documentation

- [Deploy to Render](docs/DEPLOY.md)
- [Tool reference](docs/TOOLS.md)
- [Reference user flows](docs/USER_FLOWS.md)
- [Edge and corner cases](docs/EDGE_CASES.md)
- [Architecture and reliability](docs/ARCHITECTURE.md)

The examples are illustrative only. The MCP always re-reads the selected client's actual
information, prepared allocations, FYI jobs, and interim rows instead of depending on an
example name, amount, tag, month, or job convention.

## Client catalog analytics

The AutoFYI backend can publish a validated FYI CSV snapshot as an additive SQLite read model.
This does not replace or modify the existing client JSON files, Xero invoices, allocations,
frontend state, or browser automation.

- `get_client_catalog_status` reports the active snapshot.
- `describe_client_catalog` lists safe columns and their distinct counts. Ask for one column to
  receive at most its 10 most common values; `values_truncated=true` is not exhaustive.
- `query_client_catalog` supports structured `count`, `list`, `distinct`, `group_count`, and
  numeric `summary` operations. It never accepts raw SQL.
- Catalog answers describe the imported FYI snapshot. They do not prove that a live FYI job or
  an unallocated interim exists.

CSV import belongs entirely to the AutoFYI backend. Open its Swagger UI, use
`POST /catalog/import`, choose the complete FYI CSV, and click Execute. Each successful upload
validates the file and atomically replaces the backend SQLite catalog. MCP never reads or uploads
local CSV files; it only calls the hosted status, schema, and query endpoints.
