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
   `streamable-http`, a generated `AUTOFYI_MCP_PATH_SECRET`, health checks on `/healthz`,
   and `AUTOFYI_ENABLE_WRITES=false`.
3. Leave the two `AUTOFYI_CF_ACCESS_*` prompts blank unless Cloudflare Access is set up.
4. Deploy and wait for the service to go live.

## 3. Get the team URL

Open the service → **Environment** tab → copy `AUTOFYI_MCP_PATH_SECRET`. The team URL is:

```text
https://<service-name>.onrender.com/mcp/<AUTOFYI_MCP_PATH_SECRET>
```

The full URL is the credential. Share it privately. To revoke access, change the secret in
the Environment tab and share the new URL.

## 4. Connect each teammate

- **claude.ai / Claude Desktop**: Settings → Connectors → Add custom connector → paste the URL.
- **Claude Code**:

```bash
claude mcp add --transport http --scope user autofyi https://<service-name>.onrender.com/mcp/<secret>
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
| Connector fails to add | Wrong URL or secret | Re-copy the secret from Render Environment |
| 404 from the MCP URL | Secret segment wrong | Use `/mcp/<exact secret>` |
| `autofyi_health` shows browser closed | FYI session logged out | Log in on the local machine |
| Tools time out | Tunnel or backend down on the local machine | Restart backend + `cloudflared` |
| Everything broke after redeploy | Pending confirmations cleared | Re-prepare the action |
