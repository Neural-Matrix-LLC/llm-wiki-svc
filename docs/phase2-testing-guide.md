# Phase 2 testing guide and manual test plan

**Scope.** The four Phase 2 workstreams of design v1.4 §4.10 /
`implement-plan-v1.4.md` §21: **A** domains (partitioning, routing,
synthesis), **B** hybrid retrieval + reranking, **C** cost (ledger, usage,
alerts, hard cap, the ingest worker), **D** selective multimodal. The code
path map is technical document §12; this is the *what to verify and how to
tell*, in the format of `docs/phase1-manual-test-plan-C-D.md`.

**Written 2026-09-19** against `llmwiki` with 713 unit tests.

---

## 0. How to use this plan

- Cases are `A-nn`, `B-nn`, `C-nn`, `D-nn`. Each has **Purpose**,
  **Steps**, **Expected**, **Evidence**. Results: **PASS** / **FAIL** /
  **BLOCKED** (say which precondition) / **N/A**.
- Run the automated baseline (§1) first and last.
- Every case can be run against the offline doubles first (`--offline`,
  `LEXICAL_BACKEND=sqlite`, `RERANKER_BACKEND=fake`, `LLM_PROVIDER=fake`); the
  live column is what the case proves on the real deployment.

### 0.1 Tooling

| Tool | What it gives you |
|---|---|
| `scripts/migrate_phase2.py --check\|--apply` | the three one-time upgrade steps (keyword index from `raw/`, legacy ledger → partitioned keys, registered domains' vector indexes); `--check` exits 1 while something is to do |
| `scripts/probe_domain_routing.py [--offline]` | the router's raw decision per source, without writing anything - for setting `DOMAIN_ROUTE_MIN_CONFIDENCE` |
| `llmwiki domains / usage / worker / lexical rebuild / synthesize` | the Phase 2 CLI surface (technical document §6.4) |
| `GET /domains /usage /worker /dashboard` | the same over REST; `/dashboard?token=…` in a browser |
| `scripts/smoke_flow.py --offline` | steps 8–11 exercise a second domain, scoped + fanned-out hybrid retrieval, usage/worker/synthesis and a fake-vision image end to end |

---

## 1. Automated baseline (S-0)

```bash
LANGSMITH_TRACING=false pytest -q            # 713 passed, 1 skipped
ruff check . && mypy
python scripts/smoke_flow.py --offline       # SMOKE PASS, 11 steps
python scripts/eval_answer.py --offline      # EVAL PASS (12 rows incl. two exact-term rows)
python scripts/probe_query_graph.py --offline --matrix
python scripts/probe_domain_routing.py --offline
docker compose --profile test run --rm pytest    # proves FTS5 is in the image's sqlite
```

Expected: all green. Evidence: the last line of each.

---

## 2. A — Domains

**A-01 Upgrade is a no-op for the existing wiki.** Steps: on the deployed
box, before changing anything else, `GET /concepts` and `GET /page/<slug>`
for two known pages; deploy Phase 2; repeat. Expected: byte-identical
responses; `/healthz` now lists `lexical`/`reranker` backends and `budget`.
Evidence: the two diffs (empty).

**A-02 `migrate_phase2.py`.** Steps: `python scripts/migrate_phase2.py
--check` (expect exit 1, "to build: …", the legacy ledger line), `--apply`,
`--check` again (exit 0, "nothing to do"). Expected: `{LOCAL_STORAGE_PATH}/
lexical/*.sqlite` exist with document counts ≥ the corpus's chunk count;
`wiki/_meta/cost.jsonl` gone, `wiki/_meta/cost/<month>/` populated;
`llmwiki cost` totals unchanged before/after. Evidence: the script output
and the two totals.

**A-03 Register a domain.** Steps: `llmwiki domains add ml-systems
--description "…"`; `GET /domains`; `scripts/bootstrap_indexes.py --check`.
Expected: the registry lists `general, ml-systems`; two new Vectorize
indexes `<base>-ml-systems` exist with the `source_id`/`slug`/`type`
metadata indexes. `domains add general`, `domains add concepts` → refused
by name. Evidence: the listing and the bootstrap output.

**A-04 Explicit domain, no routing call.** Steps: `llmwiki ingest --url …
--domain ml-systems`; `GET /sources/<id>`; `llmwiki usage --json | jq
.by_op`. Expected: `state=done`, `domain=ml-systems`; pages under
`wiki/domains/ml-systems/`; `general`'s manifest unchanged; **no
`route_domain` row** in the ledger for this source. Evidence: status JSON
and the `by_op` map.

**A-05 Routed source.** Steps: ingest a clearly ML-systems URL with no
`--domain`; then a clearly unrelated one. Expected: the first lands in
`ml-systems` (`routing.json` shows `explicit=false`, confidence ≥
threshold); the second in `general`, possibly with `suggested_domain` set;
exactly one `route_domain` ledger row each, `kind=ingest`. Evidence: the two
`routing.json` files.

**A-06 Threshold calibration.** Steps: `scripts/probe_domain_routing.py`
over the corpus; adjust `DOMAIN_ROUTE_MIN_CONFIDENCE` if demotions look
wrong. Expected: a verdict per source; nothing written. Evidence: the table.

**A-07 Capture hints.** Steps: Telegram message `#ml-systems <url>`; email
with subject `[ml-systems] …`; then `#nope <url>`. Expected: the first two
route explicitly and the stored title has no prefix; the third is acked
"Unknown domain 'nope'; nothing captured" / answered `{"ok": false, …}` and
nothing is captured. Evidence: the acks and `GET /sources/<id>`.

**A-08 Scoped and fanned-out queries.** Steps: `llmwiki ask "<ml question>"
--domain ml-systems`; the same without `--domain`; `--domain general`.
Expected: `domains: ml-systems` / `general, ml-systems` / none printed;
citations carry `domain`; the scoped run's LangSmith metadata lists the
domains. With `QUERY_DOMAIN_POLICY=routed` one extra `route_domain` call
appears in the ledger as `kind=query`. Evidence: the three outputs.

**A-09 Synthesis.** Steps: `llmwiki synthesize --domain ml-systems`; `GET
/page/overview?domain=ml-systems`; run it again. Expected: `written`,
`pages_read ≤ SYNTHESIS_MAX_PAGES`, one `synthesize_domain` ledger row
(`kind=synthesis`); version 2 after the second run; the domain index lists
`## Overview`; a later ingest into the domain leaves the overview at its
version (the compiler never patches it). Evidence: the two front matters.

**A-10 Lint per domain and unknown folders.** Steps: `llmwiki lint`;
`llmwiki lint --domain ml-systems`; create `wiki/domains/rogue/concepts/x.md`
by hand; `llmwiki lint`. Expected: the report names its domains; findings
carry `[domain]`; `unknown_domain rogue`. Evidence: the outputs.

**A-11 Remove a domain.** Steps: `DELETE /domains/ml-systems` (expect 409),
`?force=true` (200), `GET /domains`. Expected: the registry row is gone,
the pages remain, lint reports `unknown_domain`. Evidence: the responses.

---

## 3. B — Hybrid retrieval and reranking

**B-01 The exact-term question.** Steps: pick a rare token in the corpus (an
identifier, a model number). `LEXICAL_BACKEND=none llmwiki search "<token>"`
and `llmwiki search "<token>"`. Expected: dense-only misses or ranks it low;
hybrid returns it with a `lexical_score`. Evidence: the two hit lists.

**B-02 The gate does not move.** Steps: a question the wiki answers well,
with hybrid + rerank on and off. Expected: `used_rag_fallback` identical in
both runs; only the order/selection of pages differs. Evidence: the two
`Answer` JSONs' `used_rag_fallback`.

**B-03 Reranker failure is soft.** Steps: set `CF_API_TOKEN` to garbage for
one run (or `RERANKER_MODEL` to a nonexistent id); `llmwiki ask …`.
Expected: an answer, a WARNING "reranker failed … keeping the fused order",
no 5xx. Evidence: the log line.

**B-04 Rebuild equals incremental.** Steps: `llmwiki lexical rebuild`
document counts; ingest one source; counts again; `lexical rebuild` again.
Expected: counts rise by the source's chunks (+ its gists) and the rebuild
reproduces the same counts. Evidence: the three count tables.

**B-05 Fresh box degrades, does not fail.** Steps: move the `lexical/`
directory away; `llmwiki search …`. Expected: results (dense-only) and a
WARNING naming `llmwiki lexical rebuild`. Evidence: the log line.

**B-06 Golden set.** Steps: `python scripts/eval_answer.py --langsmith`
(real backends). Expected: `citations_resolve`, `expected_source_cited`,
`must_mention` all 1.00, including the two exact-term rows. Evidence: the
summary block.

---

## 4. C — Cost, usage, alerts, the cap, the worker

**C-01 Query-side cost.** Steps: `llmwiki ask …`; `llmwiki usage --json |
jq .by_kind`. Expected: `query` present and equal to the answer's printed
cost; `compile` rows unchanged by the question. Evidence: the map.

**C-02 Per-writer keys.** Steps: one ingest over REST, one `llmwiki ingest`
on the box, one cron `lint`. Expected: today's ledger keys are
`<DD>-api.jsonl`, `<DD>-cli.jsonl`, … - never a shared key. Evidence:
`ls wiki/_meta/cost/<month>/` (or the R2 listing).

**C-03 Usage and dashboard.** Steps: `GET /usage` without a token (401),
with; `GET /dashboard?token=…` in a browser. Expected: breakdowns by domain/
kind/op/model/day; the page renders with no console errors and no external
requests. Evidence: a screenshot.

**C-04 Soft alerts once per period.** Steps: set `COST_ALERT_DAILY_USD` just
below today's spend; ingest one source; ingest another; check the log and
the Telegram chat (`NOTIFY_BACKEND=telegram`, `ALERT_TELEGRAM_CHAT_ID`).
Expected: exactly one WARNING + one message; `wiki/_meta/cost/alerts.json`
shows `daily_warned` = today. Evidence: the message and the state file.

**C-05 The hard cap pauses processing only.** Steps: set
`COST_HARD_CAP_MONTHLY_USD` below this month's spend and restart; ingest a
URL; `GET /sources/<id>`; `GET /worker`; `llmwiki ask …`; `llmwiki search …`.
Expected: the source is captured (`raw/<id>/meta.json` exists) and
`paused` with the cap named; `/worker` lists it as parked with the reason;
the Telegram/log alert says when processing resumes and how many sources
wait; search and answer keep working. Then raise the cap, restart (or wait
`WORKER_RESUME_INTERVAL_S`, or `POST /worker/resume`). Expected: the source
reaches `done`. Evidence: the status JSONs before and after.

**C-06 Restart recovery.** Steps: submit three ingests over REST and kill
the process mid-way (`docker compose restart api`). Expected: on startup the
log says "recovered N pending source(s)"; all three reach `done`;
`status/_pending/` is empty. Evidence: the log line and the statuses.

**C-07 Per-domain serialization.** Steps: fire six ingests at once, three
into each of two domains (`WORKER_THREADS=4`). Expected: all `done`; the
domain manifests contain every page (no lost update); the log shows the
two domains' `state=compiling` intervals overlapping across domains but not
within one. Evidence: the manifests' source lists and the log.

---

## 5. D — Selective multimodal

**D-01 Off is off.** Steps: with `VISION_MODE=off`, `POST /upload` a PNG and
a scanned PDF. Expected: both `failed`, the errors naming `VISION_MODE`.
Evidence: the two statuses.

**D-02 An image becomes text.** Steps: `VISION_MODE=auto`, `describe_image`
routed to a vision-capable model; upload a photo of a whiteboard. Expected:
`done`, `vision_calls=1`; `raw/<id>/extracted.md` holds `#### Described
content (uploaded image)` with the board's text transcribed;
`raw/<id>/vision.json` exists; one `describe_image` ledger row
(`kind=ingest`). `llmwiki compile <id> --force` makes **no** second
`describe_image` call. Evidence: the extracted text and the ledger rows.

**D-03 A scan, bounded.** Steps: upload a 20-page scanned PDF with
`VISION_MAX_PAGES_PER_SOURCE=4`. Expected: `done`, `vision_calls=4`; pages
1–4 described, pages 5–20 carry the "not described - VISION_MAX_PAGES_PER_
SOURCE reached" note; the compile's cost includes the four calls;
`GET /sources/<id>` shows `vision_calls=4`. Evidence: `extracted.md` and the
status.

**D-04 A figure page in an ordinary PDF.** Steps: upload a text PDF with one
full-page chart, `VISION_MODE=auto`. Expected: `vision_calls=1`; only the
chart page gets a `#### Described content` block, with the chart's axes and
numbers; text pages untouched. Evidence: the block.

**D-05 Budget.** Steps: `INGEST_TOKEN_BUDGET=2000`, upload the scan again.
Expected: `failed` with `INGEST_TOKEN_BUDGET` in the error - vision spend
counts. Evidence: the status.

**D-06 Text-only routing refused at startup.** Steps: route `describe_image`
to a provider whose adapter has no `describe()` (only possible with a custom
adapter; skip → N/A) - or verify the refusal path with the unit test
`test_factory_refuses_at_startup_when_describe_image_routes_to_a_text_only_adapter`.

---

## 6. Results log

| Case | Result | Date | Evidence / notes |
|---|---|---|---|
| S-0 | | | |
| A-01 … A-11 | | | |
| B-01 … B-06 | | | |
| C-01 … C-07 | | | |
| D-01 … D-06 | | | |
