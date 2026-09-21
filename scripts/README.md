# Scripts

Operational tooling that sits outside the package proper — setup, diagnostics,
bulk maintenance and the end-to-end check. Nothing here is imported by
`src/llmwiki/`; each file is a standalone entry point (`python scripts/x.py`).

All of them read `.env` via `llmwiki.config.load_settings()`, so run them from
an activated venv with a real `.env` (`cp .env.example .env` and fill it in —
see `implement-plan.md` Part I §6), unless the script has an offline mode noted below.

| Script | Purpose | Needs |
|---|---|---|
| [`smoke_flow.py`](smoke_flow.py) | End-to-end flow check: ingest → compile → search → answer | `--offline` needs nothing; otherwise real backends |
| [`verify_capture.py`](verify_capture.py) | Capture a YouTube URL (`POST /ingest`) and/or a PDF (`POST /upload`) through a running service, then prove they landed in `raw/`, `wiki/` and the vector index - via the API and directly against the backends | A running service + the `.env` it runs with |
| [`bootstrap_indexes.py`](bootstrap_indexes.py) | Create/verify the two Vectorize indexes + their metadata indexes | Cloudflare creds |
| [`reset_vectorize.py`](reset_vectorize.py) | Wipe both Vectorize indexes and recreate them empty (fresh start) | Cloudflare creds |
| [`check_cloudflare_setup.py`](check_cloudflare_setup.py) | Diagnose R2 / Vectorize / Workers AI setup before flipping backends on | Cloudflare creds |
| [`browse_vectors.py`](browse_vectors.py) | Read the actual content stored in a Vectorize index (chunk text, gist metadata) | Cloudflare creds |
| [`backfill.py`](backfill.py) | Bulk re-compile every captured source (after a compiler/prompt change) | Real backends |
| [`eval_answer.py`](eval_answer.py) | Score answer quality against the golden set; push it to LangSmith; run experiments; export failures; promote corrections | `--offline` needs nothing; real backends otherwise; `--push/--langsmith/--promote-feedback` need `LANGSMITH_API_KEY` |
| [`check_local_llm.py`](check_local_llm.py) | Diagnose a self-hosted vLLM / llama.cpp endpoint before (and after) flipping `config/ops.py`'s local routing on | `VLLM_*`/`LLAMACPP_*` in `.env`; a reachable server |
| [`probe_query_graph.py`](probe_query_graph.py) | Run one question through the query graph under one or a matrix of bound settings; check the code-enforced invariants; verify the LangSmith trace | `--offline` needs nothing; real backends otherwise; `--verify-trace` needs `LANGSMITH_TRACING=true` |

---

## `smoke_flow.py`

End-to-end check: ingest a source, compile it, search it, answer a question
from it. Deliberately not a pytest test — it's slow, stateful, and prints a
narrative instead of asserting silently. Exits non-zero at the first failing
step and names it.

```bash
python scripts/smoke_flow.py --offline          # no network, no keys - runs in the pre-commit gate
python scripts/smoke_flow.py                    # real backends, from .env
python scripts/smoke_flow.py --url https://karpathy.github.io/2019/04/25/recipe/
python scripts/smoke_flow.py --question "What is retrieval-augmented generation?"
python scripts/smoke_flow.py --keep              # don't clean up the ingested source afterward
```

## `verify_capture.py`

The front-door counterpart of `smoke_flow.py`: instead of calling the Python
core, it POSTs to a **running** service — `/ingest` with a YouTube URL,
`/upload` with a PDF — polls `GET /sources/{id}` to completion, and then reads
back every place a source must end up, twice: once through the API
(`/concepts`, `/page/{slug}`, `/search`) and once straight from the backends
in `.env` (object store, vector store, embedder — the same factories the
server uses). Any check that does not hold is named and the exit code is 1.

What it proves per source:

- **`raw/`** — `meta.json` (modality, url), `original.*` byte-identical to the
  uploaded PDF (or a well-formed transcript segment list for YouTube) and
  hashing to `meta.json`'s sha256, `extracted.md` non-empty.
- **`wiki/`** — `wiki/sources/{id}.md` exists; `gists.json` lists the source
  note and at least one concept/entity page citing the source; each such page
  has the source in its front matter, a gist and a body; `index.md` links
  them; `GET /concepts` and `GET /page/{slug}` return the same pages.
- **vectors** — every chunk of the source in the chunks index: count equals
  the pipeline's `chunk_count`, ids are `{source_id}:0..n-1`, and each stored
  text is exactly `extracted.md[char_start:char_end]`; plus `GET /search` for
  the source's own opening text surfaces its chunks or a page citing it.

```bash
python scripts/verify_capture.py --pdf tests/fixtures/sample.pdf            # against http://localhost:$API_PORT
python scripts/verify_capture.py --youtube https://www.youtube.com/watch?v=...
python scripts/verify_capture.py --youtube ... --pdf ... --base-url http://localhost:8010
python scripts/verify_capture.py --pdf ... --rest-only    # server runs on backends this .env cannot reach
python scripts/verify_capture.py --pdf ... --cleanup      # delete the sources afterwards (kept by default)
```

The direct read-back needs the script's `.env` to point at the server's
backends (same R2 bucket / Vectorize index, or the same `LOCAL_STORAGE_PATH`
when the server is the bind-mounted `dev` container); the script refuses to
start when `/healthz` reports different backends than `.env`. With
`VECTOR_BACKEND=memory` the server's vectors are process-local, so only the
`GET /search` half of the vector check runs. `tests/unit/
test_verify_capture_script.py` pins the checks against the app under
`TestClient`, including the failures they exist to catch.

## `bootstrap_indexes.py`

Creates or verifies the `llmwiki-chunks` / `llmwiki-gists` Vectorize indexes
and their metadata indexes (`source_id`, `slug`, `type`). Metadata indexes
must exist *before* any insert that filters on them, so this is a setup step,
not something the app does lazily.

```bash
python scripts/bootstrap_indexes.py --check     # verify only, changes nothing - run this first
python scripts/bootstrap_indexes.py --create    # create whatever is missing
```

`--check` reports `MISSING` or a `DIMENSION MISMATCH` per index; a dimension
mismatch means the index must be deleted and recreated at the dimension
`EMBEDDING_DIM` in `.env` specifies.

## `reset_vectorize.py`

Discards every vector and leaves the indexes empty, dimensioned and
metadata-indexed exactly as `bootstrap_indexes.py` would make them. Vectorize
has no "delete all" and no listing, so a reset is delete-index + create-index;
the script polls until Cloudflare has released the name in between.

```bash
python scripts/reset_vectorize.py                  # report: dimensions and vector count per index, change nothing
python scripts/reset_vectorize.py --yes            # delete and recreate both indexes
python scripts/reset_vectorize.py --yes --index gists   # only one of them
```

There is no undo. Only Vectorize is touched — `raw/` and `wiki/` stay as they
are, and are what the vectors get rebuilt from: `process_source` per source
(or re-ingest) for chunks, `backfill.py --force` for gists. Also the fix for a
`DIMENSION MISMATCH` from `bootstrap_indexes.py --check` after changing
`EMBEDDING_DIM`.

## `check_cloudflare_setup.py`

Six independent checks, each pass/fail with a remediation hint — the thing to
run before setting `STORAGE_BACKEND=r2`, `VECTOR_BACKEND=vectorize`,
`EMBEDDING_BACKEND=workers_ai` in `.env` for the first time, or whenever
something in that chain looks broken.

1. Config — required `.env` values are present (not `changeme`)
2. R2 — bucket put/get/delete round trip
3. Cloudflare API token — valid and active
4. Workers AI — one embedding call; dimension must match `EMBEDDING_DIM`
5. Vectorize — both indexes exist, dimensions match, metadata indexes present
6. Vectorize — live upsert/query/delete round trip (the only check that
   actually proves the token's *Write* permission, not just read access)

```bash
python scripts/check_cloudflare_setup.py           # full check, including the live write test
python scripts/check_cloudflare_setup.py --quick   # read-only: skip step 6
```

Costs a small amount of Workers AI usage; the Vectorize write test cleans up
the single probe vector it creates.

## `browse_vectors.py`

Reads what's *actually* in a Vectorize index — chunk text and gist metadata,
not just plumbing. Vectorize has no "list everything" endpoint, only
nearest-neighbor query (optionally metadata-filtered) and fetch-by-id, so the
script exposes both:

```bash
# every chunk vector belonging to one source (exact, complete)
python scripts/browse_vectors.py --index chunks --source-id a0998b8b345992ba

# semantic search over the gist index (wiki page summaries)
python scripts/browse_vectors.py --index gists --query "vector databases"

# fetch specific vector ids directly, no query involved
python scripts/browse_vectors.py --index chunks --id a0998b8b345992ba:0 a0998b8b345992ba:1

# quick peek with no filter - NOT a guaranteed full enumeration past --limit
python scripts/browse_vectors.py --index gists --limit 50
```

Each result prints the vector id, score (when querying), and its stored
metadata (`source_id`, `slug`, `type`, `title`, `section`, `url`) plus the
chunk text or gist summary (truncated to 2000 chars). `--source-id` is the
one flag that gives an exact, complete list — the no-filter fallback just
queries the zero vector and shows whatever comes back, capped at `--limit`
(Vectorize caps `topK` at 100 regardless).

## `backfill.py`

Bulk re-compiles every source already captured under `raw/` — for after a
compiler or prompt change, when the wiki should be rebuilt without
re-ingesting anything. Runs serially through the normal (non-batch) compile
path, so a large backfill costs full price; the 50% Batch API discount isn't
wired up yet.

```bash
python scripts/backfill.py --dry-run          # list what would be recompiled, changes nothing
python scripts/backfill.py --limit 20         # recompile at most 20 sources
python scripts/backfill.py --force            # recompile even sources already in the wiki
```

Prints running cost and a per-source result line; exits non-zero if any
source failed (failures are listed on stderr, the run continues past them).

## `eval_answer.py`

The eval half of Phase 1-D (technical document §10). Answers every example of
a golden set (`question`, `expected_sources`, `must_mention` per JSONL line)
through `tools.answer` and scores it: `citations_resolve`,
`expected_source_cited`, `must_mention` (gated — exit 1 below 1.0 in a local
run), `tool_calls` (metric) and, with `--judge`, `judge_grounded` (one
`judge_answer` LLM call per example).

```bash
python scripts/eval_answer.py --offline                          # fake adapters + fixture docs + fixture set; the pre-commit gate
python scripts/eval_answer.py                                    # local table against the real backends in .env
python scripts/eval_answer.py --dataset eval/my-golden.jsonl     # your corpus's own set (the fixture ids do not exist in a real corpus)
python scripts/eval_answer.py --push                             # mirror the JSONL to LANGSMITH_EVAL_DATASET
python scripts/eval_answer.py --langsmith --judge                # run as a LangSmith experiment; metadata: version, git sha, routes, bounds
python scripts/eval_answer.py --export-failures failures.jsonl   # failing rows with the actual output, to edit into the set
python scripts/eval_answer.py --promote-feedback                 # corrected runs (POST /feedback / llmwiki feedback) -> new examples
docker compose --profile ops run --rm eval --langsmith           # the same on a box (compose `eval` service, ops profile)
```

`--offline` writes to `./.data-eval` (override with `LOCAL_STORAGE_PATH`).

## `check_local_llm.py`

The Phase 1-C diagnostic (`docs/phase1-manual-test-plan-C-D.md` §2). Six
checks between `.env` and a working local compile, each pass/fail with a
remediation hint, in the same style as `check_cloudflare_setup.py`:

1. Config — which local providers are active (`VLLM_API_KEY`/`VLLM_BASE_URL`,
   `LLAMACPP_*`); the base URL looks like an OpenAI-compatible root
2. Reachable — `GET {base_url}/models` answers 200 with the bearer key; lists
   the served model ids
3. Routing — every `config/ops.py` row naming a local provider names a model
   the server serves (`--served-model-name` / `--alias` must match exactly)
4. Completion — one plain completion through the project's own adapter
   (`LangChainLLM` over `ChatOpenAI`, the class the router builds); real,
   nonzero token counts
5. Structured — one *forced tool call*, the shape every compile stage uses; a
   server that answers this in prose compiles nothing (vLLM: guided decoding /
   `--enable-auto-tool-choice --tool-call-parser hermes`; llama.cpp: `--jinja`)
6. Routed op (`--op`) — the real `factory.llm_client(...).complete(op=...)`,
   i.e. exactly what the compiler calls; prints the `CostRecord` (`$0.0000`
   is expected for an unpriced local model; tokens must be real)

```bash
python scripts/check_local_llm.py                        # 1-5 for every active local provider
python scripts/check_local_llm.py --quick                # 1-3 only: no tokens generated
python scripts/check_local_llm.py --provider vllm        # one of them
python scripts/check_local_llm.py --op summarize_source --op plan_compile   # + 6, after flipping ops.py
```

Exit 1 on any failure — including "no local provider is active", since the
only reason to run it is that you expect one to be.

## `probe_query_graph.py`

The Phase 1-D live probe (`docs/phase1-manual-test-plan-C-D.md` §3). The unit
suite pins the bounded tool loop with scripted decisions; this runs it with a
real model and corpus, applying `AGENT_MAX_TOOL_CALLS` /
`AGENT_WEB_SEARCH_POLICY` / `WEB_SEARCH_BACKEND` / `AGENT_MAX_WEB_SEARCHES`
overrides *in-process* (no `.env` edit, no restart), and checks what the code
guarantees regardless of what the model wanted: `len(steps) <= cap`,
`search_web` count `<= AGENT_MAX_WEB_SEARCHES` and never under policy `off`,
backend `none`, or policy `weak` without the chunk fallback; every citation
resolves; no external ref is a citation; a non-empty answer. Per run it prints
the tool calls made and the model's *reason* for each (the graph's DEBUG
decisions, including why the loop stopped), citations, external refs, the
`run_id` and latency. `--verify-trace` fetches the LangSmith trace and checks
the node / LLM-run names against technical document §10.2.

```bash
python scripts/probe_query_graph.py -q "..."                        # the .env settings, one run
python scripts/probe_query_graph.py -q "..." --max-tool-calls 1 -v  # override a bound; show decisions
python scripts/probe_query_graph.py -q "..." --matrix               # caps 0/1/N x every policy the backend allows
python scripts/probe_query_graph.py -q "..." --web-search-backend fake --web-search-policy always
python scripts/probe_query_graph.py --offline --matrix              # no keys: fakes + fixture docs + fixture question
python scripts/probe_query_graph.py -q "..." --verify-trace         # + LangSmith run-tree check
python scripts/probe_query_graph.py -q "..." --json runs.jsonl      # append one record per run
```

Exit 1 when any invariant or trace check fails; `--offline` writes to
`./.data-probe` (override with `LOCAL_STORAGE_PATH`). The offline double
answers `agent_step` with `answer` at once, so `steps` is always 0 there —
the loop's own logic is `tests/unit/test_agent_graph.py`; this script's
offline value is the invariant/matrix plumbing (`tests/unit/test_phase1_scripts.py`).
