# Scripts

Operational tooling that sits outside the package proper — setup, diagnostics,
bulk maintenance and the end-to-end check. Nothing here is imported by
`src/llmwiki/`; each file is a standalone entry point (`python scripts/x.py`).

All of them read `.env` via `llmwiki.config.load_settings()`, so run them from
an activated venv with a real `.env` (`cp .env.example .env` and fill it in —
see `implement-plan.md` §6), unless the script has an offline mode noted below.

| Script | Purpose | Needs |
|---|---|---|
| [`smoke_flow.py`](smoke_flow.py) | End-to-end flow check: ingest → compile → search → answer | `--offline` needs nothing; otherwise real backends |
| [`bootstrap_indexes.py`](bootstrap_indexes.py) | Create/verify the two Vectorize indexes + their metadata indexes | Cloudflare creds |
| [`check_cloudflare_setup.py`](check_cloudflare_setup.py) | Diagnose R2 / Vectorize / Workers AI setup before flipping backends on | Cloudflare creds |
| [`browse_vectors.py`](browse_vectors.py) | Read the actual content stored in a Vectorize index (chunk text, gist metadata) | Cloudflare creds |
| [`backfill.py`](backfill.py) | Bulk re-compile every captured source (after a compiler/prompt change) | Real backends |

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
