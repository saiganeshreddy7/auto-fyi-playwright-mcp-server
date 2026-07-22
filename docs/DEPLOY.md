# Deploy to Render

Host this MCP server on Render so the whole team connects to one URL. The AutoFYI backend
(Playwright + FYI browser session) stays on the local machine behind the Cloudflare tunnel.

```text
Team Claude clients ──HTTPS──> Render (this MCP) ──HTTPS──> Cloudflare tunnel ──> local backend ──> FYI
```

## 1. Push to GitHub

Create an empty private repository, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/autofyi-mcp.git
git push -u origin main
```

## 2. Create the Render service

1. Render Dashboard → **New → Blueprint** → select the repository.
2. `render.yaml` provisions everything: a single Starter web service, transport
   `streamable-http`, a generated first bearer token in `AUTOFYI_MCP_AUTH_TOKENS`, health
   checks on `/healthz`, and `AUTOFYI_ENABLE_WRITES=false`.
3. Leave the two `AUTOFYI_CF_ACCESS_*` prompts blank unless Cloudflare Access is set up.
4. Deploy and wait for the service to go live.

## 3. Set up team tokens

The endpoint is `https://<service-name>.onrender.com/mcp` and every request must send
`Authorization: Bearer <token>`.

Open the service → **Environment** tab → `AUTOFYI_MCP_AUTH_TOKENS`. It starts with one
generated token; make it one token per teammate (comma-separated), generated with
`openssl rand -hex 24`. Give each person the URL plus only their own token — revoking one
person is then just deleting their token.

## 4. Connect each teammate

Full per-client instructions (Claude Desktop, Claude Code, Cursor, claude.ai web, ChatGPT)
are in [HOST.md](HOST.md). The short version for Claude Code:

```bash
claude mcp add --transport http --scope user autofyi https://<service-name>.onrender.com/mcp --header "Authorization: Bearer YOUR_TOKEN_HERE"
```

Quick verify inside Claude: run the `autofyi_health` tool. `browser: open` means the local
FYI session is logged in and ready.

## Local machine requirements

The Render service is only a front door. Every tool call still depends on the local machine:

- The machine stays awake, with the AutoFYI backend and `cloudflared` running.
- The FYI browser session is logged in. `/health` reporting `"browser":"closed"` means it
  needs to be logged in again on the local machine.

## Operating rules

- Exactly one instance, never autoscale: confirmations are in-memory and the backend browser
  queue is serialized.
- A redeploy or restart clears pending prepared actions. Prepare them again; this is by design.
- Keep `AUTOFYI_ENABLE_WRITES=false` until the URL has been shared with the team only. To
  enable writes later, flip it in the Environment tab; the service restarts automatically.

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| Connector fails to add | Wrong URL or token | Re-check the URL and your bearer token |
| 401 unauthorized | Token missing, mistyped, or revoked | Compare with `AUTOFYI_MCP_AUTH_TOKENS` in Render |
| `autofyi_health` shows browser closed | FYI session logged out | Log in on the local machine |
| Tools time out | Tunnel or backend down on the local machine | Restart backend + `cloudflared` |
| Everything broke after redeploy | Pending confirmations cleared | Re-prepare the action |
