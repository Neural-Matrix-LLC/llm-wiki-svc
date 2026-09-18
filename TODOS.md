# TODOS

Future-planning items that are not yet scheduled into a phase. Each entry
records what is known so the work can be picked up without re-deriving it.
Move an item to `HISTORY.md` when it lands.

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
