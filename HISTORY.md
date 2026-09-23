# HISTORY

Every code, configuration, or architectural change to this repository, in
reverse-chronological order. See `CLAUDE.md` for the rule this file follows.

---

## 2026-09-22 — Telegram webhook registration as a compose one-shot (production steps 3–5)

**Goal.** `docs/phase1-testing-guide.md` §2 steps 3–5 (tunnel, `setWebhook`,
`getWebhookInfo`) assumed a laptop: a host-installed `cloudflared` quick tunnel
and `curl` with the bot token in the shell. The production VPS runs only two
compose projects — `cloudflared` (own compose, token-managed tunnel) and
llmwiki — with no repo checkout, no `curl` in the image, and the token only in
`.env`. Those steps are now controlled by `docker-compose.yml`.

**Root cause of the gap.** `deployment-plan-container-hosting.md` Phase 4
step 13 routed the tunnel to `http://localhost:8010`. From a cloudflared
*container* that is its own loopback, so the route could only 502; and a
separately composed container is on a different Docker network from `api`.

**Implementation detail.**

- `docker-compose.yml`: the default network is named `llmwiki-net` (fixed,
  not `<dir>_default`) and `api` carries the alias `llmwiki-api`, so the
  cloudflared project (`docker-compose-cloudflared.yml`, deployed as its own
  project in its own directory; token from that directory's `.env`, template
  `.env.cloudflared.example`) joins it as `external` and the tunnel hostname points at
  `http://llmwiki-api:8000`. Port mapping is now
  `${API_BIND:-0.0.0.0}:${API_PORT}:8000` — default unchanged for WSL2 dev.
  `API_BIND=127.0.0.1` would stop publishing plain HTTP to the internet
  (Docker-published ports bypass `ufw`), but it was tried on the VPS and ended
  direct `http://<vps-ip>:API_PORT` access (curl timed out), which is still
  used. So the box keeps `API_BIND=0.0.0.0`, and the runbook lists loopback as
  optional hardening.
- New `telegram-webhook` service (no profile, so every `up -d` runs it; same
  image and `.env` as `api`; `depends_on: api: service_healthy`; `api` does
  not depend on it, so a Telegram/tunnel outage never blocks startup) running
  `scripts/telegram_webhook.py sync`.
- `scripts/telegram_webhook.py`: `sync` = (3) POST the public webhook URL
  without the secret header and require 401 — proves tunnel → network → api →
  route mounted → secret enforced, since `setWebhook` never probes the URL;
  502/530 (retried ~30 s), 302/403 (Cloudflare Access → Bypass app) and 404
  (token not in api's env) each get a named hint and stop before registering;
  (4) `setWebhook` with the secret and `allowed_updates=[message,
  channel_post]`, always resent so a rotated secret takes effect; (5)
  `getWebhookInfo`: URL must match and no delivery error newer than this
  registration. `info` and `delete [--drop-pending]` for later checks and
  teardown. No-op (exit 0) while `TELEGRAM_BOT_TOKEN` or `PUBLIC_BASE_URL` is
  blank, so a dev `up` never repoints a shared bot. The token is redacted from
  all output, including httpx errors.
- `docker-compose-cloudflared.yml` replaces the box's hand-written tunnel
  compose: it read `env_file: .env` (llmwiki's) and interpolated
  `TUNNEL_TOKEN=${TUNNEL_TOKEN}` — interpolation that `.env.cloudflared` could
  never feed — and reached llmwiki through `host.docker.internal`. Hostinger
  compose projects read only `.env`, so the tunnel lives in its own directory
  with its own `.env` (`env_file: .env`, `required: true`, supplying
  `TUNNEL_TOKEN` directly); `host.docker.internal` is gone. The separate
  directory also keeps the two projects apart (no orphan warnings, `down` never
  crosses over) and llmwiki's keys out of the tunnel's environment. `.gitignore` un-ignores only
  `.env.cloudflared.example`; `.dockerignore` gains `.env.*` so a token file is
  never sent in a build context.
- `docs/runbook-hostinger.md` (new, 2026-09-23): the whole production procedure
  in one place — build/smoke/push to GHCR (`DOCKER_USER=ghcr.io/neural-matrix-llc`),
  `docker login ghcr.io` on the box (a `read:packages` token; its absence is the
  `pull` → `unauthorized` failure), the two directories and `.env` files,
  dashboard hostname and Access bypass, first-start order (llmwiki → tunnel →
  re-run the one-shot), release/rollback, day-to-day operations, and a security
  checklist. Linked from the deployment plan's Phase 4 and the testing guide.
  §G "Cloudflare — Telegram Bot" (2026-09-23) is from the first real setup on
  the box. The tunnel was connected, but the webhook one-shot failed with
  `unreachable: [Errno -2] Name or service not known` because no public
  hostname existed yet, so its DNS record didn't either. Adding one under
  srv-llmwiki → Configure → Public Hostname (subdomain `tgbot`, service
  `llmwiki-api:8000`) fixed it. §G is those steps: hostname, DNS check,
  `PUBLIC_BASE_URL`, register, bot test, and renaming the subdomain
  (`tgbot` → `llmwiki`). C4's failure table now separates the DNS failure and
  the `[SKIP] PUBLIC_BASE_URL is blank` case.
  The production tunnel's Cloudflare name, `srv-llmwiki`, is used in every
  dashboard path (runbook, testing guide, `.env.cloudflared.example`,
  `docker-compose-cloudflared.yml`); the container stays `cloudflared`.
- `Settings.public_base_url` / `PUBLIC_BASE_URL` (new; read only by the
  script), `API_BIND` in `.env.example`.
- Docs: guide §2 gains "Steps 3–5 in production (docker compose)" (one-time
  cloudflared snippet + dashboard hostname, report format, failure table);
  deployment plan Phase 4 step 13 revised, step 14 gains the webhook Bypass
  Access app, new step 14a.

**Related files.** `docker-compose.yml`, `docker-compose-cloudflared.yml`,
`.env.cloudflared.example`, `.gitignore`, `.dockerignore`, `scripts/telegram_webhook.py`,
`src/llmwiki/config.py`, `.env.example`, `docs/phase1-testing-guide.md`,
`docs/deployment-plan-container-hosting.md`, `docs/runbook-hostinger.md`, `CLAUDE.md`,
`tests/unit/test_telegram_webhook_script.py`.

**Test coverage.** New `tests/unit/test_telegram_webhook_script.py` (22 tests,
`httpx.MockTransport`, no network): skip/refuse before any request; probe
sent without the secret; each failing probe status names its cause and sends
no `setWebhook`; transient 502s retried; exact `setWebhook` payload (incl.
trailing-slash base); URL mismatch and fresh vs. stale delivery errors;
`info`/`delete`; token never printed; compose wiring (`telegram-webhook` has
no profile, depends on healthy `api`, `api` not on it; `llmwiki-net` name and
`llmwiki-api` alias; `docker-compose-cloudflared.yml` joins `llmwiki-net` as
external, reads `.env`, interpolates no token). No tests removed. Full suite: 539 passed; the one
failure (`test_agent_graph.py::test_run_id_is_a_uuid_only_when_tracing_is_on`)
fails identically without this change — this checkout's `.env` has
`LANGSMITH_TRACING=true`.

---

## 2026-09-22 — `scripts/sync_wiki.py`: plan II §21's rclone remote + bucket mirror as one command

**Goal.** §21.2–§21.3 were two shell snippets to copy by hand (`rclone config
create ...` from four `R2_*` values, then `rclone sync r2:$R2_BUCKET/wiki
./vault --exclude "_meta/**"`). One script does both, reading the values from
`.env` the way every other script does, so "update my Obsidian vault from R2"
is `python scripts/sync_wiki.py`.

**Implementation detail.**

- `scripts/sync_wiki.py [DEST]` (default `./vault`), four named steps, exit 1
  at the first failure: (1) *Remote* — `rclone listremotes`; if `r2:` (or
  `--remote NAME`) is missing, or `--setup` is given, run §21.2's
  `rclone config create <name> s3 provider=Cloudflare access_key_id=...
  secret_access_key=... endpoint=... acl=private no_check_bucket=true
  --non-interactive` from `Settings.r2_*`. Placeholder (`changeme`) or empty
  values are refused by name before rclone is called. The secret is passed on
  rclone's argv and redacted (`***`) from every printed command line and
  error. (2) *Bucket* — `rclone lsd <remote>:<bucket>` must list `wiki`
  (`storage/layout.py` `WIKI_PREFIX`); a failure hints at endpoint/bucket/
  Object Read. (3) *Mirror* — `rclone sync <remote>:<bucket> DEST --exclude
  "wiki/_meta/**"`: the **whole bucket**, so `raw/`, `status/` and `wiki/`
  land side by side. **Deviation from plan §21.3** ("only the `wiki/`
  prefix"), and why: a `wiki/`-only folder did not open as a usable vault in
  practice — every source note points at `` `raw/{id}/` ``
  (`wiki/compiler.py`), and Karpathy's layout keeps `raw/` beside `wiki/` in
  the one vault so `raw/{id}/extracted.md` opens next to the page that cites
  it. §21.3 now carries a "revised 2026-09-22" note; its original form is
  `--wiki-only` (`<remote>:<bucket>/wiki`, `--exclude "_meta/**"`, DEST is
  then the wiki root) for a light view when `raw/` is too big to carry.
  `--copy` swaps in `rclone copy` (never deletes, for a vault holding one's
  own notes), `--include-meta` keeps `wiki/_meta/gists.json`/`cost.jsonl`,
  `--dry-run` and `--quiet` (cron) pass through, otherwise `-P` progress. R2
  is always the source and DEST the destination — §21.4's one rule; the
  script has no code path that writes to the bucket. (4) *Check* —
  `DEST/wiki/index.md` must exist; page counts per
  `concepts/`/`entities/`/`sources/` and the number of `raw/` source folders
  are printed. `--setup-only` stops after (2). With `STORAGE_BACKEND=local` it prints where `LOCAL_STORAGE_PATH/wiki`
  is and exits 0 — nothing to mirror. Missing `rclone` on PATH is a named
  failure with the install URL.
- `--env-file PATH` builds `Settings(_env_file=PATH)` instead of
  `load_settings()`, so `.env.prod` drives the remote, bucket and mirror
  (shell-exported variables still win, as everywhere in `llmwiki`). Because the
  bucket name is in the rclone *path* but the credentials are in the
  *remote*, an existing remote is now only accepted when its stored
  `access_key_id` and `endpoint` (`rclone config dump`) equal the env file's;
  a mismatch fails step 1 by name with the hint `--remote <other-name>` /
  `--setup`. Found on first use: this machine's `r2:` remote had been created
  from `.env.prod`'s key, so `.env` (bucket `llmwiki-dev`) was being read
  through the prod key — the earlier mirror worked only because that key can
  see both buckets. One remote name per environment (`r2` / `r2-dev`) is the
  intended shape.
- `--obscure` is deliberately *not* passed to `rclone config create`: the s3
  backend's `secret_access_key` is not a password-type field, so rclone stores
  it plain either way (verified against rclone 1.75.1 in a scratch config).
- Verified against the real `llmwiki-dev` bucket with `RCLONE_CONFIG` pointed
  at a scratch config (so the create-remote path ran for real and the
  machine's own `rclone.conf` was untouched): `lsd` → `raw status wiki`; the
  mirror landed `raw/{id}/{extracted.md,meta.json,original.bin}`, `status/`
  and `wiki/{index.md,sources/}` with `wiki/_meta/` excluded; counts printed.
- `.gitignore` gains `/vault*/` (the default destination `./vault` — and a
  `./vault-prod` beside it — is inside the checkout and is a view, never
  committed) and `.env.*` with `!.env.example` re-included: `.env.prod` was
  sitting untracked and unignored, one `git add -A` away from a commit. `scripts/README.md` gets the table row and
  a section; plan §21 gets a one-line pointer above §21.2 and §21.5's "no
  code is added by this section" sentence is corrected; `CLAUDE.md`'s Current
  State names the script.

**Related files.** `scripts/sync_wiki.py` (new), `scripts/README.md`,
`.gitignore`, `docs/implement-plan.md` (§21.1/§21.5), `CLAUDE.md`.

**Test coverage.** New `tests/unit/test_sync_wiki_script.py` (20 tests), loading
the script by path like the other script tests and replacing `subprocess.run`
with a recorder, so no rclone binary or network is touched: the `config
create` argv matches §21.2; the mirror argv is `sync <remote>:<bucket>
DEST --exclude wiki/_meta/**` with R2 as the source (and
`<remote>:<bucket>/wiki … --exclude _meta/**` under `--wiki-only`), each flag
mapped (`copy`/`include-meta`/`dry-run`/`quiet`); `redact` hides the secret and not
the key id; an existing remote is not recreated, a missing one is, `--setup`
forces it, placeholders refuse before rclone runs, a create failure's message
is redacted; the bucket check requires `wiki` and names the Object Read hint;
`vault_report` needs `wiki/index.md` (or `index.md` under `--wiki-only`),
counts pages per folder and `raw/` source folders (0 when `raw/` is absent,
not a failure); `main` end to end with the fake (`listremotes → config → lsd
→ sync` of the bucket root, `1 raw sources` reported, no secret on stdout),
`--setup-only` stops before the mirror, the `local` backend runs nothing,
and a missing rclone exits 1; an existing remote whose stored key id (or key
id and endpoint) differ from the env file is refused before anything is
created or listed; `--env-file` with a temp `.env.prod` creates `r2-prod`
from that file's values and lists `r2-prod:llmwiki-prod`, and the same file
against the dev remote name exits 1; a missing `--env-file` exits 1. No test
removed. Unit suite: 520 passing (one pre-existing, `.env`-dependent
failure in `test_agent_graph.py::test_run_id_is_a_uuid_only_when_tracing_is_on`
when `LANGSMITH_TRACING=true` is set in the real `.env`; unrelated).

---

## 2026-09-21 — The two implementation plans merged into a single `docs/implement-plan.md` (v1.5); §21 documents viewing the R2 wiki in Obsidian

**Goal.** (1) Answer "how do I look at the KB graph now that `wiki/` lives in
R2 rather than a local folder" in the plan, not in chat. (2) Fold the two
implementation plans — `docs/implement-plan.md` v1.1 (Phase 0) and
`docs/implement-plan-v1.4.md` v1.2 (Phase 0.5 packaging + §19/§20 Phase 1
behaviour) — into one document, since every reader was already having to hold
both open and the second one's "supersedes: nothing" header was no longer true
in practice (§19 replaced Part I §11, §14/§19.8 replaced Part I §13).

**Implementation detail.**

- `implement-plan-v1.4.md` first went 1.2 → 1.3 with a new **§21 "Viewing the
  R2 Wiki in Obsidian"**: the R2 key layout is already an Obsidian vault
  layout and pages already use `[[slug]]` links, so the only gap is transport.
  §21.2 is the one-time `rclone config create r2 s3 provider=Cloudflare ...`
  from the `R2_*` values in `.env` (and why `rclone sync r2://llmwiki`
  fails — the remote is a config *name*, not a URL scheme); §21.3 is
  `rclone sync r2:$R2_BUCKET/wiki ./vault --exclude "_meta/**"` — only the
  `wiki/` prefix, never `raw/`/`status/`, `_meta/` excluded because
  `cost.jsonl` grows on every compile; §21.4 is the one rule — the mirror is
  one-directional because the compiler and the scheduled lint own `wiki/`
  (Remotely Save is fine on a read-only R2 token); §21.5 lists the
  alternatives (Quartz/Foam/Logseq over the mirror; a `GET /graph` + D3 page
  in FastAPI as the not-built answer to Part I §15 item 5). No env var, code
  or test is introduced by the section.
- Then the merge: `docs/implement-plan.md` (v1.5, keeping the plain filename — the version
  lives in the header, so there is one plan file and no `-vX.Y` copies) = a new front matter
  (reference convention, a "how the two Parts relate / status" table that
  records N0–N8 as **not executed** and §19/§20/§21 as landed, a combined
  contents table) + **Part I** (the old `implement-plan.md`, body verbatim) +
  **Part II** (the old `implement-plan-v1.4.md` 1.3, body verbatim). **Each
  Part keeps its original section numbering** — this is the whole reason for
  the Part structure: ~40 live references in `src/`, `tests/`, `config/`,
  `.env.example`, `CLAUDE.md` and the other docs cite section numbers, and
  renumbering would have broken every one of them plus every historical entry
  below. Five Part I sections (§3, §4, §11, §13, §15) carry a one-paragraph
  **Superseded** callout pointing at the Part II section that replaced them;
  the text underneath is unchanged so the Phase 0 record stays readable.
- `docs/implement-plan-v1.4.md` is deleted (`git rm`); the old `implement-plan.md`
  is overwritten by the merged file. The live references were rewritten mechanically:
  `implement-plan-v1.4.md §X` → `implement-plan.md Part II §X`,
  `plan-v1.4 §X` → `plan II §X`, `implement-plan.md §6.x` →
  `implement-plan.md Part I §6.x`, `plan-1.1 D5` → `plan I D5`. Entries
  below this one in `HISTORY.md` (and the stale copy in `docs/HISTORY.md`)
  are left as written; the v1.5 front matter states the mapping
  (`implement-plan.md §X` = Part I §X, `implement-plan-v1.4.md §X` = Part II
  §X).

**Related files.** `docs/implement-plan.md` (rewritten as the merged v1.5, 3122 lines),
`docs/implement-plan-v1.4.md` (deleted),
`CLAUDE.md`, `README.md`, `scripts/README.md`, `.env.example`,
`config/ops.py`, `config/providers.py`, `src/llmwiki/config.py`,
`src/llmwiki/factory.py`, `src/llmwiki/llm/{router,langchain_client,routing_config,providers}.py`,
`src/llmwiki/embedding/workers_ai.py`, `scripts/check_cloudflare_setup.py`,
`scripts/check_local_llm.py`, `tests/unit/test_{anthropic_client,config,routing_config,factory,router,langchain_client}.py`
(docstrings/comments only), `docs/llmwiki-KB-design_v1.4.md`,
`docs/llm-wiki-technical-document.md`, `docs/cloudflare-vectorize-setup-plan.md`,
`docs/deployment-plan-container-hosting.md`, `docs/phase1-testing-guide.md`.

**Test coverage.** No code path changed: the only edits under `src/`, `config/`,
`scripts/` and `tests/` are comment, docstring and user-facing-message text
(the `implement-plan.md section 6` pointer in two error messages now reads
`implement-plan.md Part I section 6`; no test asserted on it). No tests
added or removed; the full unit suite, `ruff` and `mypy` were run after the
rewrite.

---

## 2026-09-20 — `.env`: an empty value with an inline comment is the comment, under Docker Compose

**Root cause.** `scripts/verify_capture.py --youtube ...` against the compose
`api` service on :8010 (R2 / Vectorize / Workers AI / OpenRouter, Whisper
configured as the only YouTube route) failed at `POST /ingest` with a 422
whose detail read `YOUTUBE_COOKIES_PATH=# Netscape-format cookies.txt
exported from a browser logged in is not a file`. `.env` had the line
`YOUTUBE_COOKIES_PATH=                        # Netscape-format ...`, copied
from `.env.example`. python-dotenv - what `pydantic-settings` uses when the
service runs locally - reads that as an empty value; Docker Compose's
`env_file` parser reads it as the literal string `# Netscape-format cookies.txt
exported from a browser logged in` (`docker compose config` shows it quoted).
Same for `YOUTUBE_PROXY_URL` and `MAILGUN_SIGNING_KEY`. In the container the
cookies route was therefore "configured" and wins over Whisper by design, so
every YouTube ingest failed before any fetch - and the email channel mounted
with a nonsense signing key. A value followed by a comment
(`YOUTUBE_WHISPER_MODEL=base   # ...`) parses the same in both; only the
empty case differs. Nothing in the suite could see it: unit tests never go
through Compose, and locally the same file works.

**Fix.** The eight such lines in `.env.example` (`LLM_BASE_URL`,
`LANGSMITH_ENDPOINT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
`MAILGUN_SIGNING_KEY`, `YOUTUBE_PROXY_URL`, `YOUTUBE_COOKIES_PATH`,
`YOUTUBE_WHISPER_MODEL`) and the three in this checkout's `.env` now carry
their comment block on the lines above a bare `KEY=`. A note at the top of
`.env.example` states the rule. `docker compose config` now resolves all
three to `""`.

**Related files.** `.env.example`, `tests/unit/test_config.py`.

**Tests.** No regressions; none removed. New:
`test_config.py::test_env_example_has_no_empty_value_followed_by_an_inline_comment`
scans `.env.example` for the `^KEY=\s+#` shape and names each offender.

---

## 2026-09-20 — `scripts/verify_capture.py`: prove a `/ingest` (YouTube) or `/upload` (PDF) capture landed in `raw/`, `wiki/` and the vector index

**Goal.** `smoke_flow.py` exercises the Python core in-process; nothing
walked a *running* service's front door and then checked all three places a
source is supposed to end up. After the YouTube cloud-IP work, the question
"did that capture actually store what it should, where it should?" needed a
one-command answer.

**Implementation.** A standalone script (`scripts/verify_capture.py`, no
package imports at module level beyond `httpx`) that:

- captures through REST: `POST /ingest {"url": ...}` for the YouTube URL,
  `POST /upload` multipart for the PDF, with `INGEST_API_TOKEN` from `.env`
  (or `--token`), and polls `GET /sources/{id}` until `done`/`failed`. A 4xx
  is reported with the API's `detail` (so a 422 from a YouTube block names
  the block); a duplicate is verified rather than rejected.
- reads `raw/` directly (`factory.object_store`): `meta.json` modality and
  url; `original.*` byte-equal to the uploaded PDF, or a `text`/`start`/
  `duration` segment list for YouTube, in both cases hashing to
  `meta.json`'s sha256 and matching `byte_size`; `extracted.md` non-empty.
- reads `wiki/`: `wiki/sources/{id}.md`; `gists.json` lists the source note
  and at least one concept/entity page citing the source (the manifest also
  carries the note under its own id - the first run tripped on that); each
  page's front matter carries the source, a gist and a body; `index.md`
  links them; then the same pages via `GET /concepts` / `GET /page/{slug}`.
- reads the chunks index (`factory.vector_store` + `factory.embedder`,
  filtered `where={"source_id": id}`, with a short retry loop for
  Vectorize's eventual consistency): count equals `chunk_count`, ids are
  `{id}:0..n-1`, and each stored `text` is exactly
  `extracted.md[char_start:char_end]` - the chunker's offsets are the
  contract, so a stale or cross-wired vector is caught, not just a missing
  one. Then `GET /search` for the source's own opening text must surface its
  chunks or a page citing it.
- refuses to start when `/healthz` reports different backends than `.env`
  (the direct read-back would silently look at the wrong store);
  `--rest-only` skips the direct half; `VECTOR_BACKEND=memory` degrades the
  vector check to `GET /search` with a note, since the server's store is
  process-local. Sources are kept by default (`--cleanup` calls
  `tools.delete_source`).

Verified live against an offline `uvicorn` on :8765 (local storage, fake
LLM/embedder) with `tests/fixtures/sample.pdf`, and in-process for the two
paths that server cannot reach (direct vector read-back, YouTube raw shape).

**Related files.** `scripts/verify_capture.py` (new), `scripts/README.md`
(row + section), `CLAUDE.md` (pointer, test count 459 → 499),
`tests/unit/test_verify_capture_script.py` (new).

**Tests.** No regressions (`pytest`: 497 passed, 1 skipped; the one failure,
`test_agent_graph.py::test_run_id_is_a_uuid_only_when_tracing_is_on`,
pre-exists this change and fails identically on a clean tree - the shared
`settings` fixture reads this checkout's real `.env`, where tracing is on).
No tests removed. New: `tests/unit/test_verify_capture_script.py` (8 tests)
runs the script's `Api` against the FastAPI app under `TestClient` on the
offline backends in one process, so the factory cache hands the script the
same object store and memory vector store the app wrote to and every
read-back path runs: a clean upload passes all checks; a duplicate is
verified; a swapped `original.pdf`, a chunk whose text is not its
`extracted.md` slice, a vector count disagreeing with the pipeline, a
missing source note, a malformed YouTube segment list and a wrong bearer
token are each named as failures.

---

## 2026-09-20 — `YOUTUBE_WHISPER_MODEL`: audio transcription for videos with no captions; Telegram capture moves behind the 200

**Goal.** Not every video has a caption track. When neither the transcript
API nor yt-dlp finds one, download the audio and transcribe it locally with
Whisper - FUND-financial-Research's final tier, ported this time, but gated.

**Design.**

- **Third tier, opt-in.** `YOUTUBE_WHISPER_MODEL` (blank = off; `base`,
  `small`, ...) and a `llmwiki[whisper]` extra. openai-whisper pulls torch
  (~2 GB) and needs ffmpeg, so neither is in `requirements.txt` or the default
  image; `docker compose build` with `WITH_WHISPER=1` (build arg, Dockerfile)
  adds both. A set model without the package or without ffmpeg is a one-line
  `ExtractionError` naming the install, raised *before* any audio download.
- **Only for "no captions".** A new `NoCaptions(ExtractionError)` is what the
  two caption routes raise when the video is reachable but has no track
  (`NoTranscriptFound`/`TranscriptsDisabled` from the transcript API; no json3
  track from yt-dlp). Only that falls through to Whisper. A block, an expired
  session, a dead link are still reported - Whisper never papers over a bad
  proxy or cookie file, and with the model unset a no-captions failure says
  `YOUTUBE_WHISPER_MODEL` is the fix.
- **Same raw shape.** `segments_from_whisper` turns `transcribe()`'s segments
  into the same `text`/`start`/`duration` list (ms precision), so `raw/` and
  `YouTubeExtractor` are unchanged for the third time.
- **Audio only, cleaned up.** `bestaudio/best` with `outtmpl` in a
  `mkdtemp` directory that `finally` removes; the same cookies/proxy as the
  caption routes via the shared `_yt_dlp_opts` context manager (which also
  owns the temp-copy-of-the-cookie-file rule from the previous entry). The
  model is loaded once per process (`lru_cache`), ~1 GB of RAM for `base`.
- **Telegram: capture behind the response.** Whisper is minutes of CPU per
  video, and `channels/telegram.py` ran capture inline in the request.
  Telegram re-delivers any update it has not seen a 2xx for within seconds,
  so a slow capture would start the same transcription again in parallel.
  `_handle_update` now only queues `_capture_and_process` as a Starlette
  background task; the route answers 200 immediately, the task does
  capture → ack (`Captured. source_id=...` / `Capture failed: ...`) →
  process, and `ingest_source` runs via `asyncio.to_thread` so `/healthz`
  (the Docker healthcheck) keeps answering during the fetch. An unexpected
  exception in the task is logged with its traceback and acked generically -
  the response is gone by then and silence would be the only alternative.
  The email channel is unchanged (it still returns `source_ids`, so its
  capture stays inline); a Whisper-length capture through Mailgun may exceed
  its webhook timeout - use Telegram or REST for those. Noted in
  `.env.example`.

**Also.** `requirements.txt` gains `yt-dlp==2026.8.19`: the Docker build
installs from that file, not `pyproject.toml`, so the previous entry's
dependency was not in the image. A full `pip freeze` re-pin was *not* done -
the venv has drifted from the lockfile independently of this work
(`google-genai`, `ast-serialize`, ...) and re-pinning those belongs to its own
change.

**Related files.** `src/llmwiki/extractors/youtube.py`,
`src/llmwiki/channels/telegram.py`, `src/llmwiki/pipeline/ingest.py`,
`src/llmwiki/config.py`, `pyproject.toml`, `requirements.txt`, `Dockerfile`,
`docker-compose.yml`, `.env.example`.

**Tests.**

- No regressions: `pytest` - 489 passed, 1 skipped (same pre-existing,
  unrelated `test_agent_graph` failure). `ruff`/`mypy` clean apart from the
  pre-existing `scripts/browse_vectors.py` E501;
  `scripts/smoke_flow.py --offline` passes; `docker compose config` and
  `docker build --check` validate with `WITH_WHISPER` 0 and 1.
- Changed, `tests/unit/test_channels.py`:
  `test_a_failed_fetch_is_acked_to_the_chat_not_raised` now exercises
  `_capture_and_process` (the task) rather than `_handle_update`.
  `test_yt_dlp_route_downloads_the_chosen_json3_track_only` additionally
  asserts no download happened; `test_a_video_without_captions_is_a_one_line_error`
  asserts the message names `YOUTUBE_WHISPER_MODEL`. The `fetch_transcript`
  stubs in `test_ingest.py` take `whisper_model`.
- New, `tests/unit/test_channels.py`:
  `test_handle_update_queues_the_capture_and_returns` (ingest must not run
  before the response), `test_handle_update_ignores_an_update_without_a_message`,
  `test_a_successful_capture_acks_then_processes` (order),
  `test_a_crash_after_the_200_is_reported_to_the_chat`.
- New, `tests/unit/test_extractors.py` (`whisper` faked in `sys.modules`,
  `shutil.which` patched, yt-dlp fake writes the "audio" file for
  `download=True`; no network, no torch):
  `test_whisper_runs_only_when_there_are_no_captions`,
  `test_no_captions_falls_through_to_whisper_on_the_audio` (format, cookie
  temp copy, temp dir removed, segment shape),
  `test_transcript_api_no_captions_also_falls_through_to_whisper`,
  `test_a_block_never_falls_through_to_whisper`,
  `test_whisper_configured_but_not_installed_is_a_clear_error`,
  `test_whisper_without_ffmpeg_is_a_clear_error`,
  `test_whisper_model_is_loaded_once_per_process`,
  `test_segments_from_whisper_matches_the_transcript_api_shape`.
- Not covered by a unit test, by design: real Whisper output quality/speed.
  Verify on the deployment with `WITH_WHISPER=1`, `YOUTUBE_WHISPER_MODEL=base`
  and a captionless video; expect minutes, and the service log line
  `youtube <id> has no captions; transcribing audio with whisper base`.

---

## 2026-09-20 — `YOUTUBE_COOKIES_PATH`: yt-dlp captions with a logged-in session as the alternative to the proxy

**Goal.** Give a cloud deployment a second way past YouTube's IP block that
costs no proxy subscription: the route the FUND-financial-Research agents
settled on (`fund_models/util.py`, their commit `e857f14`) - yt-dlp with a
browser-exported `cookies.txt`. Configuration picks the route:
`YOUTUBE_COOKIES_PATH` set → yt-dlp with that session; otherwise
`youtube-transcript-api`, through `YOUTUBE_PROXY_URL` when set (previous
entry). Cookies win when both are set; yt-dlp honours the proxy too.

**What was taken from FUND and what was not.** FUND's final form downloads
the full audio and transcribes it locally with Whisper. That is deliberately
*not* ported: it would add ffmpeg + openai-whisper + torch to
`pip install llmwiki`, spend minutes of CPU per video inside the capture path
(a webhook request), and produce a different raw format. What is ported is the
cookie mechanism plus FUND's earlier "Tier 2" (captions via yt-dlp, which it
still defines as `_yt_dlp_download_vtt` but no longer calls), with
`skip_download` so the video never crosses the wire. Two FUND details carried
over verbatim in spirit: the cookie file is copied to a temp file for each
call because yt-dlp writes refreshed cookies back and the deployed file is a
read-only mount; and a "Sign in to confirm you're not a bot"/403 from yt-dlp
means the session was rejected, which the error now says (re-export the
file). One FUND detail explicitly *not* carried over: its cookie files are
tracked in git. Here `.gitignore` gets `*cookies*.txt`.

**Implementation.**

- `src/llmwiki/extractors/youtube.py` — `fetch_transcript(url, proxy_url=None,
  cookies_path=None)` dispatches to `_segments_via_transcript_api` or
  `_segments_via_yt_dlp`. Both return the same `text`/`start`/`duration`
  list, so the stored raw object and `YouTubeExtractor` do not know which one
  ran. Two pure helpers are public for testing: `pick_caption_track(info)`
  (manual subtitles before automatic captions; `en`, then `en-*`, then
  anything; json3 format only) and `segments_from_json3(payload)` (YouTube's
  json3 events → segments; textless window events dropped). yt-dlp is
  imported inside the function, like every other optional SDK.
- `src/llmwiki/config.py`, `.env.example` — `youtube_cookies_path` /
  `YOUTUBE_COOKIES_PATH`, with the account-ban and expiry caveats and the
  "never commit it" rule. `.env.example`'s `YOUTUBE_PROXY_URL` note now cites
  the library README's recommendation (rotating residential; datacenter
  proxies are blocked like cloud IPs).
- `src/llmwiki/pipeline/ingest.py::_fetch` — passes both settings through.
- `docker-compose.yml` — commented read-only bind mount for the cookie file
  next to the existing `config/` override hint.
- `pyproject.toml`, `uv.lock` — `yt-dlp` as a core dependency (pure Python;
  no ffmpeg needed for captions).
- `.gitignore` — `*cookies*.txt`.

**Related files.** `src/llmwiki/extractors/youtube.py`,
`src/llmwiki/pipeline/ingest.py`, `src/llmwiki/config.py`, `.env.example`,
`docker-compose.yml`, `pyproject.toml`, `uv.lock`, `.gitignore`.

**Tests.**

- No regressions: `pytest` - 477 passed, 1 skipped (same pre-existing,
  unrelated `test_agent_graph` failure as the previous entry). `ruff` clean
  apart from the pre-existing `scripts/browse_vectors.py` E501; `mypy` clean;
  `scripts/smoke_flow.py --offline` passes.
- Changed: `test_ingest.py::test_capture_passes_youtube_proxy_url_from_settings`
  → `test_capture_passes_youtube_proxy_and_cookies_from_settings`, plus
  `test_capture_passes_none_when_neither_youtube_knob_is_set`; the
  `fetch_transcript` stub in `test_capture_fills_youtube_title_from_oembed`
  takes `**kw`.
- New, `tests/unit/test_extractors.py` (yt-dlp mocked at `yt_dlp.YoutubeDL`,
  asserting `download=False`; no network):
  `test_cookies_path_switches_the_fetch_to_yt_dlp` (and transcript-api is not
  constructed), `test_yt_dlp_gets_a_temp_copy_of_the_cookie_file_and_removes_it`,
  `test_yt_dlp_also_uses_the_proxy_when_both_are_configured`,
  `test_yt_dlp_route_downloads_the_chosen_json3_track_only`,
  `test_a_missing_cookie_file_is_a_clear_error_not_a_yt_dlp_call`,
  `test_a_video_without_captions_is_a_one_line_error`,
  `test_a_rejected_session_names_the_cookie_file_as_the_fix`,
  `test_pick_caption_track_prefers_manual_then_english_then_json3`,
  `test_segments_from_json3_matches_the_transcript_api_shape`.
- Not covered by a unit test, by design: whether a given cookie file gets
  past YouTube today. Verify on the deployment: mount the file, set
  `YOUTUBE_COOKIES_PATH`, send a link to the bot - the failure ack now says
  whether the session was rejected.

---

## 2026-09-20 — YouTube capture from a cloud IP: report the block, stop the webhook retry storm, add `YOUTUBE_PROXY_URL`

**Goal.** A YouTube link sent to the Telegram bot from the deployed service
answered the webhook with a 500 and a 40-line traceback, and the same video
then hit YouTube again every ~20 s.

**Root cause (two, stacked).**

1. `youtube_transcript_api` raised `RequestBlocked`: YouTube refuses the
   innertube transcript request from most cloud-provider egress IPs. That is
   an environmental fact, not a bug, and the library's own answer is a proxy
   (`GenericProxyConfig`). The service had no way to configure one.
2. `channels/telegram.py` let `ExtractionError` propagate out of the webhook.
   Telegram re-delivers any update not acknowledged with a 2xx, so each 500
   scheduled another delivery - the log shows the identical update at
   00:20:13, 00:20:30 and 00:21:03, each re-fetching the blocked video. The
   Mailgun inbound webhook had the same shape (Mailgun retries non-2xx for
   8 hours; only 406 tells it to stop), and `POST /ingest` 500'd the same way.

**An earlier uncommitted attempt was reverted** rather than kept: it caught
`RequestBlocked` inside `fetch_transcript` and stored `[]` as the raw
transcript so capture "succeeded" with a stub page. That breaks two design
rules at once: `raw/` is immutable and content-addressed by URL
(`content_hash_for_url`), so the empty placeholder would make every later
send of the same link a "duplicate, skipping" - the video could never be
captured once a proxy was configured - and a wiki page saying "transcript
unavailable" is compiled, embedded and cited as if it were a source. The same
diff had also moved `__version__` from 0.9.0 *down* to 0.1.2; that is
reverted too.

**Implementation.**

- `src/llmwiki/extractors/youtube.py` — `fetch_transcript(url, proxy_url=None)`.
  A set `proxy_url` becomes `GenericProxyConfig(http_url=..., https_url=...)`
  on the `YouTubeTranscriptApi` constructor. `RequestBlocked`/`IpBlocked`
  become a one-line `ExtractionError` that names the video id, whether a
  proxy was in use, and the setting to fix it; every other failure keeps only
  the first line of the library's message. Nothing is stored on failure, so
  the URL stays capturable. The pre-1.0 `get_transcript` fallback is gone -
  `proxy_config=` only exists on the 1.x constructor - and `pyproject.toml`
  pins `youtube-transcript-api>=1.0` (1.2.4 is installed).
- `src/llmwiki/config.py`, `.env.example` — `YOUTUBE_PROXY_URL` (blank =
  direct request). `pipeline/ingest.py::_fetch` passes it through; the
  extractor stays a pure function of its arguments.
- `src/llmwiki/tools.py` — re-exports `ExtractionError`. The layering rule
  (`test_layering.py`) keeps `api/` and `channels/` away from `extractors/`,
  and `tools` is the one module every transport is allowed to reach.
- `src/llmwiki/channels/telegram.py` — `_handle_update` catches
  `tools.ExtractionError | ValueError` from `_capture`, logs a warning, acks
  the chat with `Capture failed: <reason>` and returns normally, so the route
  answers 200 and Telegram stops re-delivering. Nothing is queued for
  processing.
- `src/llmwiki/channels/email.py` — the same failures become HTTP 406 with
  the reason in `detail`, the one non-2xx Mailgun does not retry.
- `src/llmwiki/api/routes.py` — `POST /ingest` maps `ExtractionError` to 422
  with the reason, instead of a 500.

**Related files.** `src/llmwiki/extractors/youtube.py`,
`src/llmwiki/pipeline/ingest.py`, `src/llmwiki/config.py`,
`src/llmwiki/tools.py`, `src/llmwiki/channels/telegram.py`,
`src/llmwiki/channels/email.py`, `src/llmwiki/api/routes.py`,
`pyproject.toml`, `.env.example`.

**Tests.**

- No regressions: `pytest` - 467 passed, 1 skipped. (One failure is
  pre-existing and unrelated:
  `test_agent_graph.py::test_run_id_is_a_uuid_only_when_tracing_is_on` fails
  identically on the parent commit because the shared `settings` fixture reads
  this checkout's real `.env`, which has LangSmith tracing on.) `ruff check`
  clean apart from a pre-existing E501 in `scripts/browse_vectors.py`; `mypy`
  clean; `scripts/smoke_flow.py --offline` passes.
- Changed: `test_ingest.py::test_capture_fills_youtube_title_from_oembed` -
  the `fetch_transcript` stub now accepts the `proxy_url` kwarg.
- New, `tests/unit/test_extractors.py` (library mocked at
  `youtube_transcript_api.YouTubeTranscriptApi`, no network):
  `test_fetch_transcript_goes_direct_when_no_proxy_is_configured`,
  `test_fetch_transcript_routes_through_youtube_proxy_url`,
  `test_a_youtube_ip_block_is_a_one_line_error_that_names_the_fix`,
  `test_other_transcript_failures_keep_only_the_first_line`.
- New, `tests/unit/test_ingest.py`:
  `test_capture_passes_youtube_proxy_url_from_settings`.
- New, `tests/unit/test_channels.py`:
  `test_a_failed_fetch_is_acked_to_the_chat_not_raised` (the ack text, no
  processing queued), `test_webhook_returns_200_when_capture_fails` (through
  the route - the 2xx is what stops the retries),
  `test_email_webhook_answers_406_when_capture_fails`.
- New, `tests/unit/test_routes.py`:
  `test_ingest_answers_422_when_the_url_cannot_be_fetched`.
- Not covered by a unit test, by design: whether a given proxy actually gets
  past YouTube. Verify on the deployment with `YOUTUBE_PROXY_URL` set and a
  link sent to the bot; the ack now says which of the two (proxy or direct)
  was tried.

---

## 2026-09-18 — Technical document: every `system=` call site in one table (§3.6)


**Goal.** Answer two recurring questions from one place: *why* only two
files under repo-root `skills/` are model-selectable while the other five
ops run fixed prompts, and *where exactly* each prompt body is handed to
`LLMClient.complete()`. §3.6's diagram showed the two mechanisms
(`load_prompt()` vs. `discover_skills()`) but predated `agent_step` and
`judge_answer`, and the design rationale was split across design §4.4,
§4.8.2 and plan §19.5/§19.9. Documentation only; no code change.

**Implementation.** New table in `docs/llm-wiki-technical-document.md`
§3.6, after the combined-flow diagram: one row per call site (nine rows over
the seven ops — `answer_query` has three: skill selection, skill generation,
fixed fallback), each with the file:line, the source of the `system=` text
and who decides it (source code vs. the model). A paragraph beneath it
states the rationale — §4.4's cost bound keeps the compiler deterministic,
`judge_answer` must be stable, `agent_step` is already the agentic node, so
the query agent's final generation is the one place §4.8.2 grants skill
invocation — and notes that `chains/prompts/*.md`'s SKILL.md frontmatter is
for external discovery only. The diagram's "EVERY CALL" header now lists
`agent/{query,graph,judge}.py`, not just `query.py`.

**Related files.** `docs/llm-wiki-technical-document.md` (§3.6), `HISTORY.md`.

**Tests.** None — documentation only. Line numbers in the table were
checked against the source at the time of writing; they are a convenience,
not a contract, and `tests/unit/test_routing_config.py`'s drift guard
remains the thing that catches a new `complete(op=...)` call site.

---

## 2026-09-17 — Technical document: object store / vector store / embedder configuration (§3.7)

**Goal.** `tools._components()` builds four adapters from `factory.*`, but
only `llm_client` had a documented configuration story (§3.5–§3.6, the
two-file routing table). The other three — `object_store`, `vector_store`,
`embedder` — were covered only as one bullet in the §6.5 variable list and
§5.5's "how to add one" note; which env vars each backend actually reads,
which creds are shared (`CF_*` between Vectorize and Workers AI, not R2),
and why `EMBEDDING_DIM` sits in two cache keys was reconstructible only
from `factory.py`. Documentation only; no code change.

**Implementation.** New `docs/llm-wiki-technical-document.md` §3.7: one
per-factory table of `*_BACKEND` switch + the variables each branch reads,
then the operational notes — the two realistic configurations (all-cloud
vs. `llmwiki --offline`'s four-variable override, mixing allowed),
`Settings.require()` failing by name and treating `changeme` as unset,
`EMBEDDING_DIM` as the coupling point between embedder, vector store and
the Vectorize indexes, per-configuration (not per-`Settings`) caching and
when `factory.reset()` is needed, lazy SDK imports, and a pointer to §5.5
for adding a backend. §6.5's "Backend selection" bullet now cross-links to
§3.7.

**Related files.** `docs/llm-wiki-technical-document.md` (§3.7 added; §6.5
one-line cross-reference).

**Tests.** None — documentation only. No regressions possible.

---

## 2026-09-17 — Technical document: entry-point → LLM-op mapping (§3.4.1)

**Goal.** Answer "which command makes which LLM calls?" in one place. A
`llmwiki ingest` of a YouTube URL left only two LangSmith traces
(`summarize_source`, `plan_compile`); the reason — the plan came back with
zero ops, so `create_page`/`patch_page` never ran, and nothing else on that
path calls the LLM — was only reconstructible from the compiler call tree
(§3.2), the query-graph call-count table (§3.3) and the `config/ops.py`
docstring together. Documentation only; no code change.

**Implementation.** New `docs/llm-wiki-technical-document.md` §3.4.1, a
table from each entry point (CLI command, REST route, MCP tool, capture
channel, eval script) to the `tools.*` function it runs and the ops it
calls, in order and with multiplicity: compile = `summarize_source`,
`plan_compile`, then `create_page`/`patch_page` once per planned op (zero
is possible); `ask` = `(n + 1)` × `agent_step` + `answer_query` (or the
skill-selection + chain calls); `eval_answer.py --judge` adds
`judge_answer`; every other command makes none. A closing note says
extraction and embedding never touch `LLMClient`, so they appear neither in
`cost.jsonl` nor in LangSmith.

**Related files.** `docs/llm-wiki-technical-document.md` (§3.4.1 added;
§3.4/§3.5 otherwise unchanged).

**Tests.** None — documentation only. No regressions possible.

---

## 2026-09-17 — Phase 1 manual test plan for C and D, with `check_local_llm.py` and `probe_query_graph.py`

**Goal:** a runnable, pass/fail manual test plan for the two Phase 1
workstreams that had setup narrative (`docs/phase1-testing-guide.md` §4/§5)
but no test cases: C (local-LLM routing — vLLM/llama.cpp) and D (LangGraph
query graph, external search, LangSmith eval/correction loop). Workstream A
was verified on 2026-09-16 (commit `c37e03d`); C and D needed the same, plus
the tooling to tell a PASS from a FAIL without reading logs by eye.

**Implementation detail:**
- `docs/phase1-manual-test-plan-C-D.md` (new): conventions, tooling table,
  cost/time expectations, condensed setup procedures (S-0.x common, S-C.1–7,
  S-D.1–6), 14 C cases and 21 D cases each with purpose / preconditions /
  steps / expected / evidence, a results-log table, and a known-limitations
  section. Two facts the plan pins that were not written down anywhere as
  test expectations: `RoutingLLMClient` has **no automatic failover** ("llama.cpp
  fallback" is a config choice — C-11 checks a down server fails loudly and
  does *not* route to the cloud), and the `no_answer` path is reached by an
  *empty* corpus, not a strange question (a nearest-neighbour store always
  returns something — D-07).
- `scripts/check_local_llm.py` (new): the C diagnostic, `check_cloudflare_setup.py`
  style. Six checks: active local providers + URL shape → `GET /models` with
  the bearer key → every local `config/ops.py` row names a *served* model →
  plain completion through `LangChainLLM`/`ChatOpenAI` (real token counts) →
  **forced tool call** (the shape every compile stage uses; the hint names the
  vLLM/llama.cpp flags) → `--op`: the real `factory.llm_client(...).complete(op=...)`
  with its `CostRecord`. A `SyntaxError`/`RuntimeError` in the routing table is
  a `[FAIL]` with "the same error the service raises at startup", not a
  traceback. Verified end to end against a stub OpenAI-compatible server
  (all six `[OK]`; 401, connection-refused, unserved model, inactive provider
  and malformed `ops.py` each `[FAIL]` with the intended hint).
- `scripts/probe_query_graph.py` (new): the D probe. Applies
  `AGENT_MAX_TOOL_CALLS` / `AGENT_WEB_SEARCH_POLICY` / `WEB_SEARCH_BACKEND` /
  `AGENT_MAX_WEB_SEARCHES` in-process per run (`Settings` is mutable; a new
  `QueryAgent` compiles its own graph), `--matrix` = caps {0, 1, N} × the
  policies the backend allows; `check_invariants()` returns the design §4.9
  violations (cap, web cap, policy/backend gates, citations resolve, refs never
  citations, non-empty text); captures the graph's `agent_step:` DEBUG lines
  via a temporary handler on `llmwiki.agent` so each run shows *why* a tool
  was called; `--verify-trace` polls LangSmith for the root run, prints the
  tree, and `trace_checks()` compares node/LLM-run names and counts with
  technical document §10.2 (`agent` ≤ cap+1, `tools` = steps, `agent_step`
  ≤ 2×(cap+1), no LLM runs expected from `FakeLLM`); `--json` appends one
  record per run. `--offline` mirrors `eval_answer.py --offline` (fakes,
  fixture docs, `./.data-probe`, `FakeWebSearcher` so every policy is
  meaningful).
- `src/llmwiki/agent/graph.py`: `agent_step` now logs at DEBUG *why the loop
  stopped* (`stop - answer (...)`, `stop - tool-call cap (n) reached`,
  `stop - context budget exhausted`, `stop - invalid action`) — previously
  only a tool choice was logged, so a cap being hit was invisible in the
  logs, and D-05's evidence is exactly that line. No behaviour change.
- `scripts/README.md`: both scripts in the table and their own sections;
  `docs/phase1-testing-guide.md`: pointer to the plan; `CLAUDE.md`: the two
  scripts and the new test file.

**Related files:** `docs/phase1-manual-test-plan-C-D.md`,
`scripts/check_local_llm.py`, `scripts/probe_query_graph.py`,
`src/llmwiki/agent/graph.py`, `tests/unit/test_phase1_scripts.py`,
`scripts/README.md`, `docs/phase1-testing-guide.md`, `CLAUDE.md`.

**Test coverage:** `tests/unit/test_phase1_scripts.py` (new, 15 tests) loads
both scripts by path and pins `check_invariants` (every violation and the
clean case), `trace_checks` (root name, always-nodes, tools = steps, agent ≤
cap+1, cap 0, LLM-run names only for a real model),
`active_local_providers` (key unset → inactive; real env beats `.env`;
`--provider` narrows) and `check_routes_served` (served / unserved with the
`--served-model-name` hint; a row for the other server is not judged), plus
one subprocess run of `probe_query_graph.py --offline --matrix
--max-tool-calls 4` asserting `PROBE PASS` and 9 `ok` runs (the cap is
passed explicitly because conftest's autouse fixture exports
`AGENT_MAX_TOOL_CALLS=0` and the child inherits it). No test removed or
weakened. `pytest`: 459 passed, 1 skipped, 7 deselected; `ruff check .` has
one pre-existing E501 in `scripts/browse_vectors.py:135` unrelated to this
change; `mypy` clean; `smoke_flow.py --offline` SMOKE PASS and
`eval_answer.py --offline` EVAL PASS (with `LOCAL_STORAGE_PATH` pointed at a
writable directory — this checkout's `.data/` is owned by the container uid).

---

## 2026-09-16 — Phase 1-D: LangGraph query graph, external search, LangSmith eval + correction loop

**Goal:** the last Phase 1 workstream (KB design §5 — "better agent tools and
citation quality", "first LangSmith datasets"). Phase 0's query agent was one
fixed procedure that could not follow a wikilink it had just read or look up a
page the first retrieval missed, and had no quality signal, so a prompt, skill
or model change had no regression check. Approved plan: full tool-calling
ReAct agent, answer-quality golden set with deterministic evaluators plus an
LLM judge, `src/llmwiki/eval/` + a script, and — from plan review — an
external (web) search tool and a defined place for corrections after an eval.
Design v1.4 → 1.6 (§4.9), implement plan → 1.2 (§20).

**Decisions (plan §20.2):** LangGraph is orchestration only — every model call
is still `LLMClient.complete()`, so routing, cost, caching, the doubles and
the layering guard are untouched and the N7 `get_llm()` surface stays
deferred. The tool decision is one forced-schema call on a new cheap op
`agent_step`, its schema derived from real LangChain tools; wiki-first stays
the first node verbatim; three code-enforced bounds (`AGENT_MAX_TOOL_CALLS`,
default 4, `0` = Phase 0 exactly; one shared context budget; `recursion_limit`
= 2n+8); citations remain a property of what was retrieved (the load-bearing
contract test did not change); `search_web` is a fourth tool offered only by
`AGENT_WEB_SEARCH_POLICY` (`off`/`weak`/`always`, default `off`) whose results
are `Answer.external_refs`, never citations; `langgraph` and `langchain-tavily`
are core dependencies; `langsmith` stays function-locally imported.

**Implementation detail:**
- `agent/graph.py` (new): `QueryState`, `build_query_graph(agent)` —
  `retrieve → [agent ⇄ tools] → select_skills → generate → resolve_citations`,
  `no_answer` short-circuit; cap and budget checked *before* the decision
  call; identical repeat refused (observation) but counted; bad args / tool
  exception → observation; an unusable decision (prose instead of the forced
  tool call — seen live with glm-5.3-flash on a long prompt) is retried once
  with a nudge (`STEP_ATTEMPTS = 2`), then the loop ends.
- `agent/toolkit.py` (new): `ToolResult`, `build_tools` (`search_wiki`,
  `search_chunks`, `get_page`, + `search_web` when a `WebSearcher` exists),
  `offered_tools` (the policy gate — applied where the action schema is
  built, so the model cannot pick a withheld tool), `action_schema`,
  `describe_tools`, `dispatch` (never raises).
- `agent/query.py`: `answer()` invokes the cached graph with
  `RunnableConfig(run_name="answer_query", run_id=uuid4(), recursion_limit,
  metadata)`; `Answer.run_id` is that uuid when tracing is on (first attempt
  read it back from `collect_runs()`, whose `traced_runs` are in *completion*
  order and returned the first LLM leaf — verified against LangSmith).
  `_build_context` gained a `budget` parameter; `_run_skill_chain` split out
  of `_answer_with_skills`; `web_searcher` constructor arg.
- `agent/judge.py` (new): `Judge.grade()` — one forced-schema
  `op="judge_answer"` call; an unusable verdict scores 0, never passes.
- `websearch/` (new L1): `WebSearcher` protocol, `TavilyWebSearcher`
  (`langchain-tavily`, imported only there; failures → `[]` + warning),
  `FakeWebSearcher`. `factory.web_searcher()` returns `None` for
  `WEB_SEARCH_BACKEND=none`; `tools._agent` passes it through.
- `eval/` (new L4, peer of `cli`): `dataset.py` (JSONL `Example`,
  `load/append/push_dataset`), `evaluators.py` (`citations_resolve`,
  `expected_source_cited`, `must_mention` gated at 1.0; `tool_calls` metric;
  `judge_grounded` opt-in), `run.py` (`answer_target`, `run_local`,
  `run_experiment` over `langsmith.evaluate` with version/git sha/bounds/
  routes metadata), `feedback.py` (`example_from_correction`,
  `corrected_examples`).
- `tools.py`: `judge_answer()`, `record_feedback()` (LangSmith feedback key
  `correctness`; raises by name when tracing is off), `health()` now reports
  the per-op `routes` in force and `query_graph` bounds. `api/routes.py`:
  `POST /feedback` (bearer; 409 when tracing is off). `cli.py`: `ask` prints
  tools/external refs/run_id; new `feedback` command.
- `models/plan.py`: `AgentStep`, `ExternalRef`, `Verdict`; `Answer` +=
  `steps`, `context`, `external_refs`, `run_id`. `config.py`: six new fields.
  `llm/routing_config.py`: `KNOWN_OPS` = 7. `llm/fake.py`: `agent_step` →
  `answer` at once, `judge_answer` → grounded. `llm/langchain_client.py`:
  `invoke(..., config={"run_name": op, "metadata": {...}})` so traces name
  LLM runs by op (D9; closes plan §19.9 item 3). `chains/prompts/
  agent_step.md`, `judge_answer.md`. `config/ops.py`: two rows; `answer_query`
  raised to 8192 tokens and `agent_step` to 2048 — glm-5.3-flash is a
  reasoning model whose thinking tokens count against the cap: at 2048 the
  live run returned an empty answer (`finish_reason=length`), at 512 the tool
  call was truncated.
- `scripts/eval_answer.py` (new): `--offline`, `--judge`, `--push`,
  `--langsmith`, `--dataset`, `--experiment-prefix`, `--export-failures`,
  `--promote-feedback`; exit 1 on a gated failure. `tests/fixtures/eval/
  answer_quality.jsonl` (new) over the offline fixture docs. `docker-compose.
  yml`: `eval` service (ops profile). `.env.example`: six vars + LangSmith
  block rewritten. `pyproject.toml`/`requirements.txt`/`uv.lock`: `langgraph`,
  `langchain-tavily` and their transitive pins.
- **Found along the way, fixed:** two pages in the real corpus had
  unparseable front matter — `render_page` wrote `title:`/`gist:` as bare
  YAML scalars and a model-written `Pi Agent vs OpenCode: Same Model` is not
  YAML; every read then failed and took every answer down. `render_page` now
  JSON-quotes the two free-text fields; `read_page` treats an unreadable page
  as absent with a warning (`lint` already reports it as `orphan`; re-compiling
  a source rewrites it). Existing broken pages persist until re-compiled.
- **Live verification (real corpus, OpenRouter, LangSmith project
  `llmwiki-phase1d-verify`):** the trace tree is root `answer_query` → node
  runs → `agent_step`/`answer_query` LLM runs named by op → `search_chunks`
  tool run (technical document §10.2 reproduces it); `run_id` is the root;
  `record_feedback` + `corrected_examples` produced a valid golden example;
  the model's malformed tool call (`slug` passed to `search_chunks`) became an
  observation, not a failure.

**Deviations from the approved plan:** (1) `tests/conftest.py` gained an
autouse fixture pinning `AGENT_MAX_TOOL_CALLS=0` — the plan said none was
needed, but `test_agent_skill_invocation.SequencedLLM` hands out responses by
call index, so an extra `agent_step` call ahead of the skill-selection call
broke those tests; the fixture is the third instance of the existing
isolation pattern and loop tests opt in per test. (2) `select_skills` and
`generate` are separate nodes (plan listed them so) but `generate` also holds
the fixed-prompt fallback. (3) `Answer.run_id` is minted locally rather than
collected (above).

**Related files:** `src/llmwiki/agent/{graph,toolkit,judge,query}.py`,
`src/llmwiki/websearch/{__init__,base,tavily,fake}.py`,
`src/llmwiki/eval/{__init__,dataset,evaluators,run,feedback}.py`,
`src/llmwiki/{tools,config,factory,cli}.py`, `src/llmwiki/api/routes.py`,
`src/llmwiki/models/plan.py`, `src/llmwiki/llm/{routing_config,fake,langchain_client}.py`,
`src/llmwiki/wiki/pages.py`, `src/llmwiki/chains/prompts/{agent_step,judge_answer}.md`,
`config/ops.py`, `scripts/eval_answer.py`, `scripts/README.md`,
`tests/fixtures/eval/answer_quality.jsonl`, `docker-compose.yml`,
`.env.example`, `pyproject.toml`, `requirements.txt`, `uv.lock`, `README.md`,
`CLAUDE.md`, `docs/llmwiki-KB-design_v1.4.md` (1.6), `docs/implement-plan-v1.4.md`
(1.2), `docs/llm-wiki-technical-document.md`, `docs/phase1-testing-guide.md`.

**Test coverage:**
- No regressions: 362 → 444 passed, 1 skipped, 7 deselected; `ruff` (one
  pre-existing E501 in `scripts/browse_vectors.py:135`, untouched) and `mypy`
  clean; `smoke_flow.py --offline` SMOKE PASS; `eval_answer.py --offline` EVAL
  PASS. The four load-bearing tests are untouched; `test_layering.py` gained
  `websearch` and `eval` rows only; `test_routing_config.py`'s drift guard
  scans `agent/graph.py` and `agent/judge.py` too, and its live-providers test
  lists the two new ops.
- Obsolete tests removed: none. Two changed without weakening:
  `test_langchain_client.ScriptedChatModel.invoke` accepts `config` (the real
  `Runnable` signature) and a test asserts the run name;
  `test_config.py::test_configure_langsmith_exports_the_env_vars` now cleans up
  with `os.environ.pop` — its `monkeypatch.delenv` in `finally` *recorded* the
  `true` it deleted and restored it at teardown, so `LANGSMITH_TRACING=true`
  leaked into every later test. Harmless before; once the graph honoured it,
  `Answer.run_id` came back set and LangChain tried to post traces to
  `smith.example`, which hung the suite.
- New: `test_agent_graph.py` (19 — **`test_tool_loop_is_bounded_by_agent_max_
  tool_calls` is the fifth load-bearing test**, added to `CLAUDE.md`; parity at
  `max=0`; early answer; invalid action retried once; repeat refused; shared
  budget; recursion limit; tool citations must resolve; unknown slug / bad
  args as observations; tool results reach generation; empty KB never loops;
  wiki-first precedes tools; web policy off/weak/always/cap/no-searcher; web
  results never citations; `run_id`), `test_agent_toolkit.py` (14),
  `test_websearch.py` (6, incl. lazy import of `langchain_tavily`),
  `test_judge.py` (4), `test_eval.py` (15, incl. `import llmwiki.eval` never
  imports `langsmith`), `test_fake_llm.py` (3), `test_routes.py` (+6),
  `test_config.py` (+2), `test_pages_and_gists.py` (+2 for the YAML fix),
  `tests/integration/test_langsmith_eval.py` (opt-in; needs only
  `LANGSMITH_API_KEY`).

---

## 2026-09-14 — Phase 1 local-LLM routing: distinct `vllm` / `llamacpp` providers

**Goal:** `config/ops.py`'s commented-out local-routing example, and
`.env.example`'s local-LLM note, both pointed a self-hosted vLLM/llama.cpp
server at the shared `"openai"` provider row (`OPENAI_BASE_URL`) — the
2026-09-11 groundwork's own admission that a real cloud OpenAI key and a
local endpoint could never both be active at once. The user asked for a
distinct provider entry per local LLM instead, so `config/ops.py`/`.env`
unambiguously show which server a given op is actually routed to.

**Design-doc note.** This reverses `docs/implement-plan-v1.4.md` §7.4's
recorded decision ("There is no `local` extra... `openai` plus `LLM_BASE_URL`
is one code path fewer than a dedicated adapter"). Per that doc's own
convention, the original reasoning is kept and annotated superseded rather
than deleted — see the new 2026-09-14 addendum right after it.

**Implementation detail:**
- `src/llmwiki/llm/providers.py` — two new `REGISTRY` entries, `"vllm"` and
  `"llamacpp"`, both wrapping the same `ChatOpenAI`/`langchain_openai` class
  `"openai"` already uses (both servers expose an OpenAI-compatible route) —
  no new PyPI dependency, `langchain-openai` is already a core dependency.
  Lazy-import pattern unchanged (`load_class` already imports lazily).
- `src/llmwiki/config.py` — added `"vllm"`, `"llamacpp"` to the `Provider`
  `Literal`.
- `config/providers.py` — `"openai"`'s row is now cloud-OpenAI-only (comment
  updated); two new rows, `"vllm"`/`"llamacpp"`, each with its own
  `api_key_env`/`base_url_env` (`VLLM_API_KEY`/`VLLM_BASE_URL`,
  `LLAMACPP_API_KEY`/`LLAMACPP_BASE_URL`).
- `config/ops.py` — the commented-out local-routing example now names
  `"vllm"`/`"llamacpp"` instead of `"openai"` twice; still commented out, no
  endpoint reachable yet (unchanged from 2026-09-11).
- `.env.example` — new `VLLM_API_KEY`/`VLLM_BASE_URL` and
  `LLAMACPP_API_KEY`/`LLAMACPP_BASE_URL` blocks; provider table updated;
  self-hosted note and the old `OPENAI_BASE_URL` comment rewritten to point
  at the new variables instead of describing a shared slot.
- `docs/implement-plan-v1.4.md` §7.4 — dated addendum (above).

**Related files:** `src/llmwiki/llm/providers.py`, `src/llmwiki/config.py`,
`config/providers.py`, `config/ops.py`, `.env.example`,
`docs/implement-plan-v1.4.md`, `tests/unit/test_providers.py`,
`tests/unit/test_routing_config.py`.

**Test coverage:**
- No regressions: full `pytest` — 362 passed, 1 skipped, 6 deselected;
  `ruff check .` and `mypy` both clean; `python scripts/smoke_flow.py
  --offline` — SMOKE PASS (confirms the new registry entries don't break
  default `Settings()` construction with both `*_BASE_URL`s unset).
- No obsolete tests: additive to the registry, the `"openai"` path is
  unchanged.
- Updated: `tests/unit/test_providers.py::test_the_langchain_providers_are_all_registered`
  now expects `"vllm"`/`"llamacpp"` in the set; the existing parametrized
  tests (`test_registry_keywords_match_the_installed_class`, etc.) already
  iterate `sorted(providers.REGISTRY)` and cover the new entries with no
  changes needed.
- New: `tests/unit/test_providers.py::test_vllm_and_llamacpp_reuse_the_openai_class`
  (same underlying class as `"openai"`, distinct registry key).
  `tests/unit/test_routing_config.py::test_the_tracked_providers_config_gives_vllm_and_llamacpp_their_own_env_vars`
  — loads the real, tracked `config/providers.py` (not a `tmp_path` fixture,
  unlike every other test in that file) with a throwaway `ops.py` routing to
  `vllm`/`llamacpp`/`openai`, and asserts all three resolve simultaneously
  with distinct `api_key`/`base_url` — the actual crux of the ask, and not
  covered by any pre-existing test.

## 2026-09-14 — Post-merge corruption cleanup: duplicated blocks from the `feat/phase1-capture-channels` merge

**Goal:** merging `origin/main` into `feat/phase1-capture-channels` (and a
follow-up "fixup" commit) left several duplicated code blocks behind —
found while trying to run the full test suite after the merge, which
initially failed to even collect.

**Root cause:** a bad `git stash`/merge interaction around commit `89b7eac`
("/ingest cleanup with usage after merge with stash") duplicated several
blocks verbatim: an import block, a function signature's parameter, a
method's body, a whole test function, a Pydantic validator, and two model
field declarations. The "fixup" commit (`c886907`) caught and fixed some of
these (the `test_ingest.py` duplicate `import json`, the duplicated
`_exactly_one_source` validator method in `routes.py`, `ingest.py`'s
duplicate `text` parameter and duplicated if/elif/else body) but not all —
three instances were still live at `HEAD`:
- `src/llmwiki/pipeline/ingest.py` — the `SourceMeta`/`ExtractedDoc` import
  from `llmwiki.models.source` was duplicated verbatim (harmless at runtime,
  but `ruff`'s `F811`/`I001` correctly flag it).
- `tests/unit/test_ingest.py` — `test_capture_requires_exactly_one_input`'s
  body was duplicated inline, breaking the `with` statement's indentation
  (`IndentationError`, blocked `pytest` collection entirely).
- `src/llmwiki/api/routes.py` — `IngestRequest`'s `url`/`text` fields were
  each declared twice (`mypy` `no-redef`), and the class body ran straight
  into the next route decorator with no blank line.

**Implementation detail:** removed the duplicate import block from
`ingest.py`; removed the duplicated body from `test_ingest.py`'s
`test_capture_requires_exactly_one_input`, keeping the single original
implementation; removed `routes.py`'s duplicate `url`/`text` field
declarations and restored the blank line before `@router.get("/healthz")`.

**Related files:** `src/llmwiki/pipeline/ingest.py`,
`tests/unit/test_ingest.py`, `src/llmwiki/api/routes.py`.

**Test coverage:** no tests added or removed — this is a pure dedup of
already-existing code/tests, not a behavior change. `pytest` went from
failing to collect (`IndentationError`) to 362 passed, 1 skipped, 6
deselected; `ruff check .` and `mypy` both went from several errors to clean
(one pre-existing, unrelated `E501` in `scripts/browse_vectors.py` remains —
predates this branch, left untouched); `python scripts/smoke_flow.py
--offline` — SMOKE PASS.

## 2026-09-13 — Technical document §9 rewritten as the Docker / `docker-compose.yml` map

**Goal:** `docs/llm-wiki-technical-document.md` §9 ("Deployment") was four
commands and a paragraph, and already stale — it described the `Dockerfile`
as two-stage (it has been three since the 2026-09-10 dev-mode change) and
led with `docker compose up --build`, which is the one command the VPS
runbook says never to run there. Meanwhile the answers to the questions a
second engineer actually asks about the container setup — which services a
plain `up -d` selects, where `.env` is read, what `target: runtime` names,
why `up -d` alone can run a stale image — lived only in the in-line comments
of `docker-compose.yml` and `Dockerfile` and in two `HISTORY.md` incident
entries. This change consolidates them into one section of the document
whose stated audience is exactly that engineer.

**Implementation detail:** §9 is now "Deployment — Docker and
`docker-compose.yml`", eight subsections, documentation only (no code or
config touched):
- 9.1 the three `Dockerfile` stages (`build`, `dev`, `runtime`) as a table,
  and the two `runtime` decisions that are easy to undo by accident — the
  explicit `COPY skills/` / `COPY config/` (2026-09-10 root cause) and
  `USER llmwiki` uid 10001.
- 9.2 the seven-service map with profile, image name, build target and
  purpose, plus `docker compose [--profile X] config --services` as the way
  to verify selection rather than reason about it; why `dev` has a
  different image name; why `DOCKER_USER` unset falls back to `local/`.
- 9.3 what the profile-less `pull && up -d` runs on the VPS (`init-data`
  then `api`, nothing else, named volume not created), the reason
  `init-data` exists (2026-09-13 `PermissionError`), and a key-by-key table
  of `api`; notes that a site-side file containing only those two services
  (without `build:` and the unused `volumes:`) is a valid production file.
- 9.4 the two ways `.env` is read (Compose interpolation vs `env_file:`),
  the `environment:` > `env_file:` > image `ENV` precedence, `API_PORT`'s
  double duty, shell variables not reaching the container, and the
  `restart` vs `up -d --force-recreate` rule from 2026-09-10.
- 9.5 bind mount vs named volume by prefix, why `api-offline`/`smoke` use
  the named one, and the "empty mount shadows baked-in files" warning for
  the commented `config/` / `skills/` overrides.
- 9.6 build → smoke → push → pull lifecycle; `pull` vs `up -d`'s default
  `missing` pull policy and the `build:` fallback; `--pull always` /
  `pull_policy: always` as the fix; prefer a real `IMAGE_TAG` over `latest`.
- 9.7 the `--profile dev` loop, `required: false` on `dev`'s `env_file`,
  why `pytest` has no `env_file`, and the `LLMWIKI_*_CONFIG` overrides
  `api-offline` needs.
- 9.8 the single-process design paragraph, kept from the old §9.

Also fixed the header table's pointer for the packaging plan's status from
§9 to §10 (it had pointed at Deployment; the "Known Gap" section is §10).

**Related files:** `docs/llm-wiki-technical-document.md`.

**Test coverage:** documentation only — no tests added, removed or
affected. Claims in 9.2/9.3 were checked against `docker compose config
--services` with and without `--profile dev|test|ops`, and `docker compose
config --volumes` (empty without a profile).

---

## 2026-09-13 — `scripts/reset_vectorize.py`: wipe the Vectorize indexes for a fresh start

**Goal:** one command that discards every vector on Cloudflare and leaves the
two indexes empty and correctly set up, without touching `raw/` or `wiki/`.

**Implementation detail:**
- `scripts/reset_vectorize.py` — Vectorize has no delete-all and no listing,
  so the reset is DELETE `/indexes/{name}` then POST `/indexes` at
  `EMBEDDING_DIM`, followed by the same three string metadata indexes
  `bootstrap_indexes.py` creates (`FILTERABLE` duplicated with a keep-in-sync
  note, as `check_cloudflare_setup.py` already does - scripts stay
  standalone). Both steps are asynchronous on Cloudflare's side, so the script
  polls `describe` until the old index is gone and retries the create while
  the name is still being released (120 s budget). Default run is a report
  (`GET /indexes/{name}/info` for the vector count) and changes nothing;
  `--yes` is required to delete; `--index chunks|gists` narrows it. Exits 1
  if a recreated index is missing or mis-dimensioned.
- `scripts/README.md` — table row and a section; also named as the fix for
  `bootstrap_indexes.py --check`'s `DIMENSION MISMATCH`.

- First real run failed after the delete: Cloudflare answers **410 Gone**,
  not 404, for an index that has been deleted (during teardown and after),
  and `describe()` treated only 404 as absent, so the poll loop raised on its
  first check. `describe` and the DELETE now accept both; the same 404-only
  check in `scripts/bootstrap_indexes.py` got the same fix, so `--check`
  reports `MISSING` rather than crashing on a recently deleted index.

**Related files:** `scripts/reset_vectorize.py`, `scripts/bootstrap_indexes.py`,
`scripts/README.md`.

**Test coverage:** scripts are not under the unit suite (they need real
credentials; `scripts/README.md`). Verified by hand: `ruff` and `mypy` clean;
the dry run reported `llmwiki-chunks: dimensions=768, vectors=595` and
`llmwiki-gists: dimensions=768, vectors=116`; the real `--yes` run then
deleted and recreated both (metadata indexes included) and verified them
empty at 768 dimensions; `bootstrap_indexes.py --check` passes afterwards.
Unit suite unaffected: 343 passed, 1 skipped.

---

## 2026-09-13 — source ids are `{hash}-{slug}`: `raw/`, `status/` and `wiki/sources/` become readable

**Goal:** a human browsing the R2 bucket or the Obsidian vault should see
`raw/06e09591603ad558-attention-is-all-you-need/` rather than a bare hex
folder per ingest — without giving up content-addressed dedup or adding a
per-ingest cost that grows with the corpus (design 4.4).

**Root cause (of the unreadability):** the id was the first 16 hex chars of a
SHA-256, and only that, because the hash is what lets capture recognise a
duplicate before it fetches anything or spends a token. It was never random,
but it read as if it were.

**Implementation detail:**
- `storage/layout.py` — `source_id_for(content_hash, title)` mints
  `{hash}-{slug}`; `content_hash_for_bytes` / `content_hash_for_url` replace
  `source_id_for_bytes` / `source_id_for_url` (same digests, honest names).
  Hash first so the folder is prefix-listable by content alone; slug baked
  into the id so every id-only caller (status polling, `GET /sources/{id}`,
  citation resolution, the compiler's `Raw object:` link, `wiki/sources/`)
  already holds the full key and nothing needs a lookup. `SOURCE_SLUG_MAX =
  40` because chunk ids are `{source_id}:{n}` and Vectorize caps a vector id
  at 64 bytes. `_ID_RE` accepts the old bare-hash form too, so existing
  corpora keep resolving; new helpers `content_hash_of`, `raw_prefix_for_hash`,
  `source_id_from_key`, `is_source_id`.
- `pipeline/ingest.py` — the dedup probe is now one prefix list of
  `raw/{hash}` (`_existing_source`) instead of a HEAD on `meta.json`: the slug
  is not known before the fetch (for a URL it comes from the page title) and
  must not matter — the same PDF under a new filename is the same source, and
  the id it was first captured under is the one returned. The id is minted
  after the title is known; `_slug_basis` falls back title → filename stem →
  URL last path segment (`2401-00001` for an arXiv link) → `untitled`, since
  a PDF has no title at capture. Cost: one R2 Class A op ($4.50/M) replaces
  one Class B ($0.36/M) per ingest, against the cents the LLM stage costs.
- `storage/local.py` — `list()` gained S3 prefix semantics for a partial
  segment: `raw/06e0` now walks only the entries of `raw/` whose names start
  with `06e0`, where before a non-directory prefix fell back to `rglob` over
  the whole parent — which would have made the new dedup probe a full scan
  of `raw/` on the local backend.
- `tools.py` — `_looks_like_source_id` delegates to `layout.is_source_id`
  instead of its own 16-hex check, so `get_page` still finds source notes.
- Docs: technical document §3.1 flow and §7 layout; `docs/implement-plan.md`
  key block; `CLAUDE.md` test count.
- `.env.example` — `DOCKER_USER=thomaschoi`, `IMAGE_TAG=0.1.0` as the tracked
  release coordinates (neither is a secret; a shell export still overrides).

**Related files:** `src/llmwiki/storage/layout.py`,
`src/llmwiki/pipeline/ingest.py`, `src/llmwiki/storage/local.py`,
`src/llmwiki/tools.py`, `tests/unit/test_layout.py`,
`tests/unit/test_ingest.py`, `tests/unit/test_local_store.py`,
`tests/unit/test_routes.py`, `docs/llm-wiki-technical-document.md`,
`docs/implement-plan.md`, `CLAUDE.md`, `.env.example`.

**Test coverage:**
- Renamed, not weakened: `test_source_id_is_content_addressed` →
  `test_content_hash_is_content_addressed`; the two URL-canonicalisation tests
  call the renamed helpers. Two `test_routes.py` assertions that pinned
  `len(source_id) == 16` now assert `is_source_id(...)`.
- Added in `test_layout.py`: `test_source_id_is_hash_then_slug_of_the_title`,
  `test_source_slug_is_short_enough_for_a_vectorize_chunk_id`,
  `test_empty_title_still_mints_a_valid_id`, `test_pre_slug_ids_stay_valid`,
  `test_source_id_from_key_reads_either_format`,
  `test_source_id_needs_a_real_hash`; four hostile-id cases added to the
  parametrised rejection test (`-../escape`, upper case, trailing `-`, a
  41-char slug).
- Added in `test_ingest.py`:
  `test_source_id_carries_the_title_slug_after_the_content_hash`,
  `test_a_pdf_upload_takes_its_slug_from_the_filename`,
  `test_a_url_only_pdf_takes_its_slug_from_the_url_tail`,
  `test_dedup_ignores_the_slug` (same bytes, different filename → first id
  wins), `test_a_source_captured_under_the_bare_hash_id_is_still_a_duplicate`
  (a pre-2026-09-13 `raw/{hash}/` folder short-circuits capture and no
  second folder appears).
- Added in `test_local_store.py`:
  `test_list_treats_the_prefix_as_a_string_not_a_folder` and
  `test_partial_prefix_list_does_not_walk_sibling_folders` (spies on
  `Path.rglob`: the probe walks exactly one folder out of twenty).
- The four load-bearing tests are untouched and pass; `test_every_citation_
  resolves_to_a_real_raw_object` exercises the new ids end to end.
- Full gate: 343 passed, 1 skipped; mypy clean (56 files); ruff down to the
  pre-existing `scripts/browse_vectors.py:135`; `scripts/smoke_flow.py
  --offline` PASS.

---

## 2026-09-13 — `init-data` one-shot hands `./.data` to the runtime uid before `api` starts

**Goal:** a deploy directory holding only `docker-compose.yml` and `.env` must
come up writable with `docker compose up -d` alone — no `chown` by hand on the
box.

**Root cause:** the runtime image is deliberately non-root (`USER llmwiki`,
uid 10001), but the bind-mount source `./.data` is created by the Docker
*daemon* when it does not exist at `up` time — as `root:root 0755`. Found on
the staging deploy dir: `.data/` root-owned and empty, and a probe run of the
image confirmed `touch: cannot touch '/data/probe': Permission denied`. The
container starts, `/healthz` passes (it makes no writes), and the first ingest
would fail with `PermissionError`. The same latent state existed on the dev
box in the other direction: `.data/` there is `1000:1000` for the `dev`
service, so the `api` service — advertised as sharing "one corpus" with it —
could not write either.

**Implementation detail:**
- `docker-compose.yml` — new `init-data` service: same image as `api` (so
  `pull` fetches nothing extra and, like `lint`, it has no `build:`), runs as
  `root`, mounts `./.data`, and runs one `find /data ! -user … -o ! -group …
  -exec chown` — one stat pass over the corpus, writes only where ownership is
  wrong, then exits. `api` and `lint` gain `depends_on: init-data:
  condition: service_completed_successfully`, so `up`, `run` and cron all go
  through it. Both also gain `user: "${API_UID:-10001}:${API_GID:-10001}"`;
  the default is the image's own user so a deploy box sets nothing, and a dev
  box can set both to `DEV_UID`/`DEV_GID` so `api` and `dev` genuinely share
  `./.data`.
- `.env.example` — `API_UID` / `API_GID` documented next to the Docker Hub
  block.
- `docs/deployment-plan-container-hosting.md` step 11 — why `.data/` needs no
  preparation and why an `Exited (0)` init-data in `ps -a` is normal.
- The dev `Dockerfile` stage is untouched: it already runs as the host uid and
  has no fixed user to conflict with.

**Related files:** `docker-compose.yml`, `.env.example`,
`docs/deployment-plan-container-hosting.md`.

**Test coverage:** no Python code changed; the unit suite is unaffected and
was not the gate here. Verified by hand against the real image
(`thomaschoi/llmwiki:0.1.0`):
- Fresh deploy-dir simulation (compose file + `.env` only, no `.data/`):
  `docker compose run --rm --entrypoint sh api -c 'id; touch /data/probe'` —
  init-data ran and exited 0, the daemon-created `.data/` came out
  `10001:10001`, and the write succeeded as `uid=10001(llmwiki)`. Before the
  change the identical probe was `Permission denied`.
- Dev-box path: `API_UID=1000 API_GID=1000` against the existing `1000:1000`
  corpus — no chown performed, write succeeded as `uid=1000`, and
  `from llmwiki.api.app import app` imports fine under a uid with no passwd
  entry.
- `docker compose config --quiet` passes; the `$$API_UID` escapes reach the
  container shell as `$API_UID` (the chown above proves it).

---

## 2026-09-10 — the auth DEBUG line masks the bearer token instead of printing it

**Goal:** keep the `require_token` debug line useful for verifying which token
a deployment is actually holding, without putting the token itself in the log.

**Root cause:** the line added while debugging the Hostinger deploy logged both
values in full — `require_token: expected=%s, supplied=%s` with the raw
strings. `INGEST_API_TOKEN` is stored as a `SecretStr` precisely so it does not
appear in a repr; formatting `.get_secret_value()` into a log message walks
around that. DEBUG is also the level a deployment turns on *when auth is
misbehaving*, so it is the log most likely to be pasted into a ticket.

**Implementation detail:**
- `src/llmwiki/api/routes.py` — new `mask()` renders `first5...last5 (N chars)`.
  The length matters as much as the ends: a trailing newline or a shell-quoted
  value is the usual cause of a token that looks right and fails, and it shows
  up as a length that is one off.
- Guarded by `MIN_MASKABLE_TOKEN_CHARS = 16`: below that, first-5 + last-5 is
  most of the secret and at exactly 10 characters it *is* the secret, so short
  tokens render as `<N chars, too short to show safely>` — which still answers
  "is the container holding `changeme`?".
- `supplied` is now computed before the log line rather than after, so both
  sides go through the same masking and the line no longer reaches into
  `credentials` a second time.
- Moved `import logging` into the stdlib import group, clearing the two ruff
  errors this line carried (E501, I001). `ruff check .` is now down to one
  error, `scripts/browse_vectors.py:135` (E501), which predates this work and
  is present at HEAD.

**Related files:** `src/llmwiki/api/routes.py`, `tests/unit/test_routes.py`.

**Test coverage:** three tests added to `tests/unit/test_routes.py` —
`test_a_masked_token_shows_its_ends_but_never_the_middle`,
`test_a_token_too_short_to_mask_shows_only_its_length` (both branches of the
length guard, plus the empty case), and
`test_debug_logging_never_writes_the_bearer_token`, a caplog guard that runs an
authenticated request at DEBUG and asserts the line still fires while the token
does not appear anywhere in the records — verified to fail against the
unmasked version. Full gate: 326 passed, 1 skipped; mypy clean (56 files).

---

## 2026-09-10 — a binary body posted to a JSON route returned 500, not 422

**Goal:** a client sending the wrong thing must get a usable 4xx, never a
server error. Reported from the deployed container: `POST /ingest` with a PDF
as `multipart/form-data` produced `UnicodeDecodeError: 'utf-8' codec can't
decode byte 0xbf in position 166` and a full traceback in the logs, with the
raw PDF echoed into them.

**Root cause** — entirely inside FastAPI's own error path, reproduced before
changing anything (`fastapi/routing.py:440-454`, `fastapi/encoders.py:85`):

1. The request body's content type was not JSON, so FastAPI never parsed it —
   `body = body_bytes`, the raw multipart bytes, PDF and all.
2. `IngestRequest` validation then failed correctly ("Input should be a valid
   dictionary"), and the `RequestValidationError` carried those bytes as its
   `input`.
3. FastAPI's stock `request_validation_exception_handler` encodes the errors
   with `jsonable_encoder`, whose bytes rule is `lambda o: o.decode()` — no
   error handling. Binary bytes raise **inside the exception handler**, so the
   422 never gets built and the request dies as a 500.

So the caller's mistake (a file belongs at `POST /upload`; `/ingest` reads
JSON) was real, but the 500 was ours: the validation error was unrenderable.

**Implementation detail:**
- `src/llmwiki/api/app.py` — `_validation_error` replaces the stock 422
  handler, encoding with `custom_encoder={bytes: _as_text}`. `_as_text`
  decodes when it can and otherwise reports `<N bytes of non-UTF-8 data>`;
  either way it truncates at `MAX_ECHOED_BODY_CHARS` (500), because quoting the
  rejected input is a debugging aid for a small JSON body and an amplifier for
  a multi-megabyte upload — the unpatched handler echoed a 200 KB body back in
  full (measured: 200,147 bytes; now 124).
- The same handler adds a `hint` naming `/upload` when the content type is
  multipart, since that is the mistake that produced this report.
- `README.md` — working `curl` for both forms. There was no `curl -F` recipe
  anywhere in the repository, which is a fair part of why `/ingest` looked like
  the place to send a file.

**Not changed:** `/ingest` does *not* learn to accept multipart. One endpoint
per body shape is what keeps `routes.py` free of content-type branching; the
fix is a clear 422 that names the right endpoint.

**Related files:** `src/llmwiki/api/app.py`, `tests/unit/test_routes.py`,
`README.md`.

**Test coverage:** two tests added to `tests/unit/test_routes.py`, both
verified to fail with the handler removed and pass with it —
`test_a_binary_body_posted_to_a_json_route_is_422_not_500` (the exact reported
request: a PDF containing `0xbf` posted as multipart; asserts 422, the
non-UTF-8 descriptor, and the `/upload` hint) and
`test_a_huge_rejected_body_is_not_echoed_back_in_full` (200 KB body under a
content type FastAPI does not parse → response under 2 KB). The existing
`test_ingest_rejects_a_malformed_body` and
`test_ingest_rejects_a_body_naming_both_a_url_and_text` confirm ordinary JSON
422s still echo their input unchanged. Full gate: 323 passed, 1 skipped; mypy
clean (56 files); `scripts/smoke_flow.py --offline` SMOKE PASS.

---

## 2026-09-10 — `config/*.py.example` deleted, now that the real files are tracked

**Goal:** remove `config/providers.py.example` and `config/ops.py.example`.
With the real `config/providers.py` and `config/ops.py` tracked in git (entry
above), the `.example` pair was a second copy of the same two tables, free to
drift out of step with the ones that actually run - the `cp X.example X`
pattern they existed to support no longer exists.

**Checked before deleting, not after:**
- *No content is lost.* Every provider row the example carried commented-out
  (openai, google, nvidia, deepseek, openrouter, fake) is present and
  uncommented in the real `config/providers.py`, so it is now its own
  reference. That works precisely because a provider whose `api_key_env` is
  unset is silently inactive rather than an error - listing all seven costs
  nothing. `config/ops.py` has the same five op rows the example had.
- *Every live reference was updated* (`grep` for both filenames across `*.py`,
  `*.md`, `*.yml`, `*.toml`).

**Implementation detail:**
- Deleted `config/providers.py.example`, `config/ops.py.example` (`git rm`).
- `config/providers.py`, `config/ops.py` — docstrings no longer point at a
  sibling that is gone; each records the deletion and why.
- `docs/llm-wiki-technical-document.md` — three live instructions repaired:
  §5.1 step 5 ("add a commented-out row to `providers.py.example`" → add a row
  to `config/providers.py`, uncommented, since an unset key makes it inactive
  anyway); §5.3 step 2 (same substitution for `ops.py`); §5.6's
  `cp X.example X` block (replaced with "tracked and baked into the image,
  edit in place and rebuild").
- `docs/llm-wiki-technical-document.md` §5.6 — one bullet was left factually
  **wrong** by the previous change and is corrected here: "Presence of both
  files is the switch. *Absent (the default, a fresh clone)*" is no longer
  true. A fresh clone is now in routed mode, and reaching the fallback takes a
  *nonexistent* path rather than an unset variable. The adjacent "Gotcha"
  bullet gained the routing-before-`LLM_BACKEND` ordering and the `api-offline`
  precedent, since that is the form this trap now takes.
- Historical references in `HISTORY.md` and `docs/HISTORY.md` were left alone:
  they describe what was true when written, which is what a history is for.

**Related files:** `config/providers.py`, `config/ops.py`,
`docs/llm-wiki-technical-document.md`; deleted `config/providers.py.example`,
`config/ops.py.example`.

**Test coverage:** no tests added or removed - nothing imports the `.example`
files (they were never importable config; only `config/providers.py` and
`config/ops.py` are read, by path, at startup). Full suite green, `ruff` and
`mypy` clean on the changed files, both smokes pass, and the image was rebuilt
and re-checked to confirm `config/` still arrives with both real files.

---

## 2026-09-10 — `config/providers.py` + `config/ops.py` tracked in git and baked into the image

**Goal:** replace the bind-mount fix from the previous entry. Thomas's
observation: these two files hold no secrets - they name *which env var*
carries each provider's key, and the values live in `.env` - so they belong in
the repository and in the image, not scp'd onto each box by hand.

Correct, and it makes the image self-contained: `docker compose pull` now
brings the routing table with it, and the VPS is back to needing exactly two
files (`docker-compose.yml`, `.env`). It also removes a trap the bind-mount fix
had introduced - Compose auto-creates a missing host directory as an empty one,
and an empty mount over `/app/config` shadows whatever the image ships, which
would have silently restored the single-provider fallback on any box that
forgot the scp.

**Implementation detail:**
- `config/providers.py`, `config/ops.py` — now tracked (created by Thomas
  earlier the same day; five ops all routed to openrouter/z-ai). Only their
  module docstrings were edited here: both were copies of the `.example`
  headers and still said "copy this file to ... to activate", which is no
  longer how they get there. The `.example` files stay as the annotated
  full-option reference.
- `Dockerfile` — `COPY config/ ./config/` next to the `skills/` copy.
- `docker-compose.yml` — the `./config:/app/config:ro` mounts added earlier on
  `api` and `lint` are now **commented out**, with the shadowing hazard spelled
  out at the comment. They remain as the documented override for a deployment
  that must diverge from the image's table without a rebuild.
- `docker-compose.yml` — `api-offline` gained
  `LLMWIKI_PROVIDERS_CONFIG=/nonexistent/providers.py` and the matching
  `LLMWIKI_OPS_CONFIG`. **Not optional.** `factory._build_llm_client` consults
  the routing table *first* and returns before the `"fake"` branch is reached,
  so with the table in the image `LLM_BACKEND=fake` stopped selecting the
  offline double. Reproduced before fixing: the service died at startup with
  `op 'summarize_source' routes to provider 'openrouter', which is not active`,
  because an offline box has no `OPENROUTER_API_KEY`. Only a path that *cannot
  exist* re-selects the fallback - unsetting the variable falls back to the
  `./config` default, which is exactly what is now present.
- `docs/deployment-plan-container-hosting.md` — Phase 2 step 4 and Phase 3
  step 9 rewritten again (step 9 is back to "exactly two files", with the
  history of why it moved); Phase 3b cause 5 updated with the final fix and the
  routing-before-LLM_BACKEND ordering.
- `.env.example` — records that a *nonexistent* path is the documented way to
  force the fallback, since an unset variable does not.

**Related files:** `config/providers.py`, `config/ops.py`, `Dockerfile`,
`docker-compose.yml`, `docs/deployment-plan-container-hosting.md`,
`.env.example`.

**Test coverage:** no new automated tests. The behaviour that changed is which
files an *image* contains, which the unit suite cannot see; the existing
`test_healthz_reports_whether_the_outside_package_config_was_found` already
guards the observability half. Verified by hand against the rebuilt image:
- `docker run --rm $IMAGE` → `llm_routing: "per-op table"`, both config paths
  `present: true`, `skills_dir.skill_files: 2`. Before this change the same
  command reported the fallback with both absent.
- `docker compose --profile offline run --rm api-offline` → builds `FakeLLM`,
  confirming the nonexistent-path override restores offline mode.
- `docker compose --profile test run --rm smoke` → `SMOKE PASS`.
- Host-side `pytest` (321 passed) and `scripts/smoke_flow.py --offline`
  (`SMOKE PASS`) with the real config files present - both construct explicit
  `Settings`, so neither was disturbed by the repo defaulting to routed mode.
  This was checked *because* `providers.py.example` warns that it would be.

---

## 2026-09-10 — Root cause: the deployed container had no `config/` or `skills/` at all

**Goal:** close out the Hostinger deploy problem. The earlier entry covered the
Compose env-plumbing mechanics; inspecting the running container showed the
environment was in fact arriving, and that something else was wrong.

**Root cause:** three settings point at paths *outside* the installed package,
and nothing put those paths inside the container. In the running container
`/app` held only `scripts/` and `tests/fixtures/`; the package itself ran from
`/opt/venv/lib/python3.11/site-packages/llmwiki`; and the deploy directory
`/docker/llmwiki` held only `.env`, `.data/` and `docker-compose.yml`.

| setting | default | resolved in container (WORKDIR `/app`) | was |
|---|---|---|---|
| `LLMWIKI_PROVIDERS_CONFIG` | `./config/providers.py` | `/app/config/providers.py` | absent |
| `LLMWIKI_OPS_CONFIG` | `./config/ops.py` | `/app/config/ops.py` | absent |
| `AGENT_SKILLS_DIR` | `./skills` | `/app/skills` | absent |

`chains/prompts/*.md` survives a `pip install` because it is declared as
package data in `pyproject.toml`; these three are deliberately *not* package
data (design v1.4 4.8/4.8.2 puts them outside the package so an operator can
edit them), so `pip install --no-deps .` in the build stage never carried them.

What made it invisible rather than fatal: absence of each is a **documented,
legitimate configuration** - `routing_config.load_routing_config` returns
``None`` for "neither file exists" (single-provider fallback) and
`discover_skills` returns ``{}`` for an absent directory (fixed answer_query
prompt). Both are deliberate and both are correct behaviour for a fresh
install. So the service was healthy, answered questions, logged no error, and
silently used none of the routing table or skills the operator believed were in
force. Relative paths made it worse: `./config/providers.py` means different
things depending on the working directory, and nothing ever printed the
resolved one.

**Implementation detail:**
- `Dockerfile` (runtime stage) — `COPY skills/ ./skills/`. `skills/` is tracked
  application content (`skills/answer_query.md`, `skills/compare_concepts.md`)
  and should travel with every image pull, exactly like the package's own
  prompts. `config/` is deliberately *not* copied: git tracks only
  `providers.py.example` / `ops.py.example`, and the real files are
  per-deployment.
- `docker-compose.yml` — `api` and `lint` bind-mount `./config:/app/config:ro`,
  so the routing table travels with `.env` into the deploy directory. Read-only
  because the app only imports it. An absent `./config` is auto-created empty by
  Compose, which lands in the documented fallback rather than an error - the
  point of the health change below is that this is now *visible* instead of
  silent. A commented-out `./skills:/app/skills:ro` override is included for
  adding a skill without a rebuild.
- `src/llmwiki/tools.py` — `health()` gained a `config` block:
  `llm_routing` (`"per-op table"` / `"single-provider fallback"`) plus each of
  the three paths `resolve()`d with a `present` flag and, for skills, a file
  count. Deliberately *not* fixed by making absence an error: `.env.example`
  ships all three variables set while the repo ships only `.example` config
  files, so erroring would break every fresh install. The fix for a silent
  legitimate fallback is to make the active mode observable, not to forbid it.
  The count uses `glob("*.md")` rather than `discover_skills()` because
  `/healthz` is polled by the Docker HEALTHCHECK every 30s and re-parsing every
  skill file (with a warning per malformed one) on each poll is not free.
- `docs/deployment-plan-container-hosting.md` — Phase 2 step 4 and Phase 3
  step 9 corrected. Step 9's "the box only needs two files" was simply wrong,
  and step 4 claimed `config/ops.py` and `config/providers.py` were "confirmed
  present as untracked files in this repo already" - they are not present, only
  the `.example` files are, so this checkout has been in fallback mode all
  along. Phase 3b gained cause 5 with the resolved-path table and the
  `curl /healthz | jq .config` check.
- `.env.example` — the relative-path trap spelled out at all three variables:
  `./config/...` is `/app/config/...` *inside* the container, not next to
  `docker-compose.yml` on the host.

**Related files:** `Dockerfile`, `docker-compose.yml`, `src/llmwiki/tools.py`,
`docs/deployment-plan-container-hosting.md`, `.env.example`,
`tests/unit/test_routes.py`.

**Test coverage:**
- **Added** `tests/unit/test_routes.py::test_healthz_reports_whether_the_
  outside_package_config_was_found` — asserts the `config` block names a
  routing mode and reports all three paths resolved (absolute) with boolean
  `present` flags. Regression guard for the whole episode: the defect was not
  that a fallback happened, it was that nothing could tell you it had.
- No tests removed or made obsolete; the two existing `/healthz` tests assert
  individual keys, so the added block did not disturb them.
- Full suite green (321 passed, 1 skipped), `ruff` clean on the changed files,
  `mypy` clean. Verified against the rebuilt production image:
  - `docker run --rm $IMAGE` → `skills_dir: {present: true, skill_files: 2}`
    (it was `present: false` before this change) and
    `llm_routing: "single-provider fallback"` with both config paths absent.
  - `docker run --rm -v <dir>:/app/config:ro $IMAGE` with the two `.example`
    files copied in → `llm_routing: "per-op table"`, both `present: true`,
    confirming the compose mount flips the mode. Verified against a scratch
    directory rather than by creating real `config/*.py` in the working tree,
    which would have silently switched this checkout into routed mode.

---

## 2026-09-10 — Env changes on the VPS were silently not applied; `/healthz` now echoes `log_level`

**Goal:** first Hostinger deploy: `LOG_LEVEL=DEBUG` set by hand in `.env` on
the box had no effect on the running container, and nothing on the box said
why.

**Root cause:** not application code — `api/app.py` calls
`configure_logging(default_settings.log_level)` at import, and pydantic-settings
reads real environment variables ahead of the `.env` file, both of which were
verified working inside a container. The environment simply never reached the
container. Four distinct Compose mechanics can cause that; all four were
reproduced locally against Compose v5.5.1 with a throwaway `alpine` service
that printed its own `$LOG_LEVEL`:

1. **`compose restart` does not re-read `.env`** (the likely culprit here). A
   container's environment is fixed at *create* time. With `.env` edited from
   INFO to DEBUG: `restart` → still INFO; `up -d` → DEBUG. Older Compose v2
   releases additionally failed to recreate on an `env_file` content change and
   reported "up-to-date", hence the `--force-recreate` recommendation.
2. **An exported shell variable does not reach the container.**
   `LOG_LEVEL=TRACE docker compose up -d` fed Compose's `${...}` interpolation
   only; the container still showed the `.env` value. A container gets a
   variable from `env_file:` or `environment:`, nothing else.
3. **A missing `.env` was silent.** With Compose's default
   `required: false`, an absent file started the container with an *empty*
   environment and no warning at all — indistinguishable from a working
   deployment until a backend call failed deep in a request.
4. **`environment:` outranks `env_file:`**, so the keys pinned there
   (`LOCAL_STORAGE_PATH`, `API_HOST`, `API_PORT`) can never be changed by
   editing `.env` on the box.

**Implementation detail:**
- `src/llmwiki/tools.py` — `health()` gained a top-level `log_level` field. A
  deployed container's effective configuration was previously unobservable from
  outside the box; now one unauthenticated `curl /healthz` distinguishes "the
  .env edit was applied" from "the container is still running the old
  environment". Not a secret, and it doubles as an image-version probe: the
  field only exists in images built after this change.
- `docker-compose.yml` — `api` and `lint` now declare `env_file: required:
  true`. Deliberately not blanket-applied: `dev` keeps `required: false` (a
  local convenience), and the offline/test profiles carry no `env_file` at all,
  so the fresh-clone/no-credentials promise in the file header still holds.
  Verified before committing to it: an *unselected* service's missing env_file
  does not break other profiles (`--profile offline` still starts, `config -q`
  still passes), and `ps`/`down` keep working when the file is missing — only
  `up` refuses, printing the absolute path it wanted. So this cannot strand a
  running deployment.
- `docker-compose.yml` — header comment now states the restart-vs-`up -d` rule
  and that a shell variable is not passed through; the `api` service's
  `environment:` block is labelled as overriding `.env`.
- `docs/deployment-plan-container-hosting.md` — new "Phase 3b — When an `.env`
  change doesn't take effect on the box", with the `docker compose exec api
  env` / `curl /healthz` diagnostic first and the four causes under it, plus a
  reminder that a *code* change needs a rebuilt and pushed image, since the box
  never builds.

**Related files:** `src/llmwiki/tools.py`, `docker-compose.yml`,
`docs/deployment-plan-container-hosting.md`, `tests/unit/test_routes.py`.

**Test coverage:**
- **Added** `tests/unit/test_routes.py::test_healthz_echoes_the_effective_log_
  level` — asserts the new field is present and reports the settings value.
  This is the regression guard for the whole episode: the symptom was that a
  container's effective configuration was invisible from outside.
- No tests removed or made obsolete. `test_healthz_needs_no_token_and_reports_
  backends` asserts individual keys rather than the whole dict, so the added
  field did not disturb it.
- Full suite green, plus `ruff` and `mypy`. Verified against the real
  production image after `docker compose build api`: with no environment
  `health()['log_level']` is `INFO`; with `-e LOG_LEVEL=DEBUG` it is `DEBUG`.
  (The first attempt returned `KeyError: 'log_level'` from the *pre-build*
  image — a live demonstration of the "a code change needs a new image" point
  now written into Phase 3b.)

---

## 2026-09-10 — Dev-mode container: bind-mounted source, no rebuild per edit

**Goal:** iterate on the code inside Docker without `docker compose build`
between changes. The existing services all bake the source into the image
(`pip install --no-deps .` in the build stage), so every one-line edit cost a
full image rebuild — usable for deployment, unusable as a development loop.

**Implementation detail:**
- `Dockerfile` — new `dev` stage, placed **between** `build` and `runtime` so
  the last stage stays `runtime` and a bare `docker build .` still produces the
  deployable image. It reuses the same `/opt/venv` from the `build` stage
  (`COPY --from=build`), then installs the project *editable*
  (`pip install --no-deps -e .`). setuptools writes the path hook into
  `/opt/venv`, outside `/app`, so it survives the bind mount and resolves
  `llmwiki` to `/app/src` at import time — including modules added after the
  build. `CMD` is uvicorn with `--reload --reload-dir /app/src`; `watchfiles`
  was already pinned in `requirements.txt`, so this is a real inotify watcher
  rather than uvicorn's stat-polling fallback. `--reload-dir` is narrowed to
  `src/` because the mount also exposes `.git/`, `.data/` and `.venv/`, and a
  compile writing into `.data/` would otherwise restart the server it is
  running under. No `USER` line, unlike `runtime` — see the uid note below.
- `docker-compose.yml` — new `dev` service under `profiles: ["dev"]`:
  - Mounts the **whole tree** (`.:/app`), not just `src/`. `tests/`,
    `scripts/`, `skills/` (AGENT_SKILLS_DIR) and `config/` (providers/ops
    tables) are all read at run time and all want to be live-editable.
  - `image: local/llmwiki-dev:latest` — deliberately *not* the
    `${DOCKER_USER}/llmwiki` name the other four services share. This image
    contains an editable install pointing at a bind mount; under the shared
    name a `docker compose push` would ship it as the production image.
  - `user: "${DEV_UID:-1000}:${DEV_GID:-1000}"`. With a bind mount, anything
    the container writes (`./.data`, `.pytest_cache/`) lands on the host with
    the container's uid; running as root would leave root-owned files in the
    developer's tree. The `runtime` image's fixed uid 10001 is wrong here for
    the same reason, hence the separate stage.
  - Host port `${DEV_PORT:-8011}`, distinct from the api service's 8010, so a
    dev container and a production-image container can run side by side
    against the same `./.data`.
  - No `restart:` policy: in dev a crash should stay down and visible.
- `docker-compose.yml` — new `pytest` service (`profiles: ["test"]`, same dev
  image, `entrypoint: ["pytest"]`). It exists separately because it must **not**
  have the `dev` service's `env_file: .env`: Compose's `env_file` promotes .env
  entries to real environment variables, and `tests/unit/test_config.py` asserts
  on defaults through `Settings(_env_file=None)`, which ignores the .env *file*
  but still reads ambient env. Running the suite inside `dev` failed four
  config tests purely because the developer's real `LLM_PROVIDER=openrouter`
  was visible; with the clean-env service the same suite passes. Unit tests
  need no credentials by rule (`CLAUDE.md`), so dropping `.env` costs nothing.
- `.env.example` — new "Dev container" block for `DEV_PORT`, `DEV_UID`,
  `DEV_GID`. Like `DOCKER_USER`/`IMAGE_TAG` these are read by Compose
  interpolation only, never by `src/llmwiki`.
- `README.md` — "Dev mode (no rebuild after a code change)" subsection under
  Docker; `docker-compose.yml`'s header comment now points at it, so the
  build/push/pull deploy loop and the dev loop are distinguishable in the file
  itself.

**Related files:** `Dockerfile`, `docker-compose.yml`, `.env.example`,
`README.md`.

**Test coverage:** no new automated tests — this is container plumbing with no
importable surface, and the existing suite is the thing being run *inside* it.
No tests were removed or made obsolete. Verified by hand end to end:
- `docker compose --profile dev build dev` → image built.
- `docker compose --profile dev up -d dev` → `GET :8011/healthz` returns 200
  with the real backends from `.env` (r2/vectorize/workers_ai/openrouter).
- **The load-bearing check:** edited `src/llmwiki/api/routes.py::healthz` on the
  host while the container ran, waited ~8s, and the new field appeared in the
  live `:8011/healthz` response with no rebuild; reverting the edit reverted the
  response. Logs show WatchFiles triggering the reload.
- `docker compose --profile dev run --rm dev python scripts/smoke_flow.py
  --offline` → `SMOKE PASS`.
- `docker compose --profile test run --rm pytest -q` → 316 passed, 4 skipped,
  6 deselected.
- `docker compose --profile dev run --rm dev llmwiki --help` → the console
  script resolves through the editable install.
- `find . -newermt '-30 minutes' ! -user thomas` → nothing, confirming the
  uid mapping keeps the tree free of root-owned files.

---

## 2026-09-10 — `docker-compose.yml` images resolve to Docker Hub (`$DOCKER_USER`)

**Goal:** `docs/deployment-plan-container-hosting.md` §8's one required code
change, ahead of the Hostinger deploy: every service named the image
`llmwiki:latest`, a bare local tag that `docker compose push` cannot push and
`docker compose pull` cannot pull. The recommended deployment builds and
pushes from the dev machine and only ever pulls on the VPS, so the image needs
a real Docker Hub repo path.

**Implementation detail:**
- `docker-compose.yml` — all four services (`api`, `api-offline`, `smoke`,
  `lint`) now use `image: ${DOCKER_USER:-local}/llmwiki:${IMAGE_TAG:-latest}`.
  Three choices worth recording:
  - *All four, not just the two the plan named.* `api`/`lint` are what deploy,
    but if `smoke` kept a different name it would build and test a **second**
    image, quietly defeating the pre-push gate that the same plan (§9.3) makes
    the only check standing between a local build and Docker Hub.
  - *Interpolated, not hardcoded.* `DOCKER_USER` is already exported in this
    machine's shell (`~/.bashrc`), and baking one account name into a
    committed file makes the repo awkward to fork. The trap this creates is
    documented in the file's header: Compose interpolation reads the shell
    environment and the top-level `./.env` — it does **not** read a service's
    `env_file:` — so the VPS's `.env` must carry `DOCKER_USER` too, even
    though the application itself never reads it.
  - *Fallback `local/`, not the bare name.* An unset `DOCKER_USER` still
    builds and runs in a fresh clone (preserving the offline/test profiles'
    no-config promise), but `local/llmwiki` is an unpushable namespace, so a
    misconfigured `pull` fails loudly instead of silently fetching some
    stranger's `llmwiki` from Docker Hub.
  - The header comment gained the full build → smoke → push → pull sequence,
    so the file explains its own deploy role without the plan doc open.
- `.env.example` — new "Docker Hub" block documenting `DOCKER_USER` and
  `IMAGE_TAG`. Neither is secret and neither is read by `src/llmwiki`, but
  they are read from the environment by the deployment, which is what
  `CLAUDE.md`'s keep-`.env.example`-in-sync rule is protecting.
- `docs/deployment-plan-container-hosting.md` — status line, §1, §6 (steps 6,
  8, 9) and §8 updated from "will need" to what was actually done: `docker
  compose build api` / `docker compose push api` replace the hand-written
  `docker build -t <dockerhub-user>/...` commands, and §8's "no `.env.example`
  changes needed" bullet was wrong and is now the `.env.example` entry above.
  The rest of the plan (VPS, Tunnel, Access policy) remains unexecuted.
- No `src/llmwiki`, `Dockerfile`, or `.env` changes. The container's internal
  port stays 8000; only image *naming* changed.

**Related files:** `docker-compose.yml`, `.env.example`,
`docs/deployment-plan-container-hosting.md`.

**Test coverage:**
- No regressions: `pytest` — 319 passed, 1 skipped, 6 deselected. `mypy` clean
  (56 source files). No Python changed, so this was confirmation, not risk.
- No new or obsolete unit tests: Compose file naming is not reachable from
  pytest, and nothing existing became inapplicable.
- The real gate is the deploy-time one the plan already specifies (§9.3), run
  here for the first time: `docker compose --profile test run --rm --build
  smoke` built `thomaschoi/llmwiki:latest` (634MB) and printed `SMOKE PASS`
  through all seven steps (ingest → pipeline → wiki → search → citations →
  cost ledger → lint). Verified both interpolation branches with `docker
  compose config --images`: `thomaschoi/llmwiki:latest` with `DOCKER_USER`
  set, `local/llmwiki:latest` with it unset, and `IMAGE_TAG=2026.09.10`
  overriding the tag.
- `docker compose push` was **not** run — pushing publishes to Docker Hub and
  is the operator's call, not part of this change.
- Two environment findings, both pre-existing and neither caused by this
  change: (1) `.venv` had no `llmwiki` installed, so `pytest` failed at
  import until `uv pip install -e ".[dev]"` was re-run; (2) the default buildx
  builder `mybuilder` (docker-container driver) cannot boot on this host —
  `open /run/nvidia-persistenced/socket: no such file or directory` — so the
  smoke build needed `BUILDX_BUILDER=default`. Worth knowing before the first
  real push. Separately, `ruff check .` reports one E501 in
  `scripts/browse_vectors.py:135`, committed in 4ab8b96 and untouched here;
  left alone rather than folded into a deployment change.

---

## 2026-09-11 — Phase 1, part 1: Telegram + email capture channels, local-LLM routing docs

**Goal:** land the first three of Phase 1's four workstreams from
`docs/llmwiki-KB-design.md` §5 (see the approved plan at
`~/.claude/plans/we-can-start-to-functional-goblet.md`): a Telegram capture
channel (webhook mode, in-process with FastAPI), an email capture channel
(provider webhook, Mailgun inbound-parse convention), and the documentation/
config groundwork for routing bulk LLM ops to a local vLLM/llama.cpp endpoint
on an RTX 3090 host. The fourth workstream (LangGraph query flow + lean
LangSmith eval) is deferred to a follow-up entry.

**Implementation detail:**
- New `src/llmwiki/channels/` package — a new L4 transport layer, peer to
  `api/`/`mcp/`. `channels/telegram.py`: `build_router(cfg) -> APIRouter |
  None`, mounted only when `TELEGRAM_BOT_TOKEN` is set. `POST
  /channels/telegram/webhook` verifies Telegram's `X-Telegram-Bot-Api-Secret-
  Token` header (constant-time compare against `TELEGRAM_WEBHOOK_SECRET`),
  then maps an `Update` onto `tools.ingest_source(...)`: plain text → `text=`,
  a lone bare URL → `url=` (modality auto-detected downstream, including
  YouTube), a forwarded document/photo → downloaded via the Bot API's
  `getFile` and passed as `file=`. Uses raw `httpx` (already a core
  dependency), not a Telegram SDK, since webhook mode never needs polling
  machinery. `channels/email.py`: same `build_router` shape, mounted when
  `MAILGUN_SIGNING_KEY` is set; `POST /channels/email/inbound` verifies
  Mailgun's HMAC-SHA256 signature over `timestamp+token`, then ingests each
  attachment as its own source plus the body (as a URL or as text) whenever
  it isn't just a caption for the attachment(s).
- `src/llmwiki/config.py` — three new optional `Settings` fields:
  `telegram_bot_token`, `telegram_webhook_secret`, `mailgun_signing_key`
  (all `SecretStr`, default empty — absence disables the channel).
- `src/llmwiki/api/app.py` — `create_app()` now also calls
  `_mount_channels(app)`, which builds and conditionally includes both
  channel routers, re-reading `llmwiki.config.settings` at call time (not a
  module-level snapshot) so a test that monkeypatches `config.settings`
  before calling `create_app()` again sees its own channel configuration.
- Local LLM (RTX 3090, vLLM primary / llama.cpp fallback): confirmed this
  needs **zero new adapter code** — `config/providers.py`'s existing `openai`
  row is the mechanism (`OPENAI_BASE_URL` pointed at the self-hosted
  OpenAI-compatible endpoint). Added clarifying comments to
  `config/providers.py`, `config/providers.py.example`, `.env.example`
  (near `OPENAI_API_KEY`/`OPENAI_BASE_URL`) documenting the repurposing, and
  a commented-out routing example in `config/ops.py` /
  `config/ops.py.example` showing how to point `summarize_source`/
  `plan_compile` at the local endpoint once it's live. The live `config/
  ops.py` routing itself (still 100% `openrouter`) was deliberately **not**
  flipped in this change — `OPENAI_BASE_URL` is still blank, and routing an
  op to an inactive provider fails loudly at startup (`routing_config.py`),
  which would break both `pytest`'s and `smoke_flow.py --offline`'s default
  `Settings()` build. Flip it once the vLLM/llama.cpp server is actually
  reachable.
- `.env.example` — new `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
  `MAILGUN_SIGNING_KEY` (with `setWebhook`/Mailgun-dashboard registration
  notes), plus the local-LLM comment above.
- `tests/unit/test_layering.py` — added a `"channels"` layer to `FORBIDDEN`
  (same posture as `mcp`: reaches `tools`/`models`/`config`, never the L1
  primitives or `pipeline`), added `"channels"` to the banned-import sets of
  `models`/`storage`/`extractors`/`embedding`/`vector`/`llm`/`wiki`/`agent`/
  `pipeline`/`tools` (nothing below L4 may import it), and added
  `"channels"` to `test_transport_layer_only_calls_tools`'s allowed-imports
  set and module-selection filter so the new package is actually scanned.
  Extends the boundary under the existing rule; nothing was weakened.

**Related files:** `src/llmwiki/channels/__init__.py`,
`src/llmwiki/channels/telegram.py`, `src/llmwiki/channels/email.py`,
`src/llmwiki/config.py`, `src/llmwiki/api/app.py`, `config/providers.py`,
`config/providers.py.example`, `config/ops.py`, `config/ops.py.example`,
`.env.example`, `tests/unit/test_layering.py`, `tests/unit/test_channels.py`
(new).

**Test coverage:**
- No regressions: full suite went from 319 to 334 passed (1 skipped, 6
  deselected/opt-in integration, unchanged); `ruff check .` and `mypy` both
  clean.
- New: `tests/unit/test_channels.py` (15 tests) — router is `None` when the
  relevant secret is unset; webhook auth (missing/wrong header or signature
  → 401) for both channels; each Telegram message shape (plain text, bare
  URL, document, photo, "nothing to capture") mapped to the correct
  `ingest_source(...)` call via a mocked `tools.ingest_source`; each Mailgun
  payload shape (bare-URL body, attachment + cover note) likewise; the
  optional-mount behavior verified end-to-end through `create_app()` (404
  when unconfigured, mounted-and-401-on-missing-secret when configured).
  `tests/unit/test_layering.py` gained the `"channels"` entries described
  above (reviewed as ordinary test-suite changes, not exempted from review).
- No obsolete tests — purely additive.
- Documented here and in `CLAUDE.md`'s "Current State" (test count, working-
  in-this-repository setup notes for the new channels and local-LLM routing).

---

## 2026-09-09 — Plan + diagnostic script for standing up real Cloudflare Vectorize

**Goal:** the service still runs entirely offline (`STORAGE_BACKEND=local`,
`VECTOR_BACKEND=memory`, `EMBEDDING_BACKEND=fake` in `.env`), even though R2
credentials had already been filled in in an earlier session — `CF_ACCOUNT_ID`
and `CF_API_TOKEN` (Vectorize + Workers AI) were still `changeme`. Asked to
plan the move to a real Cloudflare Vectorize backend, plus a script to check
the setup (structure, credentials, permissions) before trusting it with real
ingests.

**Implementation detail:**
- `docs/cloudflare-vectorize-setup-plan.md` — sequences the existing runbook
  (`docs/implement-plan.md` §6.2–6.4: account ID, scoped API token, index
  creation) against the gap actually present in this `.env`, and adds the new
  diagnostic script as an explicit preflight step before flipping the three
  backend switches.
- `scripts/check_cloudflare_setup.py` (new, executable) — six independently
  reported checks: config presence, R2 put/get/delete round trip, Cloudflare
  API token validity (`GET /user/tokens/verify`), a live Workers AI embedding
  call asserting the dimension matches `EMBEDDING_DIM`, Vectorize index/
  metadata-index structure, and (skippable with `--quick`) a live Vectorize
  upsert → poll-until-queryable → delete round trip — the only one of the six
  that actually proves the token's *Edit* scope works, since token-verify and
  index-describe both succeed on a read-only token. Each check prints
  `[OK]`/`[FAIL]`/`[SKIP]` with a remediation hint; the process exits non-zero
  if anything failed. Deliberately a superset of, not a replacement for,
  `scripts/bootstrap_indexes.py` (structure only, and it creates things) and
  `tests/integration/test_cloudflare.py` (pytest-gated, the real regression
  net) — this is the fast, human-readable, run-by-hand preflight between them.
- No `.env`/`.env.example`/`src/llmwiki` changes — the required Cloudflare
  variables were already documented; only their values are still placeholders,
  which is exactly what the new script's first check flags.

**Related files:** `docs/cloudflare-vectorize-setup-plan.md`,
`scripts/check_cloudflare_setup.py`.

**Test coverage:**
- No regressions: `pytest` — 319 passed, 1 skipped, 6 deselected (opt-in
  integration, unaffected by this change); `ruff check .` and `mypy` both
  clean on the new file.
- No obsolete tests — nothing existing changed behavior.
- No new unit test added for the new script itself, matching the existing
  convention: `scripts/bootstrap_indexes.py`, the closest analog, has none
  either — every behavior worth asserting needs a live Cloudflare account and
  is already covered, for the underlying adapters, by
  `tests/integration/test_cloudflare.py`. Noted explicitly in the plan doc
  rather than silently skipped.
- Ran `scripts/check_cloudflare_setup.py --quick` against the current (still
  placeholder) `.env`: failed at check 1 as expected, reporting
  `CF_ACCOUNT_ID, CF_API_TOKEN` missing and making no network calls — the
  intended behavior before real credentials exist. The script has not yet been
  run against a live Cloudflare account; that only happens once the plan's
  steps 1–2 are carried out.

### Follow-up within the same change: Account API Token, not User API Token

**Root cause.** The plan and `docs/implement-plan.md` §6.3 originally pointed
at Dashboard → My Profile → API Tokens (a *User* API Token). The user asked
about Cloudflare's own recommendation to use an *Account* API Token instead —
correct: a user token is tied to whoever created it and stops working if that
person loses account access; an account token is a durable service principal,
which is what a credential baked into a server's `.env` should be. Confirmed
against current Cloudflare docs (verified live, not from memory): account
tokens live under Manage Account → Account API Tokens, require Super
Administrator to create, and — the concrete consequence for this codebase —
verify against a *different* endpoint than user tokens:
`GET /accounts/{account_id}/tokens/verify`, not `GET /user/tokens/verify`.

**Fix.** `docs/implement-plan.md` §6.3 and `docs/cloudflare-vectorize-setup-plan.md`
now both specify an Account API Token with the correct dashboard path and the
account-scoped verify curl example. `scripts/check_cloudflare_setup.py` check
3 now hits `/accounts/{account_id}/tokens/verify` instead of
`/user/tokens/verify`, with a comment explaining why.

**Related files:** `docs/implement-plan.md`, `docs/cloudflare-vectorize-setup-plan.md`,
`scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` and `mypy` clean on the changed script;
re-ran `scripts/check_cloudflare_setup.py --quick` against the (still
placeholder) `.env` — same expected check-1 failure, no network calls. No
`pytest` impact — none of the three changed files are imported by the test
suite.

### Follow-up within the same change: finding CF_ACCOUNT_ID

**Root cause.** `docs/cloudflare-vectorize-setup-plan.md` step 1 said "Dashboard →
right sidebar" for the account ID; the user couldn't find it there — Cloudflare
moved it. Confirmed live against current Cloudflare docs: it's now `Cmd/Ctrl+K` →
"Copy account ID", Workers & Pages → Account details, or a domain's Overview →
API section. Also noted the faster path specific to this repo: the account ID is
already the subdomain of the `R2_ENDPOINT_URL` this `.env` has from the earlier R2
setup, since R2 and Vectorize/Workers AI share one Cloudflare account.

**Fix.** `docs/cloudflare-vectorize-setup-plan.md` step 1 rewritten with the
`.env`-shortcut first and the three current dashboard locations.
`docs/implement-plan.md` §6.2 already said "it is the R2 endpoint hostname" and
needed no change.

**Related files:** `docs/cloudflare-vectorize-setup-plan.md`.

**Test coverage:** none applicable — a documentation-only correction, not a
covered code path.

### Follow-up within the same change: the permission is "Vectorize Write", not "Vectorize Edit"

**Root cause.** Every doc in this repo (inherited from the original
`implement-plan.md`, predating this session) said `Account → Vectorize →
Edit`. The user couldn't find a permission called "Edit" when creating an
Account API token. Checked Cloudflare's current API reference directly (the
per-operation docs for `vectorize-create-vectorize-index` and others): the
permission group is actually named **"Vectorize Write"** (paired with
"Vectorize Read"), matching the Read/Write convention Cloudflare uses for
newer products, not the older Read/Edit convention. Also worth stating
explicitly since the user's phrasing suggested otherwise: Workers AI → Read
does **not** cover Vectorize — they're two separate products needing two
separate permission grants on the same token.

**Fix.** Renamed every "Vectorize Edit" reference to "Vectorize Write" across
`docs/implement-plan.md` (§6.3 permission list and the `.env` comment),
`.env.example`, `docs/cloudflare-vectorize-setup-plan.md` (step 2, step 5, and
§3's check-6 description), and `scripts/check_cloudflare_setup.py` (check 6's
label and its failure-hint text). Also added a note in both plan docs that
Workers AI Read and Vectorize Write are independent grants, and that the
permission may need to be found via search in the token-creation picker.

**Related files:** `docs/implement-plan.md`, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md`, `scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` and `mypy` clean on the changed script;
re-ran `scripts/check_cloudflare_setup.py --quick` — same expected check-1
failure against the still-placeholder `.env`, no network calls. No `pytest`
impact — this was a rename of user-facing strings and doc text only, no
behavior change.

### Follow-up within the same change: Workers AI needs Read *and* Edit, not Read alone

**Root cause.** Asked whether "Workers AI Read, Workers AI Write, Vectorize
Write" was sufficient. Checked Cloudflare's own Workers AI REST API quickstart
directly: running a model (exactly what `embedding/workers_ai.py` does) needs
**both** `Workers AI - Read` and `Workers AI - Edit` granted together — this
repo's docs had only ever specified Read, which per Cloudflare's own docs is
not enough on its own. Also confirmed the two products name their permission
groups inconsistently: Vectorize is Read/**Write**, Workers AI is Read/**Edit**
— there is no "Workers AI Write".

**Fix.** `docs/implement-plan.md` §6.3 and its `.env` comment, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md` step 2, and the failure hint in
`scripts/check_cloudflare_setup.py` (`check_workers_ai`) now all list three
required grants: Vectorize Write, Workers AI Read, Workers AI Edit.

**Related files:** `docs/implement-plan.md`, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md`, `scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` (one line-length fix needed and applied) and
`mypy` both clean; `pytest` — 319 passed, 1 skipped, 6 deselected, unaffected;
re-ran `scripts/check_cloudflare_setup.py --quick` — same expected check-1
failure, no network calls.

## 2026-09-09 — Make LangChain + every provider SDK a core dependency

**Goal:** `uv sync` (per README's Quickstart) was installing only the base
`[project.dependencies]` group — none of `langchain-core` or the five
provider integrations (`langchain-openai`, `langchain-google-genai`,
`langchain-nvidia-ai-endpoints`, `langchain-deepseek`, `langchain-openrouter`)
were present, since they lived behind `optional-dependencies` extras. The user
asked for these to stop being optional: a plain `uv sync` should install all
of them, with no per-provider extra to remember.

**Root cause (why the gap was hit in the first place):** `pyproject.toml` put
`langchain-core` and every provider integration behind `optional-dependencies`
extras (`langchain`, `openai`, `google`, `nvidia`, `deepseek`, `openrouter`,
`all-providers`), on the design rationale that non-Anthropic providers should
"cost nothing until you ask for it." README.md's Quickstart told new
contributors to run bare `uv sync`, which — by design — skips every optional
extra, so nobody following the README ever got a LangChain package (or, since
`dev` is also an extra, `pytest`/`mypy`/`ruff` either).

**Implementation detail:**
- `pyproject.toml`: moved `langchain-core>=0.3`, `langchain-openai`,
  `langchain-google-genai`, `langchain-nvidia-ai-endpoints`,
  `langchain-deepseek`, and `langchain-openrouter` into
  `[project.dependencies]`. Deleted the now-empty `langchain`, `openai`,
  `google`, `nvidia`, `deepseek`, `openrouter`, and `all-providers` extras.
  `langsmith` (optional tracing) is untouched — it is independent of provider
  choice and stays opt-in. Dropped the redundant `langchain-core>=0.3` entry
  from the `dev` extra.
- `.env.example`: provider table's `install` column now reads `(built in)`
  for every provider; removed the stale `pip install "llmwiki[all-providers]"`
  line.
- `src/llmwiki/llm/providers.py`: corrected the module docstring, which
  claimed importing it "pulls in no provider SDK at all: `pip install
  llmwiki` stays free of them" — no longer true now that every SDK is a core
  dependency. The accurate framing: the per-call lazy import inside
  `build()`/`load_class()` keeps *importing this module* cheap, not the
  install itself. Removed the now-meaningless `ProviderSpec.extra` field and
  reworded `load_class`'s `RuntimeError` — an `ImportError` now means a
  broken/incomplete install (there is no separate extra to `pip install`
  anymore), so the message points at reinstalling llmwiki instead of naming a
  now-nonexistent extra.
- `CLAUDE.md`: updated "Current State" (dropped "each behind its own extra";
  noted the 2026-09-09 change and that `LLM_PROVIDER` switching needs no
  install step), removed the stale `uv pip install -e ".[openai]"` snippet
  under "Working in this repository", and reworded the
  `test_importing_the_registry_imports_no_provider_sdk` load-bearing-test
  description (it guards lazy *importing*, not "the extras" — those no longer
  exist).
- `README.md`: Quickstart now runs `uv sync --extra dev` instead of bare
  `uv sync`, so a new contributor also gets `pytest`/`mypy`/`ruff` (this extra
  gap predates today's change but was found and fixed in the same pass).

**Related files:** `pyproject.toml`, `uv.lock`, `.env.example`, `CLAUDE.md`,
`README.md`, `src/llmwiki/llm/providers.py`, `tests/unit/test_providers.py`.

**Test coverage:**
- No regressions: full `pytest` run — 289 passed, 1 skipped (unrelated
  `langsmith`-extra skip), 6 deselected (integration, opt-in). One pre-existing
  failure (`tests/unit/test_extractors.py::test_fetch_video_title_reads_oembed`)
  reproduces identically on a clean `git stash` checkout — confirmed unrelated
  to this change, left untouched.
- `ruff check .` and `mypy` both clean.
- `python scripts/smoke_flow.py --offline` — SMOKE PASS.
- Renamed and reworded two tests in `tests/unit/test_providers.py` to match
  the new (no-extras) failure message, rather than adding new tests — the
  registry's shape and lazy-import contract didn't change, only what happens
  when an import fails:
  - `test_a_missing_extra_names_the_pip_command` →
    `test_a_broken_install_names_the_distribution_and_suggests_reinstalling`
    (asserts the message now says "reinstall llmwiki" instead of naming a
    per-provider extra).
  - `test_the_factory_reports_a_missing_extra_rather_than_failing_at_the_socket`
    → `test_the_factory_reports_a_broken_dependency_rather_than_failing_at_the_socket`
    (same assertions, renamed for accuracy).
  - No tests removed: `test_registry_keywords_match_the_installed_class`'s
    `pytest.importorskip` simply stops skipping now that every provider
    module is always present — a strict improvement in coverage, not a
    behavior change.
  - The load-bearing `test_importing_the_registry_imports_no_provider_sdk`
    (`tests/unit/test_providers.py`) is untouched and still passes: it guards
    lazy per-call imports, which is orthogonal to whether the SDKs are
    optional or core dependencies.
