# Code review plan: moving pre-Phase-2 sources into domains (2026-09-26)

**Audience:** reviewers of the `scripts/migrate_domains.py` change. **HISTORY:**
the 2026-09-26 entry "`scripts/migrate_domains.py`".

## What changed and why

`migrate_phase2.py` upgrades a deployment without moving anything: pre-Phase-2
sources stay in `general`. This change adds the optional second step: filing
those sources under registered domains. It is the "`domains reassign`" item in
`TODOS.md`, implemented as a batch migration and not yet as a per-source
command.

| Area | File | Change |
|---|---|---|
| Pipeline | `src/llmwiki/pipeline/ingest.py` | `propose_domain` (route, persist nothing) and `reassign` (move one source) |
| Wiki | `src/llmwiki/wiki/reassign.py` (new) | `prune_moved_sources`: delete the old domain's pages whose sources all moved |
| Tools | `src/llmwiki/tools.py` | `propose_domain`, `reassign_source`, `prune_moved_sources` (not MCP tools) |
| Vector | `src/llmwiki/vector/memory.py` | `delete_by_ids`, the same extra Vectorize already has |
| Script | `scripts/migrate_domains.py` (new) | `plan` / `apply` with a reviewable JSON plan |

## Where the risk is (review first)

1. **The ordering in `IngestPipeline.reassign`.**
   - The intended order is: embed and compile into the target, then rewrite
     `routing.json`, then delete the old chunks. Check that no path deletes
     old chunks before the new compile succeeded.
   - Check that an aborted compile (budget) leaves routing untouched.
   - On a re-run, the compiler's `_already_compiled` skips a source the
     target manifest already holds. A source that crashed *mid*-compile is
     therefore not recompiled on the re-run. It is still moved, but with the
     pages that partial compile wrote. That is acceptable for a one-off
     migration; confirm you agree.
2. **Prune correctness** (`cited <= moved`).
   - Only pages whose every source moved are deleted. The overview is never
     deleted.
   - Mixed pages are kept. Their body text still reflects the moved source.
     This is a documented deviation from the TODO's "lint pass".
3. **Locks.** `domain_lock` is per process. The script must run while nothing
   else is capturing. The docstring, README and runbook all say so; nothing
   enforces it.
4. **`explicit=True` on a migrated routing.json.** An admin reviewed the plan,
   so the field is set to `True`. Its other meaning, "no model call was made",
   is not true when the router proposed the domain. The `reason` field
   records the origin (`migrate_domains: …`).

## Checklists

- **Layering:** `wiki/reassign.py` imports storage, vector and wiki only; the
  layering test passes.
- **No full scan on the ingest path:** unaffected. The script lists `raw/`
  once, as `backfill.py` does, and is an ops tool, not ingest.
- **Cost:**
  - `plan` makes one `route_domain` call per general source, or none with
    `--default`.
  - `apply` costs one normal compile per moved source, plus Workers AI
    embeddings.
  - Prune makes no LLM calls.
  - Everything is ledgered under the `migrate` writer.
- **Citations:** `raw/` is never touched, so kept pages' citations resolve.

## Not verified live

- Nothing ran against real R2, Vectorize or an LLM. Only the offline tree
  (local storage, memory vectors, fake LLM, SQLite FTS) was exercised.
- `VectorizeStore.delete_by_source` on the base chunks index, and
  `delete_by_ids` on the gists index, both depend on Vectorize's eventual
  consistency. A query straight after `apply` may still return a moved
  source's chunk from `general` for a short while.
- The router's quality on the real corpus is untested. Review the plan file
  before `apply`, and use `scripts/probe_domain_routing.py` to calibrate.
