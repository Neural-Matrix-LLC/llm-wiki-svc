# llmwiki

A cost-effective, cloud-hosted research knowledge base. Captured sources are kept
immutable under `raw/`; an LLM compiles them into an interlinked markdown wiki
under `wiki/`. Retrieval is hybrid — the compiled wiki first, a vector index as
fallback — and every answer carries citations that resolve back to a real source.

- `llmwiki-KB-design.md` — architecture and scope (authoritative)
- `implement-plan.md` — the Phase 0 plan: milestones, runbook, tests
- `HISTORY.md` — the change log

## Quickstart (offline, no keys)

```bash
# Creates the virtual environment and installs dependencies
uv venv
source .venv/bin/activate
uv sync
cp .env.example .env

pytest                                  # 181 unit tests, no network
python scripts/smoke_flow.py --offline  # end-to-end flow with fake adapters
llmwiki --offline ingest --file tests/fixtures/sample.pdf
llmwiki --offline concepts
```

Offline mode swaps in a filesystem store, an in-memory vector index, a hash-based
embedder and a scripted LLM. `.data/wiki/` opens directly as an Obsidian vault.

## Running against real backends

Fill `.env` with Cloudflare and Anthropic credentials, then:

```bash
python scripts/bootstrap_indexes.py --check   # verify Vectorize indexes exist
llmwiki serve                                 # REST on :8000, MCP at /mcp
```

`implement-plan.md` §6 is the full operational runbook — creating the R2 bucket,
minting both kinds of Cloudflare token, and confirming the embedding dimension
before any index is created.

## Docker

```bash
docker compose up --build                     # reads .env
docker compose --profile offline up api-offline   # no keys needed, port 8001
docker compose --profile test run --rm smoke      # end-to-end check in the image
```

## Layout

`src/llmwiki/` is layered, and the layering is enforced by a test
(`tests/unit/test_layering.py`), not by convention:

```
models/  →  storage/ extractors/ embedding/ vector/ llm/  →  wiki/ agent/
         →  pipeline/  →  tools.py  →  api/ mcp/ cli.py
```

Business logic lives in the core package. `api/`, `mcp/` and `cli.py` validate
input, call one function in `tools.py`, and serialize the result.
