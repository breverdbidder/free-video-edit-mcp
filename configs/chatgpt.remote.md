# ChatGPT remote MCP connector

ChatGPT does **not** use local stdio MCP. It expects a **remote MCP server** reachable over the public internet, typically via **HTTPS** at a streamable-HTTP endpoint.

## 1. Start the local HTTP server

From a clone of this repo (with the package installed and FFmpeg on `PATH`):

```bash
python -m free_video_edit_mcp --transport streamable-http --host 127.0.0.1 --port 8765
# alias:
python -m free_video_edit_mcp --transport http --host 127.0.0.1 --port 8765
```

Default MCP path: `http://127.0.0.1:8765/mcp`

## 2. Expose it with a public HTTPS URL

ChatGPT connectors generally require a **public HTTPS** URL. Localhost alone will not work. Examples:

### Cloudflare Tunnel (recommended)

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

Note the printed `https://…trycloudflare.com` URL.

### ngrok

```bash
ngrok http 8765
```

Note the `https://…ngrok-free.app` (or similar) URL.

## 3. Add the connector in ChatGPT

1. Open ChatGPT → **Settings** → **Connectors** / **Advanced** → **MCP** (wording varies by plan and rollout).
2. Add a new remote MCP server / connector.
3. Use the public base URL plus the MCP path, for example:

```text
https://YOUR-TUNNEL-HOST/mcp
```

4. Save and enable the connector for a chat that supports tools.

## Example URL

```text
https://abc123.trycloudflare.com/mcp
```

## Caveats

- **Public HTTPS is required** for ChatGPT in typical setups; plain `http://127.0.0.1` is not enough.
- Exposing this server publicly lets remote clients call tools that run **FFmpeg on your machine** (including `run_ffmpeg`). Prefer a private tunnel, auth gateway, or self-hosted reverse proxy with authentication. Do not expose an open tunnel on untrusted networks without access controls.
- Tooling that reads/writes media uses `FREE_VIDEO_EDIT_ROOT` (default `~/video-projects`). Point it at a dedicated folder.
- ChatGPT MCP availability depends on your OpenAI plan and product surface; if you do not see a remote MCP / connector UI, use Cursor or Claude Desktop with stdio instead.
- Prefer `--transport streamable-http` (alias `--transport http`). Legacy `--transport sse` exists but is not the ChatGPT-preferred path.
