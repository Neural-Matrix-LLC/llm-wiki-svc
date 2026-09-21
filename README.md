# llmwiki

A cost-effective, cloud-hosted research knowledge base. Captured sources are kept
immutable under `raw/`; an LLM compiles them into an interlinked markdown wiki
under `wiki/`. Retrieval is hybrid — the compiled wiki first, a vector index as
fallback — and every answer carries citations that resolve back to a real source.

- `llmwiki-KB-design.md` — architecture and scope (authoritative)
- `docs/implement-plan.md` — the implementation plan: Part I Phase 0 (milestones, runbook, tests), Part II packaging + Phase 1
- `HISTORY.md` — the change log

## Quickstart (offline, no keys)

```bash
# Creates the virtual environment and installs dependencies, including the
# dev extra (pytest, mypy, ruff, ...) - plain `uv sync` skips it
uv venv
source .venv/bin/activate
uv sync
cp .env.example .env

pytest                                  # unit tests, no network
python scripts/smoke_flow.py --offline  # end-to-end flow with fake adapters
llmwiki --offline ingest --file tests/fixtures/sample.pdf
llmwiki --offline concepts
```

`--offline` swaps in a filesystem store, an in-memory vector index, a hash-based
embedder and a scripted LLM. `.data/wiki/` opens directly as an Obsidian vault.

Five kinds of source go in through the same command — a PDF file, a blog URL,
a YouTube URL, pasted text, and a text file. What a source *is* is detected,
never declared: a link is fetched first and routed by the content type the
server actually returns, so a link to a paper is read as a PDF and a link to a
post is read as a page.

```bash
llmwiki --offline ingest --file paper.pdf                                   # PDF file
llmwiki --offline ingest --url "https://karpathy.github.io/2019/04/25/recipe/"   # blog
llmwiki --offline ingest --url "https://www.youtube.com/watch?v=fvIVGmwgk4w"     # YouTube
llmwiki --offline ingest --text "Kelly sizing maximizes long-run log wealth."    # pure text
llmwiki --offline ingest --file notes.md                                    # text file
git log --oneline | llmwiki --offline ingest --text -                       # pure text, from stdin
llmwiki --offline concepts
```

`--url` still fetches the page or YouTube transcript over the network;
`--offline` only keeps storage and the LLM local and fake. Drop `--offline`
once `.env` has real credentials.

Over HTTP: `POST /ingest` takes `{"url": ...}` or `{"text": ...}`, and
`POST /upload` takes a file. The MCP `ingest_source` tool takes `url` or
`text`. All of them call the same `tools.ingest_source()`.

The two are not interchangeable — `/ingest` reads a JSON body, so a file has to
go to `/upload` as multipart, not to `/ingest`:

```bash
TOKEN=...   # INGEST_API_TOKEN from .env
curl -sS -X POST http://localhost:8010/upload \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@practical-guide.pdf" -F "title=practical guide pdf"

curl -sS -X POST http://localhost:8010/ingest \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://karpathy.github.io/2019/04/25/recipe/"}'

curl -sS http://localhost:8010/sources/<source_id>   # poll to `done`
```

`--url` still fetches the page or YouTube transcript over the network;
`--offline` only keeps storage and the LLM local and fake. Drop `--offline`
once `.env` has real credentials.

Over HTTP: `POST /ingest` takes `{"url": ...}` or `{"text": ...}`, and
`POST /upload` takes a file. The MCP `ingest_source` tool takes `url` or
`text`. All of them call the same `tools.ingest_source()`.

The two are not interchangeable — `/ingest` reads a JSON body, so a file has to
go to `/upload` as multipart, not to `/ingest`:

```bash
TOKEN=...   # INGEST_API_TOKEN from .env
curl -sS -X POST http://localhost:8010/upload \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@practical-guide.pdf" -F "title=practical guide pdf"

curl -sS -X POST http://localhost:8010/ingest \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"url": "https://karpathy.github.io/2019/04/25/recipe/"}'

curl -sS http://localhost:8010/sources/<source_id>   # poll to `done`
```

## Running against real backends

Fill `.env` with Cloudflare and Anthropic credentials, then:

```bash
python scripts/bootstrap_indexes.py --check   # verify Vectorize indexes exist
llmwiki serve                                 # REST on :8010 (API_PORT in .env.example), MCP at /mcp
```

`implement-plan.md` Part I §6 is the full operational runbook — creating the R2 bucket,
minting both kinds of Cloudflare token, and confirming the embedding dimension
before any index is created. `scripts/README.md` documents every script under
`scripts/` (setup, diagnostics, browsing what's in Vectorize, bulk backfill,
evaluation) with usage examples.

### Query agent, tracing and evaluation

`llmwiki ask` (and `GET /answer`) runs a bounded LangGraph loop: the wiki-first
retrieval, then up to `AGENT_MAX_TOOL_CALLS` evidence-gathering tool calls
(`search_wiki`, `search_chunks`, `get_page`, optionally `search_web`), then the
answer with verified citations. `AGENT_MAX_TOOL_CALLS=0` gives the plain
single-call behaviour. With `LANGSMITH_TRACING=true` every answer is one
LangSmith trace and carries a `run_id`.

```bash
python scripts/eval_answer.py --offline                # the answer-quality golden set, no keys
python scripts/eval_answer.py --langsmith --judge      # as a LangSmith experiment, with the LLM judge
llmwiki feedback <run_id> --score 0 --correction "…"   # file a correction; --promote-feedback turns it into an example
```

`docs/llm-wiki-technical-document.md` §3.3 (the graph) and §10 (tracing,
evaluation, the correction loop) have the details; `docs/phase1-testing-guide.md`
§5 is the step-by-step.

## Docker

```bash
docker compose up --build                     # reads .env
docker compose --profile offline up api-offline   # no keys needed, port 8001
docker compose --profile test run --rm smoke      # end-to-end check in the image
```

### Dev mode (no rebuild after a code change)

The `dev` service bind-mounts this working tree into the container and installs
`llmwiki` editable, so an edit under `src/` is live on the next request —
uvicorn `--reload` restarts the app in place. Rebuild only when
`requirements.txt` or the `Dockerfile` changes.

```bash
docker compose --profile dev up dev               # http://localhost:8011 (DEV_PORT)
docker compose --profile test run --rm pytest     # the unit suite, in the same image
docker compose --profile dev run --rm dev llmwiki lint
docker compose --profile dev build dev            # only after a dependency change
```

It runs as `DEV_UID:DEV_GID` (default `1000:1000`) so files it writes through
the mounts — `./.data`, caches — stay owned by you rather than by root. Check
with `id -u` and override in `.env` if your ids differ.

## Layout

`src/llmwiki/` is layered, and the layering is enforced by a test
(`tests/unit/test_layering.py`), not by convention:

```
models/  →  storage/ extractors/ embedding/ vector/ llm/ websearch/  →  wiki/ agent/
         →  pipeline/  →  tools.py (+ eval/)  →  api/ mcp/ cli.py channels/
```

Business logic lives in the core package. `api/`, `mcp/` and `cli.py` validate
input, call one function in `tools.py`, and serialize the result.
