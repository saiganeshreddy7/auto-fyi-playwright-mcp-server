# Connect to the hosted AutoFYI MCP

The team server runs at:

```text
https://<service-name>.onrender.com/mcp
```

You need two things from the admin:

1. The server URL above.
2. Your personal bearer token. Do not share it, post it in group chats, or commit it anywhere.

Requests must send `Authorization: Bearer <your-token>`. Clients that cannot send headers
(claude.ai web, ChatGPT) use the secret-path URL instead — see the last section.

## Claude Desktop

Add this to the Claude Desktop MCP configuration (Settings → Developer → Edit Config),
then restart Claude Desktop. Requires Node.js for `npx`.

```json
{
  "mcpServers": {
    "autofyi": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<service-name>.onrender.com/mcp",
        "--header",
        "Authorization: Bearer YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

## Claude Code

```bash
claude mcp add --transport http --scope user autofyi https://<service-name>.onrender.com/mcp --header "Authorization: Bearer YOUR_TOKEN_HERE"
```

Verify with `claude mcp list`, then inside Claude Code run `/mcp`.

## Cursor / Windsurf / other MCP clients

Any client that supports stdio MCP servers can use the same `mcp-remote` bridge as Claude
Desktop. In the client's `mcp.json` (or equivalent):

```json
{
  "mcpServers": {
    "autofyi": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<service-name>.onrender.com/mcp",
        "--header",
        "Authorization: Bearer YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

Clients that support remote HTTP servers with custom headers can skip `mcp-remote` and point
straight at the URL with the `Authorization` header.

## claude.ai web and ChatGPT (no-header clients)

Web connector UIs cannot attach an `Authorization` header. For these, the admin sets
`AUTOFYI_MCP_PATH_SECRET` in Render, which moves the endpoint to:

```text
https://<service-name>.onrender.com/mcp/<secret>
```

- **claude.ai web**: Settings → Connectors → Add custom connector → paste that URL.
- **ChatGPT**: enable Developer mode (Settings → Apps & Connectors → Advanced), then add the
  URL as a custom connector with authentication set to none.

Note: when bearer tokens are configured, they are required on every request — including on
the secret path. To support no-header clients, the admin must run the server with the path
secret as the only auth (leave `AUTOFYI_MCP_AUTH_TOKENS` empty). Choose one mode per
deployment.

## Verify it works

Ask Claude (or the connected client) to run the `autofyi_health` tool:

- `browser: open` — ready to use.
- `browser: closed` — the FYI session on the office machine is logged out; tell the admin.
- Connection or 401 errors — your URL or token is wrong, or your token was revoked.

## Admin: managing tokens

Tokens live in the Render service → Environment tab → `AUTOFYI_MCP_AUTH_TOKENS`,
comma-separated, one per teammate:

```text
alice-8f3k...,bob-2mq9...,charlie-x7w1...
```

- Add a teammate: append a new token (generate with `openssl rand -hex 24`).
- Revoke a teammate: delete their token. Render restarts the service automatically.
- A restart clears pending prepared actions; they must be prepared again. This is by design.
