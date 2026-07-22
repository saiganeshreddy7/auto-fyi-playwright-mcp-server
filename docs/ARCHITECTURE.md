# Architecture and reliability

```text
Claude/Codex
    │ local stdio MCP
    ▼
AutoFYI-MCP
    ├── client resolver and summaries
    ├── Xero/allocation business validation
    ├── live monthly state reconciliation
    ├── one-service allocation scope enforcement
    ├── live FYI job/interim verification
    └── expiring immutable confirmation store
            │ HTTPS
            ▼
https://autofyi.kellyautomations.com
            │ existing queued browser automation
            ▼
FYI
```

The existing frontend continues to call the same backend independently.

Portfolio analytics use a separate path:

```text
FYI CSV ──Swagger POST /catalog/import──> AutoFYI backend SQLite catalog
                                                   │
Claude/Codex <──safe structured query── AutoFYI-MCP┘
```

The catalog is a versioned read model. It is never joined into existing frontend/browser routes,
MCP callers cannot submit SQL, and MCP does not handle CSV files.

## Why there is no generic endpoint tool

A generic method/path/body tool would let an AI bypass client resolution, business validation,
MCP destructive annotations and confirmation controls. Only curated read, prepare and execute
operations are registered.

## Why confirmations are in memory

Restarting the MCP invalidates every pending financial action and forces a fresh FYI read. This
is safer than executing a stale persisted payload. Completed backend actions remain visible only
for the current MCP process; durable auditing belongs in protected backend logs.

## Remaining backend limitations

- Backend writes do not expose an idempotency key. Therefore a timeout cannot safely be retried.
- FYI automation is browser-driven and intentionally queued. Horizontal writers may conflict.
- The public deployment contract does not advertise authentication. Keep MCP writes disabled
  until access controls are verified.
- MCP-side serialization protects only this process. The frontend and another MCP process can
  still submit concurrent work, so backend-level locking remains the final authority.

## Streamable HTTP hosting

`AUTOFYI_MCP_TRANSPORT=streamable-http` serves the same server remotely (see `render.yaml`).
Access control is a mandatory secret URL path: the endpoint is `/mcp/<AUTOFYI_MCP_PATH_SECRET>`
and the server refuses to start HTTP without the secret. A path secret is used instead of a
header so claude.ai custom connectors, Claude Desktop, and Claude Code can all connect with a
plain URL. TLS is terminated by the host (Render). `/healthz` is unauthenticated and reveals
nothing.

The confirmation store remains in-process, so the hosted deployment must stay at exactly one
instance; a restart still safely invalidates pending confirmations. All team members share that
one process and the single backend browser queue behind the tunnel.
