# llmwiki

A cost-effective, cloud-hosted research knowledge base. Captured sources are kept
immutable under `raw/`; an LLM compiles them into an interlinked markdown wiki
under `wiki/`. Retrieval is hybrid — the compiled wiki first, a vector index as
fallback — and every answer carries citations that resolve back to a real source.

- `llmwiki-KB-design.md` — architecture and scope (authoritative)
- `docs/implement-plan.md` — the implementation plan: Part I Phase 0 (milestones, runbook, tests), Part II packaging + Phase 1
- `HISTORY.md` — the change log

## Quickstart (offline, no keys)

```bash
# Creates the virtual environment and installs dependencies, including the
# dev extra (pytest, mypy, ruff, ...) - plain `uv sync` skips it
uv venv
source .venv/bin/activate
uv sync
cp .env.example .env

pytest                                  # unit tests, no network
python scripts/smoke_flow.py --offline  # end-to-end flow with fake adapters
llmwiki --offline ingest --file tests/fixtures/sample.pdf
llmwiki --offline concepts
```

`--offline` swaps in a filesystem store, an in-memory vector index, a hash-based
embedder and a scripted LLM. `.data/wiki/` opens directly as an Obsidian vault.

Five kinds of source go in through the same command — a PDF file, a blog URL,
a YouTube URL, pasted text, and a text file. What a source *is* is detected,
never declared: a link is fetched first and routed by the content type the
server actually returns, so a link to a paper is read as a PDF and a link to a
post is read as a page.

```bash
llmwiki --offline ingest --file paper.pdf                                   # PDF file
llmwiki --offline ingest --url "https://karpathy.github.io/2019/04/25/recipe/"   # blog
llmwiki --offline ingest --url "https://www.youtube.com/watch?v=fvIVGmwgk4w"     # YouTube
llmwiki --offline ingest --text "Kelly sizing maximizes long-run log wealth."    # pure text
llmwiki --offline ingest --file notes.md                                    # text file
git log --oneline | llmwiki --offline ingest --text -                       # pure text, from stdin
llmwiki --offline concepts
```

`--url` still fetches the page or YouTube transcript over the network;
`--offline` only keeps storage and the LLM local and fake. Drop `--offline`
once `.env` has real credentials.

Over HTTP: `POST /ingest` takes `{"url": ...}` or `{"text": ...}`, and
`POST /upload` takes a file. The MCP `ingest_source` tool takes `url` or
`text`. All of them call the same `tools.ingest_source()`.

The two are not interchangeable — `/ingest` reads a JSON body, so a file has to
go to `/upload` as multipart, not to `/ingest`:

```bash
TOKEN=...   # INGEST_API_TOKEN from .env
curl -sS -X POST http://localhost:8010/upload \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@practical-guide.pdf" -F "title=practical guide pdf"

curl -sS -X POST http://localhost:8010/ingest \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://karpathy.github.io/2019/04/25/recipe/"}'

curl -sS http://localhost:8010/sources/<source_id>   # poll to `done`
```

`--url` still fetches the page or YouTube transcript over the network;
`--offline` only keeps storage and the LLM local and fake. Drop `--offline`
once `.env` has real credentials.

Over HTTP: `POST /ingest` takes `{"url": ...}` or `{"text": ...}`, and
`POST /upload` takes a file. The MCP `ingest_source` tool takes `url` or
`text`. All of them call the same `tools.ingest_source()`.

The two are not interchangeable — `/ingest` reads a JSON body, so a file has to
go to `/upload` as multipart, not to `/ingest`:

```bash
TOKEN=...   # INGEST_API_TOKEN from .env
curl -sS -X POST http://localhost:8010/upload \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@practical-guide.pdf" -F "title=practical guide pdf"

curl -sS -X POST http://localhost:8010/ingest \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://karpathy.github.io/2019/04/25/recipe/"}'

curl -sS http://localhost:8010/sources/<source_id>   # poll to `done`
```

## Running against real backends

Fill `.env` with Cloudflare and Anthropic credentials, then:

```bash
python scripts/bootstrap_indexes.py --check   # verify Vectorize indexes exist
llmwiki serve                                 # REST on :8010 (API_PORT in .env.example), MCP at /mcp
```

`implement-plan.md` Part I §6 is the full operational runbook — creating the R2 bucket,
minting both kinds of Cloudflare token, and confirming the embedding dimension
before any index is created. `scripts/README.md` documents every script under
`scripts/` (setup, diagnostics, browsing what's in Vectorize, bulk backfill,
evaluation) with usage examples.

### Query agent, tracing and evaluation

`llmwiki ask` (and `GET /answer`) runs a bounded LangGraph loop: the wiki-first
retrieval, then up to `AGENT_MAX_TOOL_CALLS` evidence-gathering tool calls
(`search_wiki`, `search_chunks`, `get_page`, optionally `search_web`), then the
answer with verified citations. `AGENT_MAX_TOOL_CALLS=0` gives the plain
single-call behaviour. With `LANGSMITH_TRACING=true` every answer is one
LangSmith trace and carries a `run_id`.

```bash
python scripts/eval_answer.py --offline                # the answer-quality golden set, no keys
python scripts/eval_answer.py --langsmith --judge      # as a LangSmith experiment, with the LLM judge
llmwiki feedback <run_id> --score 0 --correction "…"   # file a correction; --promote-feedback turns it into an example
```

`docs/llm-wiki-technical-document.md` §3.3 (the graph) and §10 (tracing,
evaluation, the correction loop) have the details; `docs/phase1-testing-guide.md`
§5 is the step-by-step.

### Querying the wiki with curl

`/answer` is a **`GET`** with `q` (the question) and `k` (retrieval hits,
default 5) as query parameters — no body, no bearer token. `curl -G` sends the
`--data-urlencode` pairs as the query string, so `?`, `&` and spaces in the
question are safe. A `POST /answer` gets `405`; a `q` passed through `-d`
without `-G` gets `422`.

```bash
export WHOST_IP=127.0.0.1
export WHOST_PORT=8010          # API_PORT in .env; 8011 for `docker compose --profile dev`

# Basic question
curl -s -G "http://$WHOST_IP:$WHOST_PORT/answer" \
  --data-urlencode "q=What does the incremental wiki compiler do to avoid full-wiki scans?" \
  --data-urlencode "k=5"

# Pretty-printed
curl -s -G "http://$WHOST_IP:$WHOST_PORT/answer" \
  --data-urlencode "q=Summarize what the wiki knows about prompt caching" | jq .

# Just the answer text and its sources
curl -s -G "http://$WHOST_IP:$WHOST_PORT/answer" \
  --data-urlencode "q=Who proposed the LLM Wiki pattern?" \
  | jq -r '.text, "", "Sources:", (.citations[] | "  - \(.title) (\(.source_id))")'

# Did the query graph do what you expect? (tool calls, fallback, cited sources)
curl -s -G "http://$WHOST_IP:$WHOST_PORT/answer" \
  --data-urlencode "q=Who proposed the LLM Wiki pattern?" \
  | jq '{steps, used_rag_fallback, citations: [.citations[].source_id], external_refs}'
```

The response is an `Answer` (`src/llmwiki/models/plan.py`):

```json
{
  "text": "Prompt caching lets ... [1][2]",
  "citations": [
    {"source_id": "src_01j8...", "title": "Anthropic prompt caching docs", "url": "https://...", "slug": "prompt-caching"}
  ],
  "used_rag_fallback": false,
  "steps": [ {"tool": "search_wiki", "args": {"q": "prompt caching"}, "...": "..."} ],
  "context": "...the wiki pages / chunks the answer was generated from...",
  "external_refs": [],
  "run_id": "3f2c...-..."
}
```

- `citations` always resolve to real objects under `raw/`.
- `external_refs` are web-search results (only when `AGENT_WEB_SEARCH_POLICY`
  allows them) and are never citations.
- `run_id` is non-null only with `LANGSMITH_TRACING=true`; it is the handle
  `POST /feedback` takes — and that route *is* a POST and *does* need the token:

```bash
export INGEST_API_TOKEN=...     # same value as in .env

RUN_ID=$(curl -s -G "http://$WHOST_IP:$WHOST_PORT/answer" \
  --data-urlencode "q=Who proposed the LLM Wiki pattern?" | jq -r .run_id)

curl -s -X POST "http://$WHOST_IP:$WHOST_PORT/feedback" \
  -H "Authorization: Bearer $INGEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": \"$RUN_ID\", \"score\": 1.0, \"correction\": \"Correct - Andrej Karpathy.\"}"
```

`/feedback` answers `409` when tracing is off (there is no run to attach to).
Connection refused → check `API_HOST`/`API_PORT` in `.env`, or
`curl -s http://$WHOST_IP:$WHOST_PORT/healthz | jq`.

### Phase 2: domains, hybrid search, usage, images

Landed 2026-09-19 (design v1.4 §4.10, `docs/implement-plan-v1.4.md` §21,
technical document §12). Everything is behaviour-compatible under its
default switch; an existing deployment needs no data migration.

```bash
# Domains: a curated registry; `general` is the wiki you already have.
llmwiki domains add ml-systems --description "Training/serving infra, GPU scheduling"
llmwiki domains list
llmwiki ingest --url https://example.org/paper --domain ml-systems     # explicit
llmwiki ingest --url https://example.org/post                          # routed (one cheap call)
llmwiki ask "how does paged attention work?" --domain ml-systems       # scoped
llmwiki ask "how does paged attention work?"                           # QUERY_DOMAIN_POLICY=all
llmwiki synthesize --all                                               # per-domain overview pages

# Hybrid retrieval (on by default): build the keyword index once, then it keeps itself.
llmwiki lexical rebuild
python scripts/migrate_phase2.py --check    # the one-time upgrade steps, or --apply
# Optional: file the pre-Phase-2 corpus (all `general`) under registered domains.
python scripts/migrate_domains.py plan --out domain-plan.json        # one route_domain call per source
python scripts/migrate_domains.py apply --plan domain-plan.json --yes # re-embed + compile into the domain

# Cost: usage, alerts, the hard cap.
llmwiki usage --month 2026-09
curl -s -H "Authorization: Bearer $INGEST_API_TOKEN" http://localhost:8000/usage | jq .by_domain
open "http://localhost:8000/dashboard?token=$INGEST_API_TOKEN"
# COST_ALERT_DAILY_USD / COST_ALERT_MONTHLY_USD notify (log or Telegram);
# COST_HARD_CAP_MONTHLY_USD pauses processing of new sources - capture and answers keep working.
llmwiki worker                               # what is queued, in flight, parked

# Images and scans: text-first vision, off by default.
VISION_MODE=auto llmwiki ingest --file whiteboard.png                  # describe_image must route to a vision model
```

Over MCP the same shows up as an optional `domain` argument on `search_wiki`,
`get_page`, `list_concepts` and `ingest_source`, plus one new read-only tool,
`list_domains`. A Telegram caption/text starting with `#ml-systems`, or an
email subject starting with `[ml-systems]`, files the source there.

### Authentication: SSO login (optional)

`AUTH_MODE` (docs/sso-integration-design.md, v1.1 section overrides v1.0):

| Mode | Writes, `/usage`, `/dashboard` | `/mcp` | `/login` |
|---|---|---|---|
| `static` (default) | `INGEST_API_TOKEN` - exactly the Phase 0 behaviour | open (WARNING at start); static token if `SSO_PROTECT_READS=true` | 404 |
| `sso` | SSO access token with `SSO_REQUIRED_PERMISSION` (Bearer) or the browser session | SSO Bearer | yes |
| `both` | either; a bearer that fails JWT verification falls back to the static compare | SSO Bearer or static token | yes |

* Grant access in the SSO admin: permission `application.app1` on the user or a group
  (SSO README §6a). Register this app there as a ServiceClient
  (`manage.py create_service_client llm-wiki`) and set `SSO_CLIENT_ID` / `SSO_CLIENT_SECRET`:
  login and refresh are sent with it plus the browser's IP, so the SSO throttles per user. A wrong
  secret makes the SSO answer 401 `invalid_client`: logged as an ERROR here, users see a 503
  "Login service misconfigured".
* Browser IP: by default the TCP peer. uvicorn's own `--proxy-headers` rewriting is disabled
  (`--no-proxy-headers` in the Dockerfile, and neutralised when the app is imported whatever the
  launch flags, D-19), so `X-Forwarded-For` / `FORWARDED_ALLOW_IPS` never choose the IP. Behind cloudflared
  (Cloudflare Tunnel) set `CLIENT_IP_HEADER=cf-connecting-ip` and `TRUSTED_PROXY_IPS` to the
  address(es) cloudflared connects from: the header is honoured only from those peers; an
  `X-Forwarded-For` list is read right to left, skipping trusted proxies.
* In `sso`/`both` the service refuses to start without `SSO_BASE_URL`, `SSO_ISSUER`,
  `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, or with a **weak `INGEST_API_TOKEN`: `changeme` or
  shorter than 24 characters** (static mode only logs a warning).
* Browser sessions are server-side: the `llmwiki_session` cookie (HttpOnly, SameSite=Lax, Secure
  unless `SESSION_COOKIE_SECURE=false`) holds a random 256-bit id; the SSO tokens live in SQLite at
  `SESSION_DB_PATH` (default `$LOCAL_STORAGE_PATH/sessions.sqlite3`, keyed by the SHA-256 of
  the id). It is created 0600 (directory 0700 when created) and the service refuses to start if
  the file is accessible by group or others. Absolute lifetime `SESSION_MAX_AGE_HOURS` (168 = 7
  days; the cookie's Max-Age is the remaining part), never extended; idle timeout
  `SESSION_IDLE_HOURS` (12). Expired rows are pruned at start and hourly. All uvicorn workers must share that file: the
  `BEGIN IMMEDIATE` transaction around a refresh is what stops two workers refreshing one session
  (a per-process asyncio lock only saves waiting inside one worker).
* CSRF: cookie-authenticated POST/PUT/PATCH/DELETE need `X-CSRF-Token` (the value is in the
  dashboard's `<meta name="csrf-token">`), else 403 `{"error":"csrf_failed"}`; Bearer requests
  are exempt. `/logout` takes it as a form field. An explicit but invalid Bearer is a 401 even
  when a valid session cookie is present.
* `/mcp` takes a Bearer token only, never the session cookie, so it cannot be hit by CSRF.
* `GET /login` while signed in redirects (303) to `next` (sanitised; default `/dashboard`). A failed
  re-login (wrong password, no permission, SSO error) keeps the existing session; a successful
  one rotates it (old row deleted, new id/cookie/CSRF value, old refresh token logged out at the
  SSO best effort). `/login`, `/logout` and `/dashboard` responses carry
  `Cache-Control: no-store` in sso/both.
* `last_seen` is written at most once a minute, so the idle timeout can be off by up to 60 s.
* `?token=` values (e.g. `/dashboard?token=...` in static/both) are redacted in uvicorn's access log.
  Parameter names are percent-decoded repeatedly (up to 5 rounds) before matching `token` /
  `*_token` (any case); a name still encoded after 5 rounds is redacted to be safe.
* SSO unreachable: 503 everywhere (login page included; for a session only once its access token
  has expired - the session is kept). Revoking the permission takes effect at the next refresh, i.e. within the
  access-token lifetime (15 min).
* `SSO_PROTECT_READS=true` also protects `/search /answer /concepts /page /domains /worker
  /sources` (in static mode with the static token, and `/mcp` too). Off by default, so default
  static mode is unchanged.

Integration test against a real SSO-server checkout (gunicorn + SQLite, nothing external):
`SSO_SERVER_DIR=../sso-server uv run pytest -m integration tests/integration/test_sso_live.py`.

## Docker

```bash
docker compose up --build                     # reads .env
docker compose --profile offline up api-offline   # no keys needed, port 8001
docker compose --profile test run --rm smoke      # end-to-end check in the image
```

### Dev mode (no rebuild after a code change)

The `dev` service bind-mounts this working tree into the container and installs
`llmwiki` editable, so an edit under `src/` is live on the next request —
uvicorn `--reload` restarts the app in place. Rebuild only when
`requirements.txt` or the `Dockerfile` changes.

```bash
docker compose --profile dev up dev               # http://localhost:8011 (DEV_PORT)
docker compose --profile test run --rm pytest     # the unit suite, in the same image
docker compose --profile dev run --rm dev llmwiki lint
docker compose --profile dev build dev            # only after a dependency change
```

It runs as `DEV_UID:DEV_GID` (default `1000:1000`) so files it writes through
the mounts — `./.data`, caches — stay owned by you rather than by root. Check
with `id -u` and override in `.env` if your ids differ.

### Running commands in a production container (no source checkout)

The runtime image carries everything the operational commands need, so the box
needs only `docker-compose.yml` and `.env` - no clone, no venv:

| In the image | So you can run |
|---|---|
| `/opt/venv/bin/llmwiki` (on `PATH`) | every `llmwiki …` subcommand |
| `/app/scripts/` | `python scripts/<name>.py` |
| `/app/config/`, `/app/skills/` | the same routing table and skills the service uses |
| `/app/tests/fixtures/` | `scripts/smoke_flow.py --offline` and other offline modes |

There is no `.env` file inside the container; `load_settings()` falls back to
the environment Compose injects from the box's `.env` (`env_file:`), which is
the one `api` runs with. From the directory holding `docker-compose.yml`:

```bash
# Inside the running api container - same env, same uid, nothing new starts
docker compose exec api llmwiki --help            # which subcommands this image has
docker compose exec api llmwiki status
docker compose exec api llmwiki domains list
docker compose exec api llmwiki ask "what is attention?"
docker compose exec api python scripts/check_cloudflare_setup.py --quick

# In a throwaway container from the same image, env and /data mount -
# works while api is stopped, and reads the *current* .env
docker compose run --rm api python scripts/migrate_phase2.py --check
docker compose run --rm api python scripts/reset_vectorize.py    # dry run; --yes to act
docker compose run --rm api llmwiki lexical rebuild
# /data is ./.data on the host, so the plan file can be reviewed there before apply
docker compose run --rm api python scripts/migrate_domains.py plan --out /data/domain-plan.json
docker compose run --rm api python scripts/migrate_domains.py apply --plan /data/domain-plan.json --yes
```

`run --rm` does not publish `API_PORT`, so it never clashes with a running
`api`. Things to know:

- **`exec` sees the environment the container was created with.** After
  editing `.env`, `docker compose up -d --force-recreate` first, or use
  `run --rm`.
- **The commands are those of the image, not of your checkout.** A subcommand
  or script added after the `IMAGE_TAG` you run is not there until you pull a
  newer tag (`llmwiki --help` / `ls scripts/` tell you).
- **Host files are not visible.** To ingest a local PDF, `POST /upload` it, or
  copy it in first: `docker compose cp paper.pdf api:/tmp/` then
  `docker compose exec api llmwiki ingest --file /tmp/paper.pdf`.
- **A CLI `ingest` compiles in its own process** (`inline` worker mode), outside
  the service's per-domain lock. Fine on a quiet box; while captures may be
  arriving for the same domain, prefer `POST /ingest`.
- **Never `llmwiki serve`** - `api` already serves port 8000.
- **Not in the image:** `scripts/sync_wiki.py` needs `rclone` and a local vault,
  so run it from a checkout on the machine that holds the vault.
- Scripts that call the service (`verify_capture.py`) default to
  `localhost:$API_PORT`, which is `8000` inside the container - right for
  `exec`; pass `--base-url` otherwise.

## Layout

`src/llmwiki/` is layered, and the layering is enforced by a test
(`tests/unit/test_layering.py`), not by convention:

```
models/  →  storage/ extractors/ embedding/ vector/ llm/ websearch/  →  wiki/ agent/
         →  pipeline/  →  tools.py (+ eval/)  →  api/ mcp/ cli.py channels/
```

Business logic lives in the core package. `api/`, `mcp/` and `cli.py` validate
input, call one function in `tools.py`, and serialize the result.
