# Direct mode

Direct mode serves one machine without a Vicoa cloud account: a local FastAPI
daemon, SQLite storage, a persistent token, and browser access over Tailscale.

## Start

```bash
cd backend
source .venv/bin/activate

vicoa direct \
  --port 8765 \
  --public-url https://<machine>.<tailnet>.ts.net
```

`vicoa daemon --direct` and `python -m vicoa.cli direct` are equivalent.

Startup prints:

```text
[direct] listening on http://127.0.0.1:8765
[direct] token: stored at /Users/<you>/.vicoa/direct/token (use --show-token to print)
[direct] public origin: https://<machine>.<tailnet>.ts.net
[direct] store: /Users/<you>/.vicoa/direct/store.db
[direct] cloud: disabled
```

`--show-token` additionally prints the token and pair link. Without it, the
token is never written to stdout or the daemon log; use
`backend/scripts/direct-status.sh --token` to display the pair link.

## Token

The first start creates `~/.vicoa/direct/token` (64 hex characters, mode 0600).
Later starts reuse it. The daemon runs with umask 077: the data directory is
0700 and the SQLite store, logs, and attachments are 0600/0700.

```text
--token <value>        use an explicit token; never read or write the file
--token-path <path>    use a custom token file
--rotate-token         generate a new token and overwrite the file
--show-token           print the stored token and pairing link on startup
```

The token authenticates REST (`Authorization: Bearer <token>`), WebSocket
subprotocols (`vicoa-local.<token>` for the browser, `vicoa-key.<token>` for
the headless runner), the runner's `--api-key`, and media URL signatures.
Never commit it or post it publicly.

## Tailscale

The daemon binds loopback only. Tailscale Serve terminates TLS and forwards to
`127.0.0.1:8765`.

```bash
tailscale up
tailscale status

# The machine's tailnet DNS name, without the trailing dot:
tailscale status --json | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"
```

Use that name as `--public-url`:

```bash
vicoa direct --port 8765 --public-url https://machine.tailnet.ts.net
```

In a second terminal:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status
```

Do **not** use `tailscale funnel`; that exposes the service to the public
internet.

With `--with-web`, expose only the daemon port (8765). The daemon proxies page
and static requests to its supervised Next server, so the browser still sees a
single origin.

## Access

```bash
curl https://machine.tailnet.ts.net/healthz

TOKEN=$(cat ~/.vicoa/direct/token)
curl -H "Authorization: Bearer $TOKEN" \
  https://machine.tailnet.ts.net/api/v1/machines
```

Requests without the token must return 401. Browser requests need an `Origin`
matching `--public-url`; CORS and the `vicoa-local.` WebSocket both use that
allow-list. The runner's `vicoa-key.` socket remains loopback-only.

## Verify a session

From the machine:

```bash
cd backend
TOKEN=$(cat ~/.vicoa/direct/token)
.venv/bin/python scripts/direct_chat.py \
  --ws-url ws://127.0.0.1:8765/ws \
  --nonce "$TOKEN" \
  --cwd /Users/<you>/project \
  --prompt "Reply with exactly: pong"
```

Remote:

```bash
.venv/bin/python scripts/direct_chat.py \
  --ws-url wss://machine.tailnet.ts.net/ws \
  --nonce "$TOKEN" \
  --cwd /Users/<you>/project \
  --prompt "Reply with exactly: pong"
```

Expected shape:

```text
[system] session <uuid> spawned
[you] Reply with exactly: pong
[queue] <id> -> consumed
[agent] pong
[system] session <uuid> ended
```

## Browser development

Terminal 1:

```bash
vicoa direct --port 8765 --origin http://localhost:3000
```

Terminal 2:

```bash
cd apps/web
NEXT_PUBLIC_VICOA_DIRECT=1 \
NEXT_PUBLIC_DIRECT_API_BASE=http://127.0.0.1:8765 \
NEXT_PUBLIC_DIRECT_WS_URL=ws://127.0.0.1:8765/ws \
corepack pnpm dev
```

Open `http://localhost:3000/#token=<token>` once; the page stores the token in
`localStorage` and removes the fragment. Later open
`http://localhost:3000/dashboard`.

## Browser sessions

`/dashboard` opens the New Session page. Type a prompt and press Enter: the
browser calls `spawn-session` over the local WebSocket, navigates to the new
session, and streams the agent's replies. Additional messages use the same
composer, and reloading the page restores the full history from SQLite.

Direct mode hides cloud-only navigation (Tasks, Automations, Agents, Skills,
Search, Kanban, onboarding/setup checklist) and account entries for billing,
invitations, settings, API keys, logout, and issue reporting. Cloud-only routes
such as `/dashboard/tasks`, `/dashboard/settings`, `/dashboard/agents`,
`/dashboard/kanban`, `/dashboard/shared/*`, and `/dashboard/join/*` redirect to
`/dashboard/sessions/new`. The task picker is hidden on New Session.

## Same-origin production

Build the web app with direct mode enabled:

```bash
cd apps/web
NEXT_PUBLIC_VICOA_DIRECT=1 corepack pnpm build
```

Then let the daemon serve one origin and supervise Next:

```bash
vicoa direct \
  --port 8765 \
  --with-web \
  --web-dir /path/to/repo/apps/web \
  --web-port 3000
```

- `/api/v1/*` and `/ws` are served by the daemon.
- Every other HTTP request is proxied to Next.
- The daemon prefers `.next/standalone/server.js`; a normal
  `next start` build is the fallback.
- Next is restarted after a crash or when it stops responding, with backoff.
- Logs: `~/.vicoa/direct/logs/next.log`.
- On shutdown the daemon also stops its supervised Next process and every
  headless runner it spawned.

To proxy to a Next server managed elsewhere, use
`--web-upstream http://127.0.0.1:3000` without `--with-web`.

## Start/stop scripts

Helper scripts live in `backend/scripts/` and all source `direct-env.sh`:

```bash
cd backend/scripts

./direct-build.sh          # NEXT_PUBLIC_VICOA_DIRECT=1 pnpm build
./direct-up.sh             # start daemon + Next in the background
./direct-status.sh --token # status, health, Serve, pair link
./direct-serve.sh on       # tailscale serve --https=443 -> daemon
./direct-serve.sh off      # tailscale serve reset (all Serve routes)
./direct-down.sh           # stop the script-managed daemon
```

Defaults:

```text
daemon:  127.0.0.1:8765
web:     127.0.0.1:3000
store:   ~/.vicoa/direct/store.db
token:   ~/.vicoa/direct/token
logs:    ~/.vicoa/direct/logs/{daemon,next}.log
pid:     ~/.vicoa/direct/daemon.pid
public:  auto-detected from `tailscale status --json`
```

Override any value from the environment, for example the temporary instance
used during validation:

```bash
VICOA_DIRECT_PORT=46040 \
VICOA_DIRECT_WEB_PORT=3002 \
VICOA_DIRECT_STORE=/tmp/vicoa-ts/store.db \
VICOA_DIRECT_TOKEN_PATH=/tmp/vicoa-ts/token \
./direct-up.sh
```

Notes:

- `direct-up.sh` only starts the daemon. Run `direct-serve.sh on` once to point
  Tailscale Serve at the port.
- The first `tailscale serve` may require enabling Serve for the tailnet; follow
  the URL it prints.
- `direct-down.sh` only stops a daemon started by `direct-up.sh` (PID file).
  A manually started daemon must be stopped by its PID.
- The daemon stops its supervised Next server and every runner it spawned.
- `direct-serve.sh off` removes every Serve route on this node, not just direct
  mode.
- To keep the existing phone pairing while moving from `/tmp` to the default
  paths, stop the daemon first, copy the store/token to `~/.vicoa/direct`, then
  start with the defaults.

## Image attachments

Direct mode supports images in chat:

- `POST /api/v1/attachments` (Bearer auth, multipart `agent_instance_id` +
  `file`), 25 MB limit.
- Accepted by magic bytes: JPEG, PNG, GIF, WebP. HEIC and other formats are
  rejected.
- Bytes are stored under `~/.vicoa/direct/attachments/<instance-id>/<id>.<ext>`.
- `GET /api/v1/attachments/{id}` accepts either the Bearer token (runner) or
  `?token=<direct-token>` (an `<img>` tag cannot send headers).
- Sending a message with `attachment_ids` stamps
  `message_metadata.attachments`; the runner downloads the image with its
  Bearer token and gives the agent a local image path.

In a direct web build, the composer uploads straight to the daemon and resolves
chat images to the daemon URL with the token query. The cloud path
(`/api/attachments` through Next) is unchanged for normal web/desktop builds.

## Permissions and queued messages

With permission mode **Default**, a tool that needs approval renders an
`Allow execution of …?` message with `Allow once` / `Always allow` / `Deny`
buttons. Clicking one sends that label as the user's reply and the runner
continues or aborts the tool.

Messages sent while a turn is running are stamped
`message_metadata.queue.status = "queued"` and appear above the composer with
**Steer** (deliver into the running turn), **Retrieve** (pull the text back into
the composer), and **Remove** (cancel). When the runner picks a message up it
marks it `consumed`; the row disappears from the queue bar.

Session-scoped WebSocket connections only receive updates for their own
session, so two concurrent sessions cannot see each other's messages or
control commands.

## PWA and phone install

The direct web build is an installable PWA. The manifest uses
`display: standalone`, `scope: /`, and `viewport-fit=cover`; the dashboard shell
pads the sidebar, top bar, and composer around the notch and home indicator.

Pairing:

1. On the computer, run `backend/scripts/direct-status.sh --token` and copy the
   pair link.
2. On the phone, open the Tailscale host first (Safari on iOS, Chrome on
   Android), then use the pair link.
3. The browser stores the token, `apiBase`, and `wsUrl` in `localStorage` and
   opens New Session.
4. Install from the browser menu (iOS: Share → Add to Home Screen; Android:
   Install app / Add to Home Screen).

If the installed app has no token (iOS may isolate storage from Safari), open
the installed app once and enter the server URL and token on `/direct-pair`.
The dashboard account menu has a **Connection** entry that returns there.
Opening `/dashboard*` without a token also redirects to `/direct-pair`.

The direct build registers `/sw.js?direct=1`. It precaches `/offline.html`,
the manifest, icons, and hashed `_next/static` assets; navigations are
network-first and fall back to the offline page. It never caches `/api/`,
`/healthz`, `/ws`, or successful HTML responses. The normal cloud build keeps
the existing push-only worker.

## Data and logs

```text
SQLite:        ~/.vicoa/direct/store.db
token:         ~/.vicoa/direct/token
runner stderr: ~/.vicoa/session_stderr/<session-id>.log
runner log:    ~/.vicoa/claude_headless/<session-id>.log
```

SQLite uses WAL. Back up `.db`, `.db-wal`, and `.db-shm` together, or stop the
daemon first.

## Security boundaries

- The daemon listens on `127.0.0.1` only.
- Tailscale ACLs decide which devices can reach it.
- The token is the application-layer second gate and can be rotated.
- `--show-token` is the only path that writes the token to stdout; the normal
  startup log only records the token file path.
- Direct data is private: `~/.vicoa/direct` is 0700; store, logs, attachments,
  and the token file are 0600/0700.
- No Tailscale Funnel.
- Direct builds do not load Google Tag Manager and do not initialize PostHog;
  the marketing/telemetry surfaces stay off.
- CORS and `vicoa-local.` WebSockets only allow the configured origins.
- `vicoa-key.` stays loopback-only.

## Stop

Foreground: Ctrl+C. Background:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
kill <pid>
```

Do not use `pkill -f vicoa`; other sessions may be running on the machine.

A clean shutdown stops the headless runners this daemon spawned and, with
`--with-web`, the Next process it supervises.

## Current limitations

- Single machine only; no multi-machine aggregation.
- Tasks, teams, and sharing are not wired into direct mode.
- The PWA is offline-aware for the shell, but sessions and messages still
  require the daemon; there is no background sync or local write queue.
- Development can still run the browser on a second origin (Next on 3000,
  daemon on 8765); production uses `--with-web` for a single origin.
- Tailscale remote access requires the user to run `tailscale up` first.
