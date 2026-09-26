# TODOS

Future-planning items that are not yet scheduled into a phase. Each entry
records what is known so the work can be picked up without re-deriving it.
Move an item to `HISTORY.md` when it lands.

## Reconcile with `~/branch/llm-wiki-svc` (Phase 1.x) — stashed work (2026-09-25)

This tree (Phase 2, `ef96cca`) and `~/branch/llm-wiki-svc` split after
`8a1ceb2` "Complete Phase 1". Each has work the other lacks:

- **Stashed here** — `git stash list` →
  *"phase1-testing-guide §6 production capture setup + cloudflared tunnel
  profile (2026-09-19)"*. It holds uncommitted changes to four files:
  - `docs/phase1-testing-guide.md`: a new §6 on production setup of the
    capture channels (6.0 Tunnel vs. Access, a named tunnel with connector
    options A/B/C, `healthz` decision table, Telegram, optional Access
    bypass for `/channels/*`, Mailgun, dev vs. production).
  - `docker-compose.yml`: an optional `cloudflared` service behind the
    `tunnel` profile, with origin `http://api:8000`.
  - `.env.example`: `CLOUDFLARE_TUNNEL_TOKEN`.
  - `HISTORY.md`: a 2026-09-19 entry describing the above.

  Mostly docs; no Python change. Restore with `git stash apply` (find its
  index with `git stash list`; `stash@{0}` when stashed).
- **Only in `~/branch`** (7 commits through `d619816`, "deploy Telegram Bot
  to production"; no Phase 2):
  - `scripts/telegram_webhook.py` + the `telegram-webhook` compose
    one-shot, `docker-compose-cloudflared.yml` (tunnel as its own project,
    joined over the `llmwiki-net` network at `llmwiki-api:8000`),
    `.env.cloudflared.example`, `docs/runbook-hostinger.md`.
  - `scripts/sync_wiki.py` (mirrors the R2 wiki locally for Obsidian) and
    `scripts/verify_capture.py`.
  - `YOUTUBE_PROXY_URL` / `YOUTUBE_COOKIES_PATH` / `YOUTUBE_WHISPER_MODEL`,
    and Telegram now captures after replying 200.
  - The two implementation plans merged into `docs/implement-plan.md`
    v1.5.
  - A fix for empty `.env` values with inline comments.

**To do.** Bring the branch's commits onto this Phase 2 tree (merge or
cherry-pick). **Pick one production tunnel design** — the branch's
separate-project + one-shot (what production runs today) or the stash's
in-compose `tunnel` profile — then either drop the stash or rewrite its §6
to match. Expect conflicts in `HISTORY.md`, `.env.example`,
`docker-compose.yml`, `docs/phase1-testing-guide.md` and the
implementation-plan files (`implement-plan-v1.4.md` vs. the merged
`implement-plan.md`).

## Deferred from the Phase 2 design (2026-09-18)

Recorded when design v1.4 §4.10 / plan §21 were locked. None is scheduled;
each is stated so it can be picked up without re-deriving the decision.

- **Custom mobile app** — design §5's conditional Phase 2 item; stays
  conditional on proven messaging friction. Telegram/email plus the
  `#domain` / `[domain]` prefixes (plan §21.7) are the capture story.
- **Compile-correctness golden set** (`scripts/eval_compile.py`) — the
  deferred Phase 1 dataset, and the natural measurement for the per-domain
  synthesis job (plan §21.2 A8). Shape: fixture source → expected pages
  touched / must-link, run offline like `eval_answer.py`.
- **Lexical retrieval inside the compiler's `_locate`** — Phase 2 keeps the
  compiler's own lookup dense + exact-slug (§4.4 machinery untouched). Once
  `LexicalIndex` exists (P2-B1) the change is one more query per probe.
- **Qdrant as an alternative hybrid store** — rejected for Phase 2 (a
  stateful container with fixed RAM plus a migration off Vectorize); the
  `LexicalIndex`/`Reranker` seams mean it would slot in as a backend, not a
  redesign.
- **Qualified cross-domain wikilinks** — `[[slug]]` is ambiguous in Obsidian
  when two domains share a slug (plan §21.2 A7). Fix when observed: write
  `[[domains/{d}/concepts/{slug}|title]]` for non-general pages in
  `append_sources_section` and the page prompts.
- **Renaming `general`**, **monthly ledger retention**, and **sharing one
  Telegram client** between `notify/telegram.py` and
  `channels/telegram.py:_ack` — small follow-ups once the pieces exist.
- **Per-hit retrieval scores in eval output** — plan §21.9 asked
  `eval_answer.py` to print gate/score distributions after P2-B2; the
  `Answer` model does not carry per-hit `dense_score`/`rerank_score`, so this
  needs a small additive field (e.g. `Answer.retrieval: list[SearchHit]`
  trimmed) before the script can print them.
- **`domains reassign`** — moving an already-compiled source to another
  domain = `delete_by_source` in the old chunk/lexical indexes, re-embed +
  recompile into the new one, and a lint pass on the pages that still list
  it. Not needed until a real corpus mis-routes something worth moving.
- **Rerank-raised wiki confidence** — whether a strong reranker score should
  count as "the wiki answers" when the dense cosine is below
  `WIKI_CONFIDENCE`. Decide from the score distributions
  `scripts/eval_answer.py` prints after P2-B2, on the real corpus.

## Ingestion: two more URL source kinds (2026-09-17)

Both extend the five-source-kind surface (technical document §3.1.1) with a
new URL-shape modality. **The `/ingest` surface itself needs no change**: kind
is detected, never declared (`extractors/base.py:detect_modality`), so REST
`POST /ingest`, MCP `ingest_source`, `llmwiki ingest --url` and the
Telegram/email channels pick a new URL kind up for free, and
`tests/unit/test_tools_and_mcp.py::test_every_transport_can_ingest_all_five_source_kinds`
checks parameter shape, not kind count. Follow the six-step recipe in
technical document §5.4.

Shared work for either kind (~2 h):

- `models/source.py:Modality` literal value.
- `detect_modality` branch placed **before** the mime checks, like the
  YouTube branch — the function runs twice for a URL (declared mime, then
  served content type), and a mime-first check would re-classify the stored
  JSON as `text` on the second pass.
- `storage/layout.py:ext_for`: map `application/json` → `json` (today it
  falls through to `bin`).
- `_title_from_source`, fixture under `tests/fixtures/`, `test_extractors.py`
  case, `detect_modality` ordering cases, §3.1.1 table → seven kinds,
  `HISTORY.md` entry, `.env.example` for any new key.

### GitHub repository URL — recommended first (~1 day, no credentials)

- Detection: host `github.com` + `owner/repo`; a `/blob/…` link to a single
  file should keep falling through to `web`/`text`.
- Fetch: prefer the REST API over a tarball/clone — it returns exactly the
  metadata + README + tree the selection policy below wants, with no
  archive handling. `httpx.Client(base_url="https://api.github.com",
  headers={"Accept": "application/vnd.github+json"} + optional
  `Authorization: Bearer`)`, then per repo:
  - `GET /repos/{owner}/{repo}` — description, `default_branch`,
    primary `language`, stars, topics, `pushed_at`.
  - `GET /repos/{owner}/{repo}/readme` — base64 `content`; decode with
    `errors="replace"`.
  - `GET /repos/{owner}/{repo}/languages` — byte counts per language.
  - `GET /repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1` —
    full file tree with sizes in one call (the file-tree listing).
  - `GET /repos/{owner}/{repo}/contents/{path}` (or
    `raw.githubusercontent.com`) — only for the docs the policy selects.
  Optional `GITHUB_TOKEN` lifts the rate limit 60 → 5000/h; a 403/429
  should log a WARNING and degrade to what was already fetched (metadata +
  README) rather than fail the ingest. Store the as-fetched responses as
  one JSON bundle in `original.json` so extraction stays a pure function of
  stored bytes.
- **Reference implementation:**
  [`thomas-choi/Resume-Builder`](https://github.com/thomas-choi/Resume-Builder) —
  `src/tools/github_client.py` (`_headers`, `_render_repo`,
  `_paged`, 403/429 degradation) and `TECHNICAL-DESIGN.md` §3 "GitHub
  source coverage". Its unit is a *username* (owned / org / contributed
  tiers, attribution contract) which we don't need; the per-repo half
  (`_render_repo`: metadata → languages → README excerpt) is the shape to
  reuse. Two lessons carry over verbatim: pull descriptions + languages +
  README excerpt, *not* full source, to keep token cost down; and the
  token is a per-call parameter with an env fallback, never a module
  global, and never logged or archived.
- Extractor: markdown with `### path/to/file` headers so citations resolve
  to a file, the way YouTube chunks resolve to a timestamp. Quote README
  and doc bodies line-by-line (`> `) or demote their headings — READMEs
  carry their own `##`/`###` headings, which would otherwise read as file
  boundaries of our document (the reference hit exactly this).
- **Open design question — the selection policy.** A repo is not one
  document: even a small one is 500k+ chars against the ~60k-token
  `ingest_token_budget` the compiler aborts at (design §4.4). Proposed
  default: README, `docs/**`, top-level `*.md`, plus a file-tree listing
  with sizes — no source code. Optional `include_code` with an extension
  allowlist and a hard char cap (~200k) noting truncation in `extra`.
  Exclude vendored/lock/binary/`.git`. Needs a size-cap test.
- Freshness: `content_hash_for_url` makes a re-ingest of the same URL a
  duplicate even after the repo changes; key on `url + default-branch HEAD
  sha` if that matters.

### X (Twitter) post / thread URL (~½ day single post; +1 day for threads)

- Detection: host `x.com`/`twitter.com` + `/status/<id>`.
- Today a post URL goes through `WebExtractor` and fails ("no readable
  content") because `x.com` serves a JS shell.
- **Open decision — access, not code.** Options:
  - oEmbed (`publish.twitter.com/oembed`): free, no key, single post only,
    unofficial. Recommended first step.
  - X API v2 `GET /2/tweets/:id` + conversation search for threads: needs a
    developer account; reading is paid-tier only (~$100/mo at last check —
    verify), a fixed cost the project's cost philosophy is built to avoid.
  - Nitter-style mirrors: free but unreliable.
- Extractor: JSON → markdown (author, date, text, quoted posts, thread
  order); title `@author: first 60 chars`; `X_BEARER_TOKEN` only if the API
  path is chosen. Treat threads as out of scope until an API key is
  justified.
