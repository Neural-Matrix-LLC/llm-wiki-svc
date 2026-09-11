# HISTORY

Every code, configuration, or architectural change to this repository, in
reverse-chronological order. See `CLAUDE.md` for the rule this file follows.

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
