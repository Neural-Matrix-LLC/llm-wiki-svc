# Runbook — llmwiki + Cloudflare Tunnel on the Hostinger VPS

How to set up and start the production deployment from scratch, and how to ship
each later release. It puts [deployment-plan-container-hosting.md](deployment-plan-container-hosting.md)
§6 into practice for the box as it actually runs (2026-09-23).

## 0. What runs where

```
 Telegram / you ──HTTPS──> Cloudflare edge ──(outbound tunnel)──> cloudflared container
                                                                        │ llmwiki-net
                                                                        ▼
                                                  api container (alias llmwiki-api, port 8000)
```

| Where | What | Files |
|---|---|---|
| Dev machine | builds, smoke-tests and pushes the image; never deploys | this repo |
| GHCR | holds the image `ghcr.io/neural-matrix-llc/llmwiki:<tag>` | — |
| VPS `/docker/llmwiki/` | the llmwiki compose project: `init-data` → `api` → `telegram-webhook` one-shot | `docker-compose.yml` + `.env` |
| VPS `/docker/cloudflared/` | the tunnel compose project: container `cloudflared`, the connector for tunnel **srv-llmwiki** | `docker-compose.yml` (= `docker-compose-cloudflared.yml`) + `.env` |
| Cloudflare dashboard | tunnel **srv-llmwiki**: its public hostname → `llmwiki-api:8000`; optional Access | — |

`srv-llmwiki` is the tunnel's name in Cloudflare, and it appears only in the
dashboard. On the box, the tunnel is identified by the token in
`/docker/cloudflared/.env`. The container is called `cloudflared` either way.

Some facts that shape the steps below:

- **Two projects, two directories.** A Hostinger compose project reads exactly
  one env file, `.env`, in its own directory. The tunnel token and llmwiki's API
  keys therefore live in separate `.env` files, and neither container sees the
  other's secrets.
- **The VPS never builds.** It has no repo checkout. It gets two compose files
  and two `.env` files, and pulls a finished image. A code change means a new
  image tag (§A), not an edit on the box.
- **The tunnel reaches `api` over a Docker network, not over the host port.**
  llmwiki's compose names its network `llmwiki-net` and gives `api` the alias
  `llmwiki-api`. The cloudflared project joins that network as `external`. In
  the Cloudflare dashboard, `localhost:3010` would mean cloudflared's *own*
  loopback and could only return 502.
- **Order matters on the first start.** llmwiki creates `llmwiki-net`, so it
  must be up before cloudflared can join the network.
- **Telegram registration is automatic.** Every `docker compose up -d` in
  `/docker/llmwiki` runs the `telegram-webhook` one-shot
  (`scripts/telegram_webhook.py sync`). It covers the testing guide's steps 3–5:
  it probes the public webhook URL through the tunnel, calls `setWebhook`, and
  verifies with `getWebhookInfo`.

---

## A. Dev machine — build, gate and push an image

Do this for the first deploy and for every release. `IMAGE_TAG` is the release
version, and it must be new each time: the box pulls exactly the tag its `.env`
names.

1. **Commit what you are shipping**, so the tag matches a git state:
   ```bash
   cd ~/branch/llm-wiki-svc
   git status                      # clean, or commit first
   ```

2. **Name the image.** Compose builds `${DOCKER_USER}/llmwiki:${IMAGE_TAG}`.
   Both values come from the shell (or the repo's top-level `.env`):
   ```bash
   export DOCKER_USER=ghcr.io/neural-matrix-llc
   export IMAGE_TAG=0.1.3          # next unused version
   ```
   Nothing from `.env` is baked into the image. The Dockerfile copies only
   `src/`, `scripts/`, `config/`, `skills/` and `tests/fixtures/`, and
   `.dockerignore` excludes `.env` and `.env.*`. Your local `.env` can hold
   dev values and it does not matter.

3. **Build:**
   ```bash
   docker compose build api
   ```
   Add `WITH_WHISPER=1` in front only if the box needs captionless-YouTube
   transcription (about 2 GB larger image).

4. **Gate it before it leaves the machine.** The box never builds, so this is
   the last chance to catch a broken image:
   ```bash
   docker compose --profile test run --rm smoke
   ```
   It must end without errors. Running `pytest` and `ruff check . && mypy`
   beforehand is the usual pre-commit gate.

5. **Log in to GHCR (once per machine) and push:**
   ```bash
   echo '<classic PAT with write:packages>' | docker login ghcr.io -u <github-user> --password-stdin
   docker compose push api
   docker manifest inspect ghcr.io/neural-matrix-llc/llmwiki:$IMAGE_TAG >/dev/null && echo pushed
   ```
   If the `neural-matrix-llc` org enforces SAML SSO, authorize the token for the
   org (GitHub → the token → *Configure SSO*). Otherwise the push, and later the
   pull, fail with `unauthorized`.

---

## B. VPS — one-time setup

Run as root (or the deploy user in the `docker` group) on `srv1910993`.

### B1. Log the box in to GHCR

```bash
echo '<classic PAT with read:packages only>' | docker login ghcr.io -u <github-user> --password-stdin
```
The box only pulls, so give it a separate token with only `read:packages`,
SSO-authorized for the org. Docker saves the login in `/root/.docker/config.json`,
so every later `pull` uses it. Without this login, `docker compose pull` prints
`error from registry: unauthorized`, followed by a misleading hint to
"build from source". Ignore that hint.

### B2. Put the files on the box

From the dev machine:
```bash
ssh root@srv1910993 'mkdir -p /docker/llmwiki /docker/cloudflared'
scp docker-compose.yml             root@srv1910993:/docker/llmwiki/docker-compose.yml
scp .env.example                   root@srv1910993:/docker/llmwiki/.env.example
scp docker-compose-cloudflared.yml root@srv1910993:/docker/cloudflared/docker-compose.yml
scp .env.cloudflared.example       root@srv1910993:/docker/cloudflared/.env.example
```
The tunnel file is **renamed** to `docker-compose.yml` on the box, so plain
`docker compose ...` works in that directory (and Hostinger's Docker Manager
finds it). Re-copy `docker-compose.yml` whenever it changes in git. It is part
of the release, like the image.

### B3. llmwiki's `.env` — `/docker/llmwiki/.env`

If the box already has a working `.env`, keep it and add the lines below.
Otherwise start from the template:
```bash
cd /docker/llmwiki
cp -n .env.example .env
nano .env
```
The values that matter for this deployment:

```bash
# image coordinates - Compose interpolation (what `pull` fetches)
DOCKER_USER=ghcr.io/neural-matrix-llc
IMAGE_TAG=0.1.3                      # exactly what step A pushed

# host port; the tunnel does not use it, direct http://<vps-ip>:3010 access does
API_PORT=3010
API_BIND=0.0.0.0                     # 0.0.0.0 keeps http://<vps-ip>:3010 reachable. Docker-published
                                     # ports bypass ufw, so it is world-open plain HTTP; 127.0.0.1
                                     # closes it (tunnel unaffected) - see F

# real backends and keys - as for any real run (see .env.example sections)
STORAGE_BACKEND=r2
VECTOR_BACKEND=vectorize
CF_ACCOUNT_ID=...
CF_API_TOKEN=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
INGEST_API_TOKEN=<long random, not "changeme">
# ...LLM provider keys per config/providers.py...

# YouTube from a cloud IP - one of these, or videos fail to capture
YOUTUBE_PROXY_URL=
YOUTUBE_COOKIES_PATH=

# Telegram (optional channel)
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_WEBHOOK_SECRET=<output of: openssl rand -hex 20>
PUBLIC_BASE_URL=https://llmwiki.<yourdomain>   # the tunnel hostname, https, no trailing path
```

Notes:
- `PUBLIC_BASE_URL` turns automatic webhook registration on. If it is blank, or
  `TELEGRAM_BOT_TOKEN` is blank, the one-shot prints `[SKIP]` and does nothing.
- `TELEGRAM_WEBHOOK_SECRET` may contain only `A-Z a-z 0-9 _ -` (Telegram's
  rule). `openssl rand -hex 20` output satisfies it.
- Lock the file down: `chmod 600 /docker/llmwiki/.env`.

### B4. The tunnel's `.env` — `/docker/cloudflared/.env`

```bash
cd /docker/cloudflared
cp -n .env.example .env
nano .env                            # TUNNEL_TOKEN=<real token>
chmod 600 .env
```
The token is in Cloudflare Zero Trust → Networks → Tunnels → **srv-llmwiki** →
Configure → "Install and run a connector": the long string after `--token`.
This is the only variable the tunnel needs. Ingress rules are not configured on
the box (see B5).

### B5. Cloudflare dashboard — route srv-llmwiki's hostname to the api container

Zero Trust → Networks → Tunnels → **srv-llmwiki** → **Public Hostname** → add
(or edit the existing llmwiki entry):

| Field | Value |
|---|---|
| Subdomain / Domain | `llmwiki` / `<yourdomain>` |
| Path | *(empty)* |
| Service type | `HTTP` |
| URL | `llmwiki-api:8000` |

`llmwiki-api` is the api container's alias on `llmwiki-net`, and `8000` is the
port *inside* the container. Do **not** use `localhost:3010` or
`host.docker.internal:3010`.

Saving creates the DNS record for the hostname. Check that it resolves before
C4. Step-by-step and troubleshooting: **§G. Cloudflare — Telegram Bot**.

### B6. Cloudflare Access — only if you use it

Check Zero Trust → Access → Applications for an app covering
`llmwiki.<yourdomain>`. You can also test from any machine: if
`curl -sI https://llmwiki.<yourdomain>/healthz` redirects to
`*.cloudflareaccess.com`, Access is on.

If it is on, Telegram (and Mailgun) cannot pass its login. Add a **second**
self-hosted application:

- domain `llmwiki.<yourdomain>`, path `channels/telegram/webhook`, policy
  action **Bypass**, include **Everyone**
- the same for `channels/email/inbound` if the email channel is used

These paths stay protected. The route rejects any request without the
`X-Telegram-Bot-Api-Secret-Token` header (401), and the email route rejects any
request with a bad Mailgun HMAC signature.

### B7. Retire any previous tunnel container

The new tunnel file uses `container_name: cloudflared`, so an old container
with that name blocks it. Find it:
```bash
docker ps -a --filter name=cloudflared --format '{{.Names}}  {{.Status}}  {{.Label "com.docker.compose.project.working_dir"}}'
```
Remove it just before C3, so the tunnel is down only for seconds. Use
`docker compose down` in its old directory, or `docker rm -f cloudflared`.

---

## C. VPS — first start (order matters)

### C1. Pull the image

```bash
cd /docker/llmwiki
docker compose pull
```
Expect `Pulled` for `ghcr.io/neural-matrix-llc/llmwiki:<IMAGE_TAG>`. An
`unauthorized` error means B1 is missing or its token lacks org access, or that
tag was never pushed (A5).

### C2. Start llmwiki

```bash
docker compose up -d
docker compose ps -a
```
What happens, in order:
1. **`init-data`** runs once as root, chowns `./.data` to the container user
   and exits 0. It shows as `Exited (0)` in `ps -a`; that is expected.
2. **`api`** starts on network `llmwiki-net` with the alias `llmwiki-api`. The
   host port binds to `<API_BIND>:3010` (`0.0.0.0:3010` by default). After its healthcheck passes (up to
   about 30 s), it shows as `healthy`.
3. **`telegram-webhook`** starts once `api` is healthy and runs the three checks.
   On this **first** start the tunnel is not up yet, so its step 3 fails
   (`answered 502/530 ...` or `unreachable`) and it exits 1. That is expected;
   C4 re-runs it.

Check the api locally:
```bash
curl -s localhost:3010/healthz
```
The output should show `"config"` with `llm_routing: per-op table`. A
`log_level` field confirms the new image is running.

### C3. Start the tunnel

```bash
docker rm -f cloudflared 2>/dev/null   # the old one, from B7, if still there
cd /docker/cloudflared
docker compose up -d
docker compose logs cloudflared | grep -i "registered tunnel connection"
```
Expect up to four `Registered tunnel connection` lines. In the dashboard,
Zero Trust → Networks → Tunnels should now show **srv-llmwiki** as
**HEALTHY**. If it shows **DOWN**, the token in `/docker/cloudflared/.env`
belongs to a different tunnel, or was pasted incompletely. Then check from
anywhere:
```bash
curl -s https://llmwiki.<yourdomain>/healthz
```
- A 502 or 530 here means the dashboard URL (B5) is wrong, or cloudflared is not
  on `llmwiki-net`. Check the network:
  `docker network inspect llmwiki-net --format '{{range .Containers}}{{.Name}} {{end}}'`
  must list both `cloudflared` and the api container.
- An error like `network llmwiki-net declared as external, but could not be
  found` at `up` means C2 did not run first.

### C4. Register the Telegram webhook (first start only)

```bash
cd /docker/llmwiki
docker compose run --rm telegram-webhook
```
A good report:
```
3. Tunnel
  [OK]   https://llmwiki.<yourdomain>/channels/telegram/webhook -> 401 without the secret
4. Register
  [OK]   setWebhook https://llmwiki.<yourdomain>/channels/telegram/webhook
5. Verify
  [OK]   https://llmwiki.<yourdomain>/channels/telegram/webhook (pending updates: 0)
```
Step 3 posts to the public URL *without* the secret header. The 401 therefore
proves the whole path works: edge → tunnel → `llmwiki-net` → api → route
mounted → secret enforced.

| `[FAIL]` at step 3 | Meaning | Fix |
|---|---|---|
| `answered 502` / `530` | tunnel can't reach `llmwiki-api:8000` (retried for ~25 s first) | B5 URL; C3 network check |
| `answered 302` / `403` | Cloudflare Access in front of the webhook | B6 Bypass app |
| `answered 404` | route not mounted: `TELEGRAM_BOT_TOKEN` not in the api container | set it in `.env`, then `docker compose up -d --force-recreate` |
| `unreachable: [Errno -2] Name or service not known` | no DNS record for the hostname: the public hostname is missing or named differently from `PUBLIC_BASE_URL` | §G |
| `unreachable: ...` (other) | no tunnel, or DNS not propagated yet | C3; §G |

| `[FAIL]` elsewhere | Meaning |
|---|---|
| `[SKIP] PUBLIC_BASE_URL is blank` (not a failure) | set `PUBLIC_BASE_URL=https://<subdomain>.<yourdomain>` in `/docker/llmwiki/.env` and re-run |
| `PUBLIC_BASE_URL=http://...` | Telegram accepts only `https://` |
| `TELEGRAM_WEBHOOK_SECRET is blank` | set it; the route would 401 every update |
| `setWebhook: HTTP 401` | wrong `TELEGRAM_BOT_TOKEN` |
| `Telegram's last delivery failed: ...` | Telegram tried a real update and got an error; the message says which |

### C5. End-to-end check

1. In Telegram, send the bot some plain text. It replies
   `Captured. source_id=<id>`.
2. Send a bare URL, such as a blog post. It replies the same way.
3. Check the processing status:
   ```bash
   curl -s https://llmwiki.<yourdomain>/sources/<id>
   docker compose -f /docker/llmwiki/docker-compose.yml logs -f api
   ```
4. Confirm the route rejects a request without the secret, through the tunnel:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://llmwiki.<yourdomain>/channels/telegram/webhook
   ```
   Expect `401`.

---

## D. Every later release

1. On the dev machine, run §A with a new `IMAGE_TAG` (e.g. `0.1.4`). If
   `docker-compose.yml` changed in git, re-copy it (B2).
2. On the box:
   ```bash
   cd /docker/llmwiki
   sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=0.1.4/' .env
   docker compose pull
   docker compose up -d
   docker compose logs telegram-webhook       # the tunnel is already up: expect [OK] x3
   curl -s localhost:3010/healthz
   ```
   Leave the tunnel running. It keeps its place on `llmwiki-net` while `api`
   is recreated, so the only downtime is the api restart.

**Rollback:** set `IMAGE_TAG` back to the previous version, then run `pull` and
`up -d` again.

---

## E. Day-to-day operations

| Task | Command (in `/docker/llmwiki` unless noted) |
|---|---|
| Apply an edited `.env` | `docker compose up -d --force-recreate`. **Not** `restart`, which reuses the old environment |
| Rotate `TELEGRAM_WEBHOOK_SECRET` | edit `.env`, then `docker compose up -d --force-recreate`; the one-shot re-registers with the new secret |
| Check the webhook | `docker compose run --rm telegram-webhook info` |
| Remove the webhook | `docker compose run --rm telegram-webhook delete` (add `--drop-pending` to discard queued updates) |
| Logs | `docker compose logs -f api` · `cd /docker/cloudflared && docker compose logs -f` |
| Weekly global lint (host cron) | `cd /docker/llmwiki && docker compose --profile ops run --rm lint` |
| Rotate the tunnel token | edit `/docker/cloudflared/.env`, then `cd /docker/cloudflared && docker compose up -d --force-recreate` |
| Full stop | **tunnel first**: `cd /docker/cloudflared && docker compose down`, then `cd /docker/llmwiki && docker compose down`. llmwiki's `down` removes `llmwiki-net`, and it fails while cloudflared is still attached |
| Start after a full stop | as in §C: llmwiki, then the tunnel. The one-shot succeeds on its own if the tunnel comes up within its retry window; otherwise run `docker compose run --rm telegram-webhook` |
| Reboot of the VPS | nothing to do: `api` and `cloudflared` are `restart: unless-stopped`, and Docker restores `llmwiki-net`. The webhook registration persists at Telegram |

---

## F. Security checklist

- Both `.env` files are `chmod 600` and are never committed. The repo commits
  only `.env.example` and `.env.cloudflared.example`.
- `API_BIND=0.0.0.0` (as deployed) publishes port 3010 on the public IP as plain
  HTTP - Docker-published ports bypass ufw. `INGEST_API_TOKEN` is then the only
  guard on that path; Access (B6) protects the tunnel hostname only. To close it,
  set `API_BIND=127.0.0.1` and `up -d --force-recreate api` (the tunnel keeps
  working; `ss -ltnp | grep 3010` then shows `127.0.0.1:3010`, and direct
  `http://<vps-ip>:3010` access stops - use `ssh -L 3010:127.0.0.1:3010` instead).
- `INGEST_API_TOKEN` is a long random value, and Access (B6) is in front of
  `/ingest`, `/upload` and `/mcp` if the hostname is exposed beyond you.
- The box's GHCR token has only `read:packages`.

---

## G. Cloudflare — Telegram Bot

This section connects the bot to llmwiki through the srv-llmwiki tunnel:
public hostname → DNS → `PUBLIC_BASE_URL` → webhook. Guide §2 steps 3–5 on
this box. Before starting, the tunnel must be connected (C3: four
`Registered tunnel connection` lines, and `cloudflared` listed on `llmwiki-net`).

A connected tunnel is **not** enough on its own. Without a public hostname, the
name has no DNS record: `curl` says `Could not resolve host`, and C4 says
`unreachable: [Errno -2] Name or service not known`.

### G1. Add the public hostname

Zero Trust → Networks → Tunnels → **srv-llmwiki** → **Configure** →
**Public Hostname** tab → **Add a public hostname**:

| Field | Value |
|---|---|
| Subdomain | `llmwiki` (any name works; the name must match `PUBLIC_BASE_URL`) |
| Domain | `neuralmatrixllc.com` |
| Path | *(empty)* |
| Service → Type | `HTTP` |
| Service → URL | `llmwiki-api:8000` |

Save. Cloudflare creates a proxied (orange-cloud) DNS record in the zone:
`CNAME <subdomain> → <tunnel-id>.cfargotunnel.com`. Check it under the domain's
**DNS → Records**.

If the save fails with *"An A, AAAA, or CNAME record with that host already
exists"*, delete the old record for that name under DNS → Records first. It is
typically an A record pointing at the VPS IP. Then add the hostname again.

### G2. Check DNS and the tunnel path

From the VPS or any machine:
```bash
dig +short <subdomain>.neuralmatrixllc.com @1.1.1.1     # Cloudflare IPs (104.x / 172.x)
curl -sS -m 10 https://<subdomain>.neuralmatrixllc.com/healthz   # JSON from llmwiki
```
Use `https://` with **no port**. The tunnel answers on 443 at Cloudflare's edge;
`:3010` / `:8010` exist only on the VPS IP.

| Result | Cause | Fix |
|---|---|---|
| `dig` empty, `dig NS neuralmatrixllc.com` shows `*.ns.cloudflare.com` | no public hostname, or a different name | G1 |
| `dig NS` shows non-Cloudflare nameservers | the domain's DNS isn't on Cloudflare, so tunnel hostnames can't be created | add the site in Cloudflare, switch the nameservers in Hostinger's panel, wait, then G1 |
| `dig @1.1.1.1` has IPs, but the VPS still can't resolve | the VPS cached the earlier "not found" | `resolvectl flush-caches`, or wait a few minutes |
| `curl` → 502 / 530 | the hostname exists, but the tunnel can't reach api | Service URL must be `llmwiki-api:8000`; C3 network check |
| `curl` → 302 to `*.cloudflareaccess.com` | Access is in front of the hostname | expected for `/healthz`; add the B6 Bypass for the webhook path |

### G3. Point llmwiki at the hostname and register

In `/docker/llmwiki/.env`:
```
PUBLIC_BASE_URL=https://<subdomain>.neuralmatrixllc.com    # https, no port, no trailing slash
TELEGRAM_BOT_TOKEN=...          # from @BotFather
TELEGRAM_WEBHOOK_SECRET=...     # openssl rand -hex 20
```
Then:
```bash
cd /docker/llmwiki                      # the llmwiki project, NOT /docker/cloudflared
docker compose run --rm telegram-webhook
```
- Changed only `PUBLIC_BASE_URL`? `run` reads `.env` fresh, and `api` doesn't
  use it, so there is nothing to recreate.
- Changed the token or secret? Run `docker compose up -d --force-recreate`
  instead. It recreates `api` with the new values and runs the one-shot too.

For the expected output and the step-3 failure table, see C4. The run from
`/docker/cloudflared` fails with `no such service: telegram-webhook`: that
project has only `cloudflared`.

### G4. Test with the bot

DM the bot some text, then a URL. Each should get back
`Captured. source_id=...`. Then:
```bash
curl -sS https://<subdomain>.neuralmatrixllc.com/sources/<source_id>
docker compose run --rm telegram-webhook info     # no last error, pending 0
```

### G5. Renaming the subdomain (e.g. `tgbot` → `llmwiki`)

Telegram holds exactly one webhook URL per bot, so a rename is:
1. G1: add the new public hostname (`llmwiki`, same service
   `llmwiki-api:8000`). Keep the old one until step 4.
2. G2: check that `https://llmwiki.neuralmatrixllc.com/healthz` answers.
3. Set `PUBLIC_BASE_URL=https://llmwiki.neuralmatrixllc.com` and run
   `docker compose run --rm telegram-webhook`. `setWebhook` replaces the old
   URL, and `info` should show the new one.
4. Delete the old public hostname (`tgbot`) on the tunnel, then its CNAME under
   DNS → Records. Deleting the hostname does not always remove the record.
5. If Access is used, repoint the B6 Bypass app(s) to the new hostname.

