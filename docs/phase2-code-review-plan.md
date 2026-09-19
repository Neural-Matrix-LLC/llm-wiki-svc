# Phase 2 — code review plan

**For:** the developers and team lead reviewing the Phase 2 change set
(commit "Phase 2 — …", 2026-09-19).
**What it covers:** how the code was written, what changed, where the risk
sits, what to review first and with which checklist, what was verified and
what was not. It is a plan for *your* review, not a substitute for it.

Read alongside: design v1.4 §4.10 (the decisions), `implement-plan-v1.4.md`
§21 (the implementation design and the milestone table), technical document
§12 (the code-path map), `docs/phase2-testing-guide.md` (the live test plan),
and the twelve `HISTORY.md` entries dated 2026-09-19 (one per milestone, each
with its own *Tests* section).

---

## 1. How this code was written

The whole set was produced in one AI-assisted session (Claude Code) against
an approved plan, in twelve milestones, each landing as a separately
verifiable increment:

| Order | Milestone | Size (approx.) |
|---|---|---|
| 1 | P2-0 docs (design §4.10, plan §21) | text only |
| 2 | P2-1 ledger, metering, models, ops rows, prompts | ~600 lines |
| 3 | P2-A1 domain layout, registry, per-domain compile/lint, `list_domains` | ~900 |
| 4 | P2-A2 domain router, capture hints, probe script | ~450 |
| 5 | P2-C1 ingest worker, per-domain lock, recovery | ~400 |
| 6 | P2-A3 query scopes, the retrieval seam | ~450 |
| 7 | P2-B1 lexical index (FTS5/BM25), RRF, rebuild | ~650 |
| 8 | P2-B2 reranker | ~250 |
| 9 | P2-C2 usage, alerts, hard cap, dashboard | ~700 |
| 10 | P2-A4 synthesis | ~250 |
| 11 | P2-D1 vision protocol, describe step, image extractor | ~500 |
| 12 | P2-D2 PDF page selection | ~200 |
| 13 | P2-Z migration script, smoke, docs | ~900 (mostly docs) |

Totals: 68 files changed, ~5.2k insertions, ~480 deletions; 38 new files;
~2.4k lines of new production code under `src/llmwiki/`; 459 → 714 unit
tests. The working method, which you can rely on when reviewing:

- **Every milestone ended green** on the full gate (`pytest`, `ruff check .`,
  `mypy`, `smoke_flow --offline`, `eval_answer --offline`,
  `probe_query_graph --offline --matrix`) before the next started. There was
  no "fix the tests at the end" phase.
- **Tests were written in the same milestone as the code**, usually right
  after it, and several times they caught real defects that were fixed before
  moving on (listed in §5 — those are the places worth a second look).
- **Behaviour compatibility was a design constraint, not a hope.** Each
  feature is off, or identical to Phase 1, under its default switch, and a
  test pins that: the `general`-only byte-identity test, the "single scope
  with no lexical/reranker is the pre-Phase-2 call" test, `VISION_MODE=off`
  = the old extractor, alerts at `0`. Six autouse fixtures in
  `tests/conftest.py` pin every existing test to the Phase 1 behaviour;
  Phase 2 tests opt in explicitly.
- **Deviations from the plan were recorded, not hidden.** Each `HISTORY.md`
  entry names them (§6 below collects them).
- **The five original load-bearing tests were not edited.** Three new ones
  joined them (`CLAUDE.md`).

What this method does *not* give you: a second pair of human eyes on
design judgement calls, live verification against Cloudflare / Telegram / a
real vision model, or load behaviour beyond a four-thread unit test. That
is what this review and the manual plan are for.

---

## 2. Review order and suggested split

Review in dependency order; each row is a self-contained reading of about
one to two hours. Suggested owners are roles, not names.

| # | Area | Files to read (in order) | Tests that pin it | Suggested owner |
|---|---|---|---|---|
| R1 | **Domain layout & registry** — the organising principle | `storage/layout.py` (diff), `wiki/domains.py`, `wiki/gists.py`, `wiki/pages.py`, `wiki/compiler.py` (diff), `wiki/lint.py` | `test_layout_domains.py`, `test_domains.py` (esp. the byte-identity and one-manifest guards), `test_compiler_no_full_scan.py` (unchanged) | someone who knows the Phase 0 compiler |
| R2 | **Routing & the ingest pipeline** | `pipeline/ingest.py` (diff), `wiki/router.py`, `channels/domain_prefix.py`, `channels/{telegram,email}.py` (diff), `llm/fake.py` (`route_domain` synthesis) | `test_router.py`, `test_channels.py` (+6) | backend |
| R3 | **The ingest worker** — concurrency | `pipeline/worker.py`, `factory.compile_worker`, `api/app.py` (lifespan), `tools.enqueue_source`/`process_source` | `test_worker.py` (esp. the interleaving test) | whoever owns deployment/ops |
| R4 | **Retrieval seam, hybrid, rerank** | `agent/retrieval.py`, `agent/query.py` (diff), `agent/graph.py` (diff), `agent/toolkit.py` (diff), `lexical/*`, `rerank/*`, `pipeline/lexical_rebuild.py` | `test_query_scopes.py`, `test_retrieval.py`, `test_lexical.py`, `test_lexical_rebuild.py`, `test_rerank.py`; `test_agent.py` (unchanged) | search/RAG owner |
| R5 | **Cost: ledger, metering, alerts, cap, dashboard** | `wiki/ledger.py`, `llm/metering.py`, `wiki/alerts.py`, `notify/*`, `api/dashboard.py`, `tools.py` (`_record_costs`, `usage_summary`, `processing_capped`) | `test_ledger.py`, `test_metering.py`, `test_alerts.py` | backend + whoever pays the bill |
| R6 | **Vision** | `llm/base.py` (`VisionLLMClient`), the four `describe()` implementations, `llm/router.py` (diff), `pipeline/describe.py`, `extractors/{image,pdf}.py`, `factory` startup check | `test_vision.py`, `test_pdf_vision_policy.py` | LLM integration owner |
| R7 | **Synthesis** | `wiki/synthesis.py`, `wiki/compiler.py:_locate` (overview exclusion) | `test_synthesis.py` | prompt/quality owner |
| R8 | **Surfaces & config** | `tools.py` (whole diff), `api/routes.py`, `mcp/server.py`, `cli.py`, `config.py`, `.env.example`, `config/ops.py`, `docker-compose.yml` | `test_routes.py`, `test_tools_and_mcp.py` (parity), `test_config.py` | API owner |
| R9 | **Ops & docs** | `scripts/migrate_phase2.py`, `scripts/probe_domain_routing.py`, `scripts/smoke_flow.py` (diff), technical document §12, README, testing guide | `test_phase1_scripts.py` (+3) | team lead |

R1 first — everything else assumes "`general` is the Phase 1 layout" holds.
R3 and R5 are where a defect would cost money or lose data; give them the
most careful reader.

---

## 3. What to look for, per area

### R1 Domain layout & registry
- `layout.py`: is there any code path where a `general` key differs from
  its pre-Phase-2 constant? (`gists_key`, `index_key`, `wiki_page`,
  `domain_index_name`.) The byte-identity fixture covers what the compiler
  writes; check `get_page`'s fallbacks and lint's key attribution by eye.
- `check_domain`'s reserved list: is anything missing that a future folder
  under `wiki/` could collide with? Is 32 chars the right cap given
  Vectorize's 64-char index-name limit and the longest configured base?
- `domains.py:remove_domain` never deletes pages; confirm the team wants
  that (it mirrors `delete_source`'s posture).
- `compiler.py`: the registry is read once per compile *only* for
  `general`'s index. Is one extra GET per compile acceptable on R2, or should
  the `## Domains` section be rendered only on registry change?
- `lint.py`: `unknown_domain` uses one `store.list("wiki/")` — lint is the
  one place allowed to, but confirm the general/domain key attribution in
  `_lint_domain` excludes `_meta/`, `index.md` and `overview.md` correctly.

### R2 Routing & pipeline
- `pipeline/ingest.py:route`: the cheapest-first order (explicit → general-
  only → `DOMAIN_ROUTING=off` → model). Is routing on the title + first
  1 500 chars enough for your corpus, or should it see the compiler's
  summary (deliberately rejected in the plan — see design §4.10.1)?
- `wiki/router.py`: the demotion rules (unregistered name, low confidence)
  and `_clean_suggestion`. A model can never create a domain — check that
  claim against the code, not the docstring.
- `route()` persists `routing.json` *before* embed/compile. If compile then
  fails, the source stays routed; is that what you want on `compile_update`?
- Channel prefixes: `#domain` on Telegram is matched at the start of the
  caption/text only; an email `[domain]` only at the start of the subject.
  An unknown domain is acked, not 500'd. Check the regexes against the
  domain-name grammar.

### R3 Ingest worker
- `domain_lock` is process-wide and held around **embed + compile** only.
  Walk `IngestPipeline.process` and confirm every manifest/index write of a
  domain happens inside it, and that `tools.synthesize` takes the same lock.
- Two API replicas would each have their own locks: the design assumes one
  API process. Confirm that is true of the deployment (it is today).
- Pending markers: written at capture, cleared on every exit of `process()`
  (done and failed). What happens to a source whose `process()` raises
  something that is *not* caught (`clear_pending` is after the try/except -
  read it)?
- Parking under the cap: a parked source's marker is kept; `_retry_parked`
  is a daemon `threading.Timer`. Check the timer is re-armed exactly once
  and cancelled on `shutdown()`.
- `WORKER_THREADS` also bounds concurrent LLM calls from ingest — is 4 right
  for your provider's rate limits?

### R4 Retrieval
- `retrieve_layer`: the pass-through branch (one scope, no lexical, no
  reranker) must be byte-identical to Phase 1. Read it against the old
  `graph.retrieve` in git history.
- The gate reads `gate_score` = `dense_score ?? score`. A lexical-only wiki
  hit therefore never opens the wiki gate. Agree or disagree — it is the
  conservative reading; the alternative is recorded in `TODOS.md`.
- `rrf_fuse`: ranks only, `K=60`, ties broken by id. `score` is overwritten
  with the fused score - check nothing downstream still treats `score` as a
  cosine (grep `WIKI_CONFIDENCE`, `.score >=`).
- `lexical/sqlite.py`: `fts_query` quotes every token and ORs them. Confirm
  no user text can reach `MATCH` unquoted. One connection per call,
  `check_same_thread=False`, a process lock per index for writes: is that
  enough under `WORKER_THREADS=4` writers into one domain? (The domain lock
  already serialises them; the lexical lock is belt-and-braces.)
- `memory.py` has a light suffix stemmer so tests behave like FTS5's porter
  tokenizer - it is test infrastructure, not a product feature; make sure no
  production path selects it (`LEXICAL_BACKEND=memory` is documented as
  tests/offline).
- `rerank/workers_ai.py`: request/response shape is asserted by a mock, not
  by a live call. **Verify against the real API once** (testing guide B-03).

### R5 Cost
- `ledger.py`: key = `{YYYY-MM}/{DD}-{writer}`; the writer name is the
  process's `COST_WRITER`. Check every entry point sets a distinct writer
  (`api` default, `cli`, `backfill`, `eval`, `smoke`, `probe`, `migrate`,
  compose `synth`). Two processes with the same writer name on the same day
  *would* race — is anything deployed twice with the default?
- `metering.py`: the `contextvars` collector. `test_metering.py` proves
  LangGraph's node threads see it today; this depends on LangGraph copying
  context into its executor. The fallback (a state reducer) is written down
  in plan §21.2 C2 — decide whether you want it implemented pre-emptively.
- `alerts.py:evaluate`: the "don't add `added_usd` after a refresh" rule
  was a real double-counting bug found by test (§5). Read `_ensure_fresh` +
  `evaluate` together. Totals are cached for 60 s per process; with several
  processes the cap can be crossed by up to one refresh window — acceptable?
- The hard cap parks *processing*; queries keep spending. That is the
  decision the user made (design §4.10.3). Make sure the alert text and the
  dashboard say so plainly.
- `api/dashboard.py`: the token may arrive as `?token=`. It is the same
  static bearer token; the route is documented "keep behind TLS". Confirm
  the deployment terminates TLS in front of it. No JS, no external assets -
  check the CSP story is as simple as it looks.
- `_record_costs` swallows storage errors (logs, never fails an answer).
  Agree that is right for the query path.

### R6 Vision
- `VisionLLMClient` is a separate protocol; `complete()` is unchanged. Diff
  `llm/base.py` and confirm no existing signature moved.
- `pipeline/describe.py`: the cap, the cache (`vision.json`) and the
  "nothing described and only headings" error. `extra["vision_pages"]`
  carries raw PNG bytes in memory between extractor and describe step - check
  it is stripped before anything serialises `ExtractedDoc`.
- `extractors/pdf.py:_select`: the scan heuristic (≥ 80 % text-less pages →
  reading order, else by image area). The thresholds are settings; the
  `DRAWINGS_MIN=200` and `SCAN_RATIO=0.8` constants are not — should they be?
- `config/ops.py` routes `describe_image` to `openrouter` /
  `google/gemini-2.5-flash-lite`. **The model id has not been verified
  against OpenRouter** (plan risk); the first live `VISION_MODE=auto` ingest
  will tell.
- Cost: vision usage is ledgered as `kind="ingest"` *and* counted in the
  compiler's budget via `prior_costs` but not re-ledgered. Read both ends.

### R7 Synthesis
- One call, ≤ `SYNTHESIS_MAX_PAGES` bodies each ≤ 6 000 chars. Is 10 pages
  × 6k chars the right prompt size for the routed model (`glm-5.3-flash`,
  8192 output tokens)?
- The compiler excludes `overview` pages from `_locate`. Confirm there is no
  other path (plan ops with `type` `concept`/`entity` only) that could patch
  it.
- `overview`'s `sources` is the union of the ranked pages' sources - that
  is what makes its citations resolve. Decide whether that is honest enough
  for the citation contract.

### R8 Surfaces & config
- Parity: every capture surface takes `domain`; every read surface that has
  a scope takes `domain`; MCP grew by exactly `list_domains`. The parity
  test in `test_tools_and_mcp.py` is the contract - read it.
- Error mapping: unknown domain → 404 on REST, an observation in the query
  graph's tools, an ack on Telegram, `{"ok": false}` on email. Consistent
  enough?
- `Settings` defaults: `LEXICAL_BACKEND=sqlite`, `RERANKER_BACKEND=
  workers_ai` and `WORKER_MODE=threads` default **on** ("defaults are
  production"); `VISION_MODE=off`, alerts `0`, `QUERY_DOMAIN_POLICY=all`.
  This is the single decision most likely to surprise an upgrader - agree
  it, or flip the two retrieval defaults to `none`.
- `.env.example`: every new variable is there with a comment; check the
  comments match the code's defaults.
- `docker-compose.yml`: the new `synthesize` service (ops profile,
  `COST_WRITER=synth`) - wire it to cron next to `lint`.

### R9 Ops & docs
- `scripts/migrate_phase2.py`: `--check` is read-only and exits 1 while
  work remains; `--apply` runs the lexical rebuild (lists `raw/` - the one
  admin operation allowed to), migrates the ledger, ensures indexes. Run
  `--check` on a copy of the production data before trusting it.
- Docs: technical document §12 is the code-path map written *after* the
  code; spot-check three claims against the source (suggested: the
  retrieval pseudo-code, the ledger key format, the vision flow).

---

## 4. What was verified, and what was not

**Verified automatically (every milestone, and at the end):** 714 unit tests
offline; `ruff`, `mypy`; `smoke_flow --offline` (11 steps, incl. a second
domain, hybrid + fake reranker, usage/worker/synthesis, a fake-vision image);
`eval_answer --offline` (12 golden rows incl. two exact-term rows);
`probe_query_graph --offline --matrix`; `probe_domain_routing --offline`;
`migrate_phase2.py --check/--apply --offline`. Inside the dev Docker image:
709 passed (the 6 skips are pre-existing provider-extra skips, not Phase 2)
and **FTS5 is present in the image's sqlite (3.46.1)**.

**Not verified - needs the live plan (`docs/phase2-testing-guide.md`):**
Vectorize index creation for a new domain (`ensure_index` against the real
API); the Workers AI reranker's real request/response; a real vision model
on `describe_image` (and the OpenRouter model id); Telegram alerts; restart
recovery under Docker; per-domain serialization under real load; the router's
confidence threshold on a real corpus; the migration script on production
data. Nothing in Phase 2 has been deployed.

**Not covered by design (recorded in `TODOS.md`):** per-hit score
distributions in the eval output; qualified cross-domain wikilinks; moving a
source between domains (`domains reassign`); a compile-correctness golden set
for synthesis; whether a strong rerank score should raise wiki confidence.

---

## 5. Defects the tests caught during the session (worth a second look)

These are places where the first draft was wrong and a test corrected it;
the fix is in, but the neighbourhood deserves attention.

1. `wiki/alerts.py` - spend was double-counted when a refresh happened in
   the same `evaluate()` call as the just-ledgered amount (`_ensure_fresh`
   now returns whether it refreshed).
2. `llm/fake.py` - the routing double matched domain names against the
   whole prompt, including the registry listing, so it routed *everything*
   to the first domain; it now looks only at the `# Source` section. Affects
   tests and offline runs only.
3. `rerank/fake.py` - imported `lexical.tokenize` across L1 peers;
   `test_layering.py` refused it. Now has its own tokenizer.
4. `pipeline/describe.py` - the "empty after description" rule originally
   fired only on a literally empty string; headings-and-placeholders now
   count as empty.
5. `extractors/pdf.py` - with `max_pages=0` a scan produced a document of
   notes; it now raises like a scan under `off`.
6. `cli.py` - `llmwiki --offline` was not offline in a checkout that ships
   `config/providers.py`: the routing table outranks `LLM_PROVIDER`. An
   `--offline ingest` on the dev box compiled with the real OpenRouter route
   (a few cents). `OFFLINE_ENV` now points both config paths at nothing.
7. The offline scripts built the real Workers AI reranker from `.env`'s
   `CF_*` credentials and passed anyway thanks to the fallback; they now
   force `RERANKER_BACKEND=fake`.
8. `wiki/ledger.py` / tests - test helpers appended old records under
   today's key; the month-window read then excluded them by `at`. Not a
   product bug, but read `read()`'s two filters (key day, then record `at`).

---

## 6. Deviations from the approved plan (all recorded in `HISTORY.md`)

| Plan said | Code does | Why |
|---|---|---|
| P2-1 sets `KNOWN_OPS`=10 | grew per call-site milestone (8 → 9 → 10) | the drift guard equates `KNOWN_OPS` with the ops the code calls |
| conftest pins `LEXICAL_BACKEND=memory` | pins `none` (and `RERANKER_BACKEND=none`) | `none` is the byte-identical Phase 1 path the wiki-first/citation tests were written against |
| P2-A1 = layout only; explicit `domain=` in A2 | explicit `domain=` + `routing.json` pulled into A1 | a second domain had to be testable end to end |
| P2-B2 eval prints score distributions | deferred | `Answer` carries no per-hit scores; needs a small additive field |
| technical document §3.8 / §5 / §10 edits | one consolidated §12 + table patches | one place to read the Phase 2 map |
| conftest: four autouse fixtures | six (worker, lexical+reranker) | new switches needed the same isolation |

---

## 7. Suggested acceptance criteria for this review

- R1 and R4 reviewers sign off that the Phase 1 path is byte-identical under
  the defaults they read (not just that the tests say so).
- R3 and R5 reviewers each name one failure scenario they tried to construct
  and could not.
- The default-on decision for `LEXICAL_BACKEND`/`RERANKER_BACKEND` is
  explicitly accepted or flipped before the first deploy.
- The live plan's A-01, A-02, B-03, C-05 and D-02 are run on a staging copy
  before production; their evidence goes in the testing guide's results log.
- Any review finding is fixed with a test and a `HISTORY.md` entry, per the
  repo's standing rule.
