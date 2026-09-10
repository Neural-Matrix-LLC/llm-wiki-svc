# Phase 0 Implementation Plan — LLM Wiki KB

**Version:** 1.1
**Date:** 2026-08-30
**Derived from:** `llmwiki-KB-design.md` v1.2 (§4.6 hybrid interface, §4.4 compilation strategy, §5 Phase 0)
**Status:** Ready to execute, pending the open items in §15

**Changes since 1.0:** added §4 (package architecture and code handling), §6 (operational runbook —
R2, Vectorize, Workers AI provisioning and day-to-day operations), and §12.5–§12.6 (worked unit-test
examples and an end-to-end smoke flow script). Sections renumbered accordingly.

> **Reference convention:** "design doc §X" points at `llmwiki-KB-design.md`. A bare "§X" points at
> a section of *this* plan.

**Contents**

| § | Section | § | Section |
|---|---|---|---|
| 1 | What Phase 0 Is | 9 | Ingest Sequence |
| 2 | Locked Decisions | 10 | Canonical Tool Surface |
| 3 | Target Repository Layout | 11 | LLM & Embedding Routing |
| 4 | **Package Architecture & Code Handling** | 12 | **Testing Plan** (incl. worked examples + smoke script) |
| 5 | Data Contracts | 13 | Environment Variables |
| 6 | **Operational Runbook** | 14 | Success Criteria |
| 7 | Milestones | 15 | Open Items |
| 8 | The Incremental Compiler | 16 | Documentation Obligations |

---

## 1. What Phase 0 Is

A working **vertical slice for one researcher**: capture a source → store it immutably →
extract → chunk + embed → incrementally compile wiki pages → answer a question with citations,
exposed over both HTTP and MCP.

**In scope (design doc §5, Phase 0):**

- Object storage for `raw/` + `wiki/`
- One capture channel
- Extraction: PDF, web page, YouTube transcript
- Vector store + retrieval
- Incremental wiki compiler with hierarchical index
- Core logic as an importable Python package
- FastAPI + MCP exposing the canonical tool surface
- Obsidian-readable wiki output
- Cheap cloud models only

**Explicitly out of scope — do not build (design doc §5, Phases 1–3):**

| Deferred | Phase |
|---|---|
| Telegram / WhatsApp / Signal / email channels | 1 |
| Local LLM (Ollama / vLLM) inference | 1 |
| Multi-user auth, workspaces, RBAC | 1→3 |
| Image / page-as-image multimodal extraction | 2 |
| Reranking, domain partitioning, domain routing | 2 |
| Custom mobile app, PWA share target | 1→3 |
| Git-backed wiki versioning | 1+ |
| Dedup beyond exact content-hash matching | 1 |

If a task during implementation appears to need one of these, stop and confirm the phase before building it.

---

## 2. Locked Decisions

These answer design doc §7 questions 2, 3 and 7 for Phase 0 only. Revisit at the Phase 1 gate.

| # | Decision | Rationale | Reversal cost |
|---|---|---|---|
| D1 | **Storage: Cloudflare R2** (S3-compatible, via `boto3`) for both `raw/` and `wiki/` | Zero egress fees, lowest fixed cost, S3 API means the provider is swappable (design doc §4.5) | Low — hidden behind `ObjectStore` protocol |
| D2 | **Vectors: Cloudflare Vectorize** | Design doc §4.2 rates it "extremely low" cost / minimal ops; same provider as R2 | Low — hidden behind `VectorStore` protocol |
| D3 | **Embeddings: Cloudflare Workers AI** (`@cf/baai/bge-base-en-v1.5`, 768-dim) | Same account/token as R2+Vectorize; pay-per-use; Anthropic has no embeddings API | Low — hidden behind `Embedder` protocol; local `sentence-transformers` on the RTX 4060 is the drop-in fallback |
| D4 | **Capture: authenticated FastAPI webhook** (`POST /ingest`) as the only Phase 0 channel | Testable with `curl`, no third-party account, and the Phase 1 Telegram bot becomes a thin adapter that posts to this same endpoint rather than a parallel code path | N/A — additive |
| D5 | **LLM: `claude-haiku-4-5` by default**, `claude-sonnet-5` only for the compiler's patch-generation step behind an env flag | Design doc §4.3 + §6: cheap cloud models by default. Haiku is $1/$5 per MTok vs Sonnet 5 at $2/$10 | Config change |
| D6 | **Interface: hybrid from day one** — core Python package, FastAPI on top, MCP mounted in the same process via FastMCP | Design doc §4.6 recommended path; avoids a rewrite at the Phase 1 gate | High if skipped, so do it now |
| D7 | **Single-process orchestration** (FastAPI `BackgroundTasks` + an in-process asyncio queue), not a serverless queue | One researcher, one writer. §6's "serverless/event-driven" matters at Phase 1 volume; premature here | Medium — ingestion is written as a pure function of `(source_id)` so it can move behind a real queue unchanged |

**Model ID note:** use the exact strings `claude-haiku-4-5` and `claude-sonnet-5`. Never append a
date suffix. Haiku 4.5 does **not** support `output_config.effort` (it errors) and uses the older
`thinking: {type: "enabled", budget_tokens: N}` form if thinking is ever needed; Sonnet 5 uses
`thinking: {type: "adaptive"}` and rejects `budget_tokens`. Keep this difference inside the LLM
client wrapper, not scattered through call sites.

---

## 3. Target Repository Layout

Follows the layout in `~/.claude/CLAUDE.md`.

```
llm-wiki-svc/
├── CLAUDE.md
├── HISTORY.md                     # created in M0 — mandatory, every change logged
├── README.md
├── implement-plan.md              # this file
├── llmwiki-KB-design.md
├── pyproject.toml                 # package metadata + abstract deps (§4.1)
├── requirements.txt               # pinned lockfile from pip freeze (§4.1)
├── .env.example                   # committed, kept in sync with §13
├── .env                           # never committed
├── .gitignore                     # .env, .venv/, __pycache__/, *.pyc, .pytest_cache/
│
├── src/llmwiki/
│   ├── __init__.py                # __version__ + the public surface (§4.4)
│   ├── py.typed                   # ships type information to importers
│   ├── config.py                  # pydantic-settings; the ONLY module that reads os.environ
│   ├── factory.py                 # builds concrete adapters from settings (§4.3)
│   ├── models/                    # pydantic schemas — no raw dicts across boundaries
│   │   ├── source.py              # SourceRef, SourceMeta, ExtractedDoc, SourceStatus
│   │   ├── chunk.py               # Chunk, ChunkMetadata, SearchHit
│   │   ├── page.py                # WikiPage, PageFrontMatter, PageGist
│   │   └── plan.py                # CompilePlan, CompileOp, CompileResult
│   ├── storage/
│   │   ├── base.py                # ObjectStore protocol
│   │   ├── r2.py                  # boto3 S3-compatible impl
│   │   ├── local.py               # filesystem impl — unit tests + offline dev
│   │   └── layout.py              # the ONLY place that builds object keys (§5.1)
│   ├── extractors/
│   │   ├── base.py                # Extractor protocol + registry
│   │   ├── pdf.py                 # pymupdf
│   │   ├── web.py                 # httpx + trafilatura
│   │   └── youtube.py             # youtube-transcript-api
│   ├── embedding/
│   │   ├── base.py                # Embedder protocol
│   │   ├── workers_ai.py          # Cloudflare Workers AI
│   │   └── fake.py                # deterministic hash-based, for unit tests
│   ├── vector/
│   │   ├── base.py                # VectorStore protocol
│   │   ├── vectorize.py           # Cloudflare Vectorize REST
│   │   └── memory.py              # numpy cosine, for unit tests
│   ├── llm/
│   │   ├── base.py                # LLMClient protocol
│   │   ├── anthropic_client.py    # Anthropic SDK: routing, caching, cost accounting
│   │   └── fake.py                # scripted responses, for unit tests + offline smoke
│   ├── wiki/
│   │   ├── pages.py               # read/write/parse markdown + front matter
│   │   ├── gists.py               # hierarchical index + gist manifest
│   │   ├── compiler.py            # incremental compiler (§8) — the core of Phase 0
│   │   └── lint.py                # scheduled global lint, NOT run on ingest
│   ├── chains/prompts/            # every prompt template lives here as its own file
│   │   ├── summarize_source.md
│   │   ├── plan_compile.md
│   │   ├── patch_page.md
│   │   ├── create_page.md
│   │   └── answer_query.md
│   ├── pipeline/
│   │   └── ingest.py              # capture → extract → embed → compile orchestration
│   ├── agent/
│   │   └── query.py               # wiki-first, RAG-fallback answering with citations
│   ├── tools.py                   # canonical tool surface (§10) — the shared brain
│   ├── api/
│   │   ├── app.py                 # FastAPI app; mounts MCP
│   │   └── routes.py              # /ingest, /search, /page, /compile, /healthz
│   ├── mcp/
│   │   └── server.py              # FastMCP over the same tools.py functions
│   └── cli.py                     # `llmwiki ingest|search|compile|lint|cost`
│
├── tests/
│   ├── conftest.py                # fixtures wiring the fake adapters (§12.5)
│   ├── doubles.py                 # SpyObjectStore, ScriptedLLM
│   ├── factories.py               # make_extracted_doc, seed_gists
│   ├── fixtures/                  # golden inputs: sample.pdf, sample.html, transcript.json
│   ├── unit/                      # no network, no API calls
│   └── integration/               # @pytest.mark.integration, real R2/Vectorize/Anthropic
├── scripts/
│   ├── bootstrap_indexes.py       # create/verify Vectorize indexes (§6.4)
│   ├── smoke_flow.py              # end-to-end flow check (§12.6)
│   └── backfill.py                # bulk re-compile via the Batch API
└── docs/                          # architecture notes; §16 doc obligations land here
```

**Rule that must hold:** `api/`, `mcp/` and `cli.py` contain no business logic. They validate
input, call a function in `tools.py`, and serialize the result. This is what makes D6 cheap.

---

## 4. Package Architecture & Code Handling

The design doc calls the Python package "the brain" (design doc §4.6.1) and says the FastAPI and MCP
layers are glue over it. That only stays true if the boundaries are mechanical rather than aspirational.
This section defines how the code is packaged, how layers may depend on each other, and how that is enforced.

### 4.1 Packaging and installation

**src-layout, installed editable.** `src/` is not on `sys.path` by accident — it is on the path
because the package is installed. This is what lets tests, scripts, notebooks and the CLI all write
`from llmwiki.wiki.compiler import Compiler` with no `sys.path` manipulation anywhere.

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "llmwiki"
dynamic = ["version"]
requires-python = ">=3.11"
dependencies = [
    "anthropic>=1.0",
    "boto3",
    "fastapi",
    "fastmcp",
    "httpx",
    "numpy",
    "pydantic>=2",
    "pydantic-settings",
    "pymupdf",
    "python-dotenv",
    "python-frontmatter",
    "trafilatura",
    "uvicorn",
    "youtube-transcript-api",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-mock", "pytest-asyncio", "mypy", "ruff", "respx"]

[project.scripts]
llmwiki = "llmwiki.cli:main"

[tool.setuptools.dynamic]
version = {attr = "llmwiki.__version__"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
markers = ["integration: hits real external services; opt in with -m integration"]
addopts = "-m 'not integration'"

[tool.mypy]
files = ["src/llmwiki"]
disallow_untyped_defs = true
```

**`pyproject.toml` vs `requirements.txt`.** `~/.claude/CLAUDE.md` mandates `requirements.txt` +
`pip freeze`. Both files are committed and each has one job:

- `pyproject.toml` declares **abstract** dependencies (what the package needs to work at all).
- `requirements.txt` is the **pinned lockfile** — exactly what is installed in the working `.venv`,
  so a fresh clone reproduces the environment byte for byte.

Adding a dependency is a four-step procedure, all in one change:

```bash
source .venv/bin/activate
pip install <package>                       # 1. install into the venv
#                                             2. add it to pyproject.toml [project.dependencies]
pip freeze --exclude-editable > requirements.txt   # 3. re-pin the lockfile
#                                             4. note it in HISTORY.md
```

`--exclude-editable` keeps the `-e .` self-reference out of the lockfile, which otherwise breaks
`pip install -r requirements.txt` on a fresh clone.

**Versioning.** `__version__` lives in exactly one place, `src/llmwiki/__init__.py`, and
`pyproject.toml` reads it dynamically. Bump the minor version at each milestone (M0 → `0.1.0`,
M8 → `0.9.0`), and `1.0.0` when the §14 success criteria are met. The version is stamped into
`/healthz` and into every `cost.jsonl` record so a cost regression can be traced to a code version.

### 4.2 Layers and the dependency rule

Dependencies point **inward and downward only**. Nothing in a lower layer may import from a higher one.

```
       L0  models/            pure pydantic schemas, zero I/O, zero llmwiki imports
            │
       L1  storage/  extractors/  embedding/  vector/  llm/
            │        one adapter family each; siblings never import each other
            │
       L2  wiki/  agent/       domain logic; depends on L1 protocols, never on concrete adapters
            │
       L3  pipeline/           orchestration; wires L2 components into the ingest flow
            │
       L4  tools.py            the canonical surface — the only thing L5 is allowed to call
            │
       L5  api/  mcp/  cli.py  transport glue; no business logic, no direct L0–L3 imports
```

| Layer | Responsibility | May import | Must not import |
|---|---|---|---|
| `models/` | Data shapes crossing every boundary | stdlib, pydantic | anything in `llmwiki` |
| `storage/` | Bytes in/out of an object store | `models` | any other L1 sibling, L2+ |
| `extractors/` | Bytes → `ExtractedDoc` | `models` | `vector`, `wiki`, L2+ |
| `embedding/` | Text → vectors | `models` | any other L1 sibling, L2+ |
| `vector/` | Vector upsert/query | `models` | any other L1 sibling, L2+ |
| `llm/` | Prompt → structured response, with cost accounting | `models` | any other L1 sibling, L2+ |
| `wiki/` | Pages, gists, the incremental compiler | `models`, L1 **protocols** | `api`, `mcp`, `pipeline`, `agent` |
| `agent/` | Wiki-first + RAG-fallback answering | `models`, L1 protocols, `wiki` | `api`, `mcp`, `pipeline` |
| `pipeline/` | Ingest orchestration | everything L0–L2 | `api`, `mcp` |
| `tools.py` | The six canonical tools + CLI helpers | everything L0–L3, `factory` | `api`, `mcp` |
| `api/`, `mcp/`, `cli.py` | Transport, auth, serialization | `tools`, `models`, `config` | L1–L3 directly |

`config.py` and `factory.py` sit outside the ladder: `config` is importable everywhere,
and `factory` is imported only by `tools.py` and the entry points.

This is enforced by a test, not by good intentions — `tests/unit/test_layering.py` in §12.5 walks the
AST of every module and fails on a forbidden import edge.

### 4.3 The adapter pattern for every external service

Four things in Phase 0 touch a network: object storage, embeddings, the vector store, and the LLM.
Each follows the identical three-file shape — protocol, real adapter, fake adapter — and each is
selected at runtime by `factory.py`. This is what makes unit tests offline (§12.3) and what makes
D1/D2/D3 cheap to reverse.

```python
# src/llmwiki/vector/base.py
from typing import Protocol
from llmwiki.models.chunk import SearchHit

class VectorStore(Protocol):
    """Upsert and query dense vectors. Implementations must be safe to call concurrently."""

    def upsert(self, index: str, ids: list[str], vectors: list[list[float]],
               metadata: list[dict]) -> None: ...

    def query(self, index: str, vector: list[float], k: int = 5,
              where: dict | None = None) -> list[SearchHit]: ...

    def delete_by_source(self, index: str, source_id: str) -> int: ...
```

```python
# src/llmwiki/factory.py
from functools import lru_cache
from llmwiki.config import Settings, settings
from llmwiki.storage.base import ObjectStore
from llmwiki.vector.base import VectorStore

@lru_cache(maxsize=None)
def object_store(cfg: Settings = settings) -> ObjectStore:
    """Return the configured object store. Cached: one client per process."""
    if cfg.storage_backend == "local":
        from llmwiki.storage.local import LocalObjectStore
        return LocalObjectStore(root=cfg.local_storage_path)
    from llmwiki.storage.r2 import R2ObjectStore
    return R2ObjectStore(
        endpoint_url=cfg.r2_endpoint_url, bucket=cfg.r2_bucket,
        access_key_id=cfg.r2_access_key_id,
        secret_access_key=cfg.r2_secret_access_key.get_secret_value(),
    )

@lru_cache(maxsize=None)
def vector_store(cfg: Settings = settings) -> VectorStore:
    if cfg.vector_backend == "memory":
        from llmwiki.vector.memory import MemoryVectorStore
        return MemoryVectorStore(dim=cfg.embedding_dim)
    from llmwiki.vector.vectorize import VectorizeStore
    return VectorizeStore(account_id=cfg.cf_account_id,
                          api_token=cfg.cf_api_token.get_secret_value())
```

Rules that go with the pattern:

- **Domain code accepts protocols, never constructs adapters.** `Compiler.__init__` takes
  `(store, vectors, embedder, llm, settings)`. It never calls `factory.*` and never reads
  `os.environ`. That is why the §8.3 tests can hand it a spy.
- **Imports of concrete adapters are function-local** inside `factory.py`, so an offline run never
  imports `boto3` or `anthropic` at all.
- **A real adapter and its fake are tested against the same contract test** — one parametrized test
  module per protocol, run twice, so the fake cannot silently drift from the real behavior.

### 4.4 Import discipline and the public surface

- `src/llmwiki/__init__.py` exports only `__version__` and the `tools` functions. Everything else is
  reached by full path. Submodule `__init__.py` files stay empty — no re-export chains, no import
  side effects.
- Explicit imports only; no `from x import *` (per `~/.claude/CLAUDE.md`).
- No module-level I/O, network calls, or client construction anywhere in `src/`. Importing any
  `llmwiki` module must be free and side-effect-free — this is what keeps the test suite fast and
  what stops a missing env var from breaking `--help`.
- Type hints on every signature; `mypy` with `disallow_untyped_defs` runs in the pre-commit gate
  alongside `ruff` and `pytest -m "not integration"`.
- `py.typed` ships so anything importing `llmwiki` gets the types.

---

## 5. Data Contracts

Settle these in M2 before anything writes to storage — everything downstream depends on them.

### 5.1 Object key layout (`storage/layout.py`)

```
raw/{source_id}/original.{ext}      # immutable bytes exactly as captured
raw/{source_id}/meta.json           # SourceMeta — capture time, url, mime, sha256, title
raw/{source_id}/extracted.md        # normalized text; rewritable if the extractor improves
wiki/index.md                       # hierarchical index, human-readable
wiki/concepts/{slug}.md
wiki/entities/{slug}.md
wiki/sources/{source_id}.md         # one note per source: summary + link back to raw/
wiki/_meta/gists.json               # machine index: slug → {title, gist, type, updated, version, sources[]}
wiki/_meta/cost.jsonl               # append-only per-operation token/cost ledger
```

`source_id = sha256(bytes)[:16]` for files, `sha256(canonical_url)[:16]` for URLs. Content-addressing
gives exact-duplicate detection for free: if `raw/{source_id}/meta.json` exists, the ingest is a no-op.

**`raw/` is append-only.** `original.{ext}` and `meta.json` are never mutated after capture (design
doc §1). Only `extracted.md` may be regenerated, and only by a re-extraction job.

### 5.2 Wiki page format (Obsidian-compatible)

```markdown
---
title: Retrieval-Augmented Generation
slug: retrieval-augmented-generation
type: concept          # concept | entity | index | source
gist: Grounding LLM answers in retrieved documents rather than parametric memory.
sources: [a1b2c3d4e5f60718, 90abcdef12345678]
updated: 2026-08-30
version: 4
---

## Summary
...

## Details
... [[chunking-strategies]] ...

> [!warning] Contradiction
> `[[a1b2c3d4e5f60718]]` claims X; `[[90abcdef12345678]]` claims not-X. Unresolved.

## Sources
- [[sources/a1b2c3d4e5f60718]] — Lewis et al., RAG for Knowledge-Intensive NLP
```

`gist` is the one-line summary that powers progressive disclosure (design doc §4.4).
`version` is an optimistic-concurrency counter: a patch that reads v4 and writes v5 fails if the
stored page is already v5.

### 5.3 Chunk metadata (stored on the vector, for citation without a second fetch)

`{source_id, chunk_index, title, url, section, char_start, char_end, ingested_at}`

Vectorize requires metadata indexes to be declared before filtering on a field — create indexes on
`source_id` at bootstrap (§6.4).

### 5.4 Two vector namespaces

| Index | One vector per | Used by |
|---|---|---|
| `llmwiki-chunks` | content chunk | RAG fallback retrieval |
| `llmwiki-gists` | wiki page (its `gist`) | the compiler, to find which pages a new source affects |

The second index is what makes §8 possible without scanning the wiki.

---

## 6. Operational Runbook

Everything needed to go from an empty machine to a running service, plus the day-to-day commands.

> **Accuracy note:** the exact CLI flags and REST paths below are the ones to **verify in M1**
> (§7, M1) against current Cloudflare documentation and record in `docs/cloudflare-contracts.md`.
> Cloudflare's Vectorize and Workers AI surfaces have moved before. Treat this section as the
> starting script, not as gospel; fix it in place when M1 finds a discrepancy.

### 6.1 Prerequisites

| Need | Install | Check |
|---|---|---|
| Python 3.11+ | `sudo apt install python3.11 python3.11-venv` (system Python is 3.10.12) | `python3.11 --version` |
| Node 18+ (for `wrangler`) | `curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo -E bash - && sudo apt install -y nodejs` | `node --version` |
| Wrangler CLI | `npm install -g wrangler` | `wrangler --version` |
| Cloudflare account | dashboard signup; R2 requires a payment method on file even within the free tier | `wrangler whoami` |
| Anthropic API key | console.anthropic.com | — |

`wrangler login` opens a browser. In WSL2 that may not launch — if it hangs, use
`wrangler login --browser=false` and paste the URL into the Windows browser manually.

Everything `wrangler` does below can also be done from the Cloudflare dashboard, or over the REST
API with `curl`; the CLI is just the fastest path.

### 6.2 Cloudflare R2 (object storage)

```bash
# 1. Create the bucket
wrangler r2 bucket create llmwiki
wrangler r2 bucket list                      # confirm

# 2. Note your account ID — it is the R2 endpoint hostname
wrangler whoami                              # prints the account ID
```

**3. Create S3-compatible credentials** (dashboard only — `wrangler` does not mint these):

> Dashboard → **R2** → **Manage R2 API Tokens** → **Create API token**
> - Permission: **Object Read & Write**
> - Scope: **Apply to specific buckets** → `llmwiki`
> - TTL: no expiry for Phase 0
>
> The **Secret Access Key is shown once**. Copy both values straight into `.env` now.

This yields three values for `.env`:

```bash
R2_ACCESS_KEY_ID=<Access Key ID>
R2_SECRET_ACCESS_KEY=<Secret Access Key>
R2_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

**4. Verify with boto3** — this is the exact client construction `storage/r2.py` uses:

```bash
python - <<'PY'
import boto3, os
from dotenv import load_dotenv; load_dotenv()
s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT_URL"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",                      # R2 requires literally "auto"
)
s3.put_object(Bucket=os.environ["R2_BUCKET"], Key="_healthcheck", Body=b"ok")
print(s3.get_object(Bucket=os.environ["R2_BUCKET"], Key="_healthcheck")["Body"].read())
s3.delete_object(Bucket=os.environ["R2_BUCKET"], Key="_healthcheck")
print("R2 OK")
PY
```

**Known R2 gotchas** — confirm each in M1 and record the resolution:

- `region_name` must be the literal string `"auto"`; a real AWS region gives a signature error.
- Recent `boto3` releases send additional integrity checksum headers that some S3-compatible
  services reject. If uploads fail with a checksum or `501` error, set
  `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` / `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required`,
  or pass the equivalent through `botocore.config.Config`. Pin whatever works into `storage/r2.py`
  with a comment naming the boto3 version it was verified against.
- R2 has no true directories. `layout.py` builds flat keys with `/` separators; never rely on a
  "folder" existing.

**5. Lifecycle policy for cold raw data** (design doc §4.5) — optional in Phase 0, cheap to set now:

```bash
# Move original/ objects to Infrequent Access after 90 days
wrangler r2 bucket lifecycle add llmwiki --prefix "raw/" --storage-class InfrequentAccess --days 90
wrangler r2 bucket lifecycle list llmwiki
```

Never apply a lifecycle rule that **deletes** anything under `raw/` — it is the immutable substrate
the entire wiki is derived from.

### 6.3 Cloudflare API token (Vectorize + Workers AI)

The R2 credentials above are S3 keys and do **not** work for Vectorize or Workers AI, which use a
Cloudflare API token.

**Use an Account API Token, not a User API Token.** A user token is tied to whoever created it and
stops working if that person ever loses access to the account; an account token is owned by the
account itself, acts as a service principal, and is Cloudflare's own recommendation for exactly
this case — a long-lived credential baked into a server's `.env`
([Account API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/)).
Creating one requires **Super Administrator** on the account.

> Dashboard → **Manage Account** → **Account API Tokens** → **Create Token** → **Create Custom Token**
> - Permissions — three separate grants, because the two products name their
>   permission groups inconsistently (Vectorize uses Read/Write, Workers AI uses
>   Read/Edit) and because running a model via the REST API needs *both* of
>   Workers AI's, per Cloudflare's own quickstart ("that token will need
>   permissions for both `Workers AI - Read` and `Workers AI - Edit`" —
>   [source](https://developers.cloudflare.com/workers-ai/get-started/rest-api/)):
>   - `Account` → **Vectorize** → **Write** (covers read+write for Vectorize —
>     query/describe accept either Read or Write, so Vectorize Read alone is
>     not needed in addition; search "Vectorize" if it isn't in the initial list)
>   - `Account` → **Workers AI** → **Read**
>   - `Account` → **Workers AI** → **Edit**
> - Account Resources: your account
> - TTL: no expiry for Phase 0

```bash
CF_API_TOKEN=<token>
CF_ACCOUNT_ID=<account id from `wrangler whoami`>
```

Verify the token — note the endpoint is account-scoped (`/accounts/{account_id}/tokens/verify`),
**not** the `/user/tokens/verify` path documented for user tokens:

```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/tokens/verify" \
  -H "Authorization: Bearer $CF_API_TOKEN" | python -m json.tool
# expect: "status": "active"
```

Then verify, critically, the **embedding dimension**:

```bash
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/ai/run/@cf/baai/bge-base-en-v1.5" \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"text": ["retrieval augmented generation"]}' \
  | python -c "import json,sys; r=json.load(sys.stdin); print('shape:', r['result']['shape'])"
# expect: shape: [1, 768]
```

**The number printed here is `EMBEDDING_DIM`.** If it is not 768, the Vectorize indexes in §6.4 must
be created with that number instead. A dimension mismatch is the single most common Vectorize failure
and it surfaces as an opaque insert error much later.

### 6.4 Cloudflare Vectorize (two indexes)

```bash
# Create both indexes — dimensions MUST match the number printed in §6.3
wrangler vectorize create llmwiki-chunks --dimensions=768 --metric=cosine
wrangler vectorize create llmwiki-gists  --dimensions=768 --metric=cosine

# Metadata indexes must exist BEFORE any vector is inserted, or filtering silently
# will not work on already-inserted vectors
wrangler vectorize create-metadata-index llmwiki-chunks --property-name=source_id --type=string
wrangler vectorize create-metadata-index llmwiki-gists  --property-name=type      --type=string

# Confirm
wrangler vectorize list
wrangler vectorize info llmwiki-chunks
```

REST equivalents used by `vector/vectorize.py` (verify shapes in M1):

| Operation | Method + path (base `https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/v2`) |
|---|---|
| Upsert | `POST /indexes/{name}/upsert` — body is **newline-delimited JSON**, one vector per line, not a JSON array |
| Query | `POST /indexes/{name}/query` — `{vector, topK, filter, returnMetadata}` |
| Delete by id | `POST /indexes/{name}/delete_by_ids` |
| Index info | `GET /indexes/{name}` |

Two behaviors to design around, both verified in M1:

- **Writes are eventually consistent.** A vector is not necessarily queryable the instant upsert
  returns. Integration tests must poll with a timeout rather than asserting immediately, and
  `scripts/smoke_flow.py` (§12.6) does the same.
- **Delete-by-metadata may not exist**; `delete_by_source` may need to resolve ids first. Because
  chunk ids are deterministic (`{source_id}:{chunk_index}`), the ids can always be reconstructed
  without a lookup — design `layout.py` that way.

Idempotent bootstrap for a fresh environment:

```bash
python scripts/bootstrap_indexes.py --create   # creates anything missing
python scripts/bootstrap_indexes.py --check    # asserts both indexes exist, dims match EMBEDDING_DIM
```

`--check` runs at the start of every integration test session and in `/healthz`.

### 6.5 Anthropic

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Before the first real run, set a **monthly spend limit** in the Anthropic console (Settings →
Limits). Design doc §6 makes LLM spend the dominant variable cost; a hard ceiling at the provider is
the only guard that survives a bug in `INGEST_TOKEN_BUDGET`.

### 6.6 Local project bootstrap

```bash
cd ~/projects/llm-wiki-svc

python3.11 -m venv .venv
source .venv/bin/activate
python --version                       # must print 3.11.x or newer

pip install --upgrade pip
pip install -e ".[dev]"                # editable install — see §4.1

cp .env.example .env                   # then fill in the real values from §6.2–§6.5
                                       # (Claude never edits .env — you do)

python scripts/bootstrap_indexes.py --check
pytest                                 # unit tests only; must be green
python scripts/smoke_flow.py --offline # end-to-end with zero network — see §12.6
```

Then run the service:

```bash
uvicorn llmwiki.api.app:app --host 0.0.0.0 --port 8000 --reload
# 0.0.0.0 so the Windows host browser can reach it: http://localhost:8000/docs
```

MCP is mounted in the same process. Point an MCP client at
`http://localhost:8000/mcp` (transport and path confirmed in M7).

### 6.7 Offline / no-cloud mode

Every external service has a fake (§4.3), so M0 and M2–M5 can proceed before the Cloudflare account
exists, and unit tests never need one:

```bash
STORAGE_BACKEND=local
VECTOR_BACKEND=memory
EMBEDDING_BACKEND=fake
LLM_BACKEND=fake          # scripted responses; set to `anthropic` for real compilation
LOCAL_STORAGE_PATH=./.data
```

With those five lines in `.env`, `pytest`, `python scripts/smoke_flow.py --offline`, and
`uvicorn ...` all work with no network and no keys. `.data/` is gitignored and its layout is
identical to the R2 key layout in §5.1, so you can inspect the wiki with Obsidian by opening
`.data/wiki/` as a vault.

### 6.8 Day-to-day operations

| Task | Command |
|---|---|
| Ingest a URL | `curl -X POST localhost:8000/ingest -H "Authorization: Bearer $INGEST_API_TOKEN" -H "Content-Type: application/json" -d '{"url":"https://arxiv.org/abs/2005.11401"}'` |
| Ingest a file | `curl -X POST localhost:8000/upload -H "Authorization: Bearer $INGEST_API_TOKEN" -F "file=@paper.pdf"` |
| Check ingest status | `curl -s localhost:8000/sources/<source_id> \| jq` |
| Same, from the CLI | `llmwiki ingest --url https://...` / `llmwiki status <source_id>` |
| Search | `llmwiki search "retrieval augmented generation"` |
| Read a page | `llmwiki page retrieval-augmented-generation` |
| Force a recompile of one source | `llmwiki compile <source_id>` |
| Global lint (scheduled, never on ingest) | `llmwiki lint --dry-run` then `llmwiki lint --apply` |
| Cost so far | `llmwiki cost --since 7d` |
| Health | `curl -s localhost:8000/healthz \| jq` — reports version, backends, index dims |
| Sync the wiki down for Obsidian | `aws s3 sync s3://llmwiki/wiki ./vault --endpoint-url $R2_ENDPOINT_URL` |

**Scheduled lint** — a cron entry, not part of ingest (design doc §4.4):

```cron
# Weekly global lint/synthesis, Sundays 03:00
0 3 * * 0 cd ~/projects/llm-wiki-svc && .venv/bin/llmwiki lint --apply >> ~/.llmwiki-lint.log 2>&1
```

### 6.9 Cost hygiene and teardown

```bash
# What has this cost?
llmwiki cost --since 30d --by-operation

# Cloudflare usage: dashboard → R2 → Metrics, and Workers AI → Analytics
# Anthropic usage:  console.anthropic.com → Usage

# Tear down a scratch environment (destructive — never point this at the real bucket)
wrangler vectorize delete llmwiki-chunks
wrangler vectorize delete llmwiki-gists
wrangler r2 bucket delete llmwiki-scratch
```

Deleting the Vectorize indexes is recoverable: `raw/` holds every original, so
`scripts/backfill.py` can re-embed everything via the Batch API at 50% cost. Deleting the R2
bucket is **not** recoverable. Keep scratch experiments in a separate bucket
(`llmwiki-scratch`) so the two commands can never be confused.

---

### 6.10 Docker

Added after v1.1 at the operator's request; not part of the original plan. The
container is a packaging and deployment convenience, not an architectural change —
decision D7 still holds, so there is one process and no separate worker or broker.

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build. Deps install from `requirements.txt` before the source is copied, so a code edit does not rebuild the dependency layer. Runtime stage carries only the venv — no compilers in the shipped image. Runs as uid 10001, not root. |
| `docker-compose.yml` | Four services across three profiles: `api` (default), `api-offline`, `smoke`, `lint`. |
| `.dockerignore` | Keeps `.venv/`, `.data/`, `.env` and caches out of the build context. |

```bash
docker compose up --build                          # real backends, reads .env
docker compose --profile offline up api-offline    # no keys, no network, port 8001
docker compose --profile test run --rm smoke       # end-to-end check in the image
docker compose --profile ops run --rm lint         # the scheduled global lint (§6.8)
```

Notes that matter in operation:

- **`.env` is never baked into the image.** Compose reads it at run time via
  `env_file`. An image built here carries no credentials and is safe to push to a
  private registry.
- **With `STORAGE_BACKEND=r2` the container is stateless** and the `llmwiki-data`
  volume stays empty — R2 holds everything. The volume exists for local-storage
  mode, where losing it means losing the wiki.
- **The health check calls `/healthz`**, which makes no network calls by design.
  An unhealthy container therefore means the process is wedged, not that
  Cloudflare is having a bad day — which is the distinction you want at 3am.
- **Weekly lint** replaces the §6.8 host cron entry with:
  `0 4 * * 0 cd /path/to/llm-wiki-svc && docker compose --profile ops run --rm lint`


## 7. Milestones

Sizing is rough and assumes one developer. Each milestone ends with tests green and a `HISTORY.md` entry.

### M0 — Scaffold `[S]`

The system Python is **3.10.12**; `~/.claude/CLAUDE.md` requires 3.11+. Install 3.11+ before creating
`.venv` (§6.1), and confirm with `python --version` inside the venv.

Deliverables: repo layout (§3), `pyproject.toml` + editable install (§4.1), `.venv`,
`requirements.txt`, `.env.example`, `.gitignore`, `HISTORY.md`, `config.py` via `pydantic-settings`,
`factory.py` skeleton, `pytest` green with `test_config.py` and `test_layering.py` (§12.5). Init git;
work on `feat/phase-0-scaffold`.

**Accept:** `pip install -e ".[dev]"` then `pytest` passes; `python -c "import llmwiki; print(llmwiki.__version__)"`
works with only `.env.example` values present.

### M1 — Cloudflare contract spike `[S]`

Execute §6.2–§6.4 against a live account and **correct §6 in place** wherever reality differs. Verify
specifically: the R2 endpoint form and the boto3 checksum behavior; the Vectorize v2 REST paths and
the ndjson upsert body; the Workers AI endpoint shape and the **actual** embedding dimension; whether
metadata filtering requires pre-declared indexes; Vectorize write-visibility lag. Record everything in
`docs/cloudflare-contracts.md`.

**Accept:** the §6.2 boto3 snippet, the §6.3 curl, and a one-vector upsert-then-query round trip all
succeed. `scripts/bootstrap_indexes.py --check` passes. Index dimension equals the embedder's measured output.

*This milestone exists because a wrong dimension or API path is discovered late and expensively.*

### M2 — Storage + capture `[M]`

`ObjectStore` protocol with `r2.py` and `local.py`; `layout.py`; `SourceMeta`/`SourceRef` models;
`POST /ingest` (JSON URL) and `POST /upload` (multipart), authenticated by a static bearer token from
`INGEST_API_TOKEN`; content-hash dedup returning `{"status": "duplicate"}`.

**Accept:** posting the same PDF twice writes `raw/` exactly once. `raw/` objects are never
overwritten by a second ingest of the same source.

### M3 — Extraction `[M]`

`Extractor` protocol + registry dispatching on MIME type / URL pattern. `pdf.py` (pymupdf, page
markers preserved so citations can name a page), `web.py` (httpx + trafilatura), `youtube.py`
(youtube-transcript-api, timestamps preserved as section markers). Output: `ExtractedDoc`
(title, text, sections, source-specific metadata) persisted to `raw/{id}/extracted.md`.

Failures are recorded on the source, not raised to the caller — a bad PDF must not kill the pipeline.

**Accept:** three golden fixtures (small PDF, saved HTML page, canned transcript JSON) extract to
expected text, offline, in unit tests.

### M4 — Chunking, embedding, vector store `[M]`

Heading-aware chunker: split on markdown headings first, then pack to ~3,200 characters with ~400
overlap, avoiding mid-sentence breaks. Use character counts, not a tokenizer — `tiktoken` is not
Claude's tokenizer, and per-chunk `count_tokens` API calls are not worth their cost.

`Embedder` (`workers_ai.py`, `fake.py`) with batching and retry; `VectorStore` (`vectorize.py`,
`memory.py`) with `upsert`, `query`, `delete_by_source`.

**Accept:** ingesting a fixture PDF produces N chunks in the vector store; a query for a phrase in
that PDF returns the containing chunk as hit #1 (real embedder, marked `integration`); the
`memory.py` + `fake.py` pair reproduces the same code path offline in unit tests.

### M5 — Incremental wiki compiler `[L]` ← **the hard part; see §8**

`gists.py` (load/update `wiki/_meta/gists.json`, render `wiki/index.md`), `pages.py` (front-matter
parse/serialize, optimistic version check), `compiler.py` (the §8 algorithm), `lint.py` (global pass,
invoked only by `llmwiki lint`, never by ingest).

**Accept:** the three assertions in §8.3.

### M6 — Query agent `[M]`

`agent/query.py`: read `wiki/index.md` + gists → select candidate pages → read only those → answer.
Fall back to `llmwiki-chunks` vector search when the wiki does not cover the question (no gist above a
similarity floor, or the model reports the pages are insufficient). Every answer carries citations
resolving to `wiki/sources/{id}.md` and through to `raw/`.

**Accept:** a question answerable from the wiki is answered without touching the chunk index (assert
the vector store was not queried); a question about a detail present only in a source body triggers
the fallback and still cites correctly.

### M7 — FastAPI + MCP `[M]`

`tools.py` exposing the canonical six (§10). `api/routes.py` wrapping them plus `/upload`,
`/sources/{id}` and `/healthz`. `mcp/server.py` via FastMCP, mounted into the FastAPI app so one
process serves both (design doc §4.6). Bind `0.0.0.0` for WSL2 (§6.6).

**Accept:** an MCP client connects, lists the six tools, and answers a question using `search_wiki` +
`get_page`. The same operations work over `curl` against the REST routes. `test_tools_parity.py` proves
both transports call the identical function.

### M8 — Measure `[S]`

Ingest a representative corpus (≈50 mixed sources). Record per-ingest and per-query token spend and
wall time from `wiki/_meta/cost.jsonl`; `llmwiki cost` summarizes it. Open the wiki in Obsidian and
confirm `[[wikilinks]]`, front matter, and callouts render. Write `docs/phase0-measurements.md`.

**Accept:** the §14 success criteria are evaluated with real numbers, and Phase 1 priorities are
chosen from that data (design doc §8 step 5).

---

## 8. The Incremental Compiler

Design doc §4.4 is the central cost constraint: **compilation must never scan the full wiki.**
This is the one component where a shortcut silently destroys the project's economics.

### 8.1 Algorithm

For each newly extracted source:

1. **Summarize** — one Haiku call over the extracted text produces a structured summary plus
   candidate concept/entity names. Structured output, not free text.
2. **Locate** — embed each candidate name + the source summary; query `llmwiki-gists` for the top
   `COMPILE_CANDIDATE_PAGES` (default 8) existing pages. **Only gists are loaded here — never page bodies.**
3. **Plan** — one Haiku call over `{source summary, candidate gists, index skeleton}` returns a
   `CompilePlan`: a list of ops, each `create_page` / `patch_page` / `add_backlink` /
   `flag_contradiction`. The plan is capped at `COMPILE_MAX_PAGES` ops (default 5). The planner sees
   gists only, so its input size is bounded by the cap, not by wiki size.
4. **Execute** — for each op, load **only that one page body**, generate the patch (Haiku by default;
   Sonnet 5 when `COMPILE_EXECUTOR_MODEL` says so), write back under an optimistic version check,
   refresh that page's gist, and upsert its gist vector.
5. **Record** — append a `wiki/sources/{id}.md` note, update `gists.json`, re-render `wiki/index.md`
   from `gists.json` (cheap, no LLM), append token/cost to `cost.jsonl`.

Global lint / cross-page synthesis / orphan detection run **only** in `llmwiki lint`, on a schedule
(§6.8). Never on the ingest path.

### 8.2 Cost controls

- Prompt caching on the stable prefix (system prompt + prompt template + tool schemas) of the
  summarize and plan calls. Verify it works by asserting `usage.cache_read_input_tokens > 0` on the
  second call — a silent invalidator (a timestamp, an unsorted dict) makes caching quietly do nothing.
  Put the volatile per-source content **after** the last cache breakpoint.
- Batch API (50% cost) for any backfill or bulk re-compile in `scripts/backfill.py`. Not for
  interactive ingest — batch is asynchronous.
- Hard caps: `COMPILE_MAX_PAGES`, `COMPILE_CANDIDATE_PAGES`, and `INGEST_TOKEN_BUDGET` (abort and
  mark the source `needs_review` rather than run away).
- Every LLM call appends `{op, model, input_tokens, output_tokens, cache_read_tokens, cost_usd, version}`
  to `cost.jsonl`. Cost is measured, not estimated.

### 8.3 Acceptance assertions for M5

1. **No full scan:** with 200 synthetic pages in `gists.json`, compiling one source reads at most
   `COMPILE_MAX_PAGES` page bodies and never lists a wiki prefix. Asserted by a spy store — see
   `test_compiler_no_full_scan.py` in §12.5. This test is the guard on the design's core
   constraint; it must never be weakened.
2. **Bounded planner input:** the planner prompt's size is a function of `COMPILE_CANDIDATE_PAGES`,
   not of wiki size. Asserted by comparing prompt length at 10 pages vs 200 pages.
3. **Idempotence:** re-compiling the same source produces zero ops the second time (already in the
   page's `sources` list).

---

## 9. Ingest Sequence

```
POST /ingest {url | file}
  → auth check
  → source_id = sha256(...)          → duplicate? return early
  → write raw/{id}/original + meta.json
  → enqueue ingest task (in-process)
        → extract           → raw/{id}/extracted.md
        → chunk + embed     → llmwiki-chunks
        → compile (§8)      → wiki/**, llmwiki-gists, cost.jsonl
  → return {source_id, status: "queued"}
GET /sources/{id} → status: queued | extracting | compiling | done | failed
```

`/ingest` returns immediately; compilation is asynchronous. Design doc §7 question 6 (acceptable
compilation latency) is answered pragmatically: seconds-to-a-minute, polled via `/sources/{id}`.

---

## 10. Canonical Tool Surface

The six tools named in design doc §4.6, defined once in `tools.py`, exposed three ways.

| Tool | Signature | Notes |
|---|---|---|
| `search_wiki` | `(query: str, k: int = 5) -> list[SearchHit]` | gists first, chunk fallback |
| `get_page` | `(slug: str) -> WikiPage` | full markdown + front matter |
| `ingest_source` | `(url: str \| None, file: bytes \| None) -> SourceRef` | same path as `POST /ingest` |
| `compile_update` | `(source_id: str) -> CompileResult` | re-run §8 for one source |
| `list_concepts` | `(prefix: str \| None) -> list[PageGist]` | reads `gists.json` only — no LLM, no page bodies |
| `lint_wiki` | `(dry_run: bool = True) -> LintReport` | scheduled/manual only |

Four further functions live in `tools.py` for the CLI, `/healthz` and the smoke script (§12.6), but
are **not** exposed as MCP tools — keeping the agent-facing surface to the six the design doc names:
`get_source_status`, `answer`, `source_exists`, `cost_summary`.

FastAPI adds `/upload`, `/healthz`, `/sources/{id}`. Phase 0 auth is a single static bearer token;
real auth is Phase 1 (design doc §5).

---

## 11. LLM & Embedding Routing

| Operation | Model | Why |
|---|---|---|
| Source summarize | `claude-haiku-4-5` | High volume, structured extraction — Haiku's job |
| Compile plan | `claude-haiku-4-5` | Cheap model plans (design doc §4.4 two-phase) |
| Page create / patch | `claude-haiku-4-5`, overridable to `claude-sonnet-5` | Stronger model executes, only if M8 shows Haiku quality is insufficient |
| Query answering | `claude-haiku-4-5` | Escalate only on measured need |
| Embeddings | Workers AI `@cf/baai/bge-base-en-v1.5` | Not an Anthropic capability |

All Anthropic calls go through the official `anthropic` Python SDK in `llm/anthropic_client.py` —
never raw `requests`/`httpx` to the Messages API. That adapter owns model selection, prompt-caching
breakpoints, retries with typed exception handling (`RateLimitError` and `APIConnectionError` retry;
`BadRequestError` does not), and cost logging.

---

## 12. Testing Plan

Per `~/.claude/CLAUDE.md`, all four required points are addressed explicitly.

### 12.1 Must pass all existing tests

There is currently **no test suite** — this is a greenfield repository. From M0 onward the standing
rule applies: every milestone leaves `pytest` green, and no milestone may break a test written by an
earlier one. `pytest -m "not integration"` (the default via `addopts`) plus `ruff` and `mypy` is the
pre-commit gate.

### 12.2 Obsolete tests announced for removal

**None.** No tests exist to be made obsolete. Should a later Phase 0 milestone invalidate an earlier
milestone's test (most likely candidate: M4 chunker tests if the chunking strategy changes after M8
measurement), that test must be named, its removal justified, and the removal logged in `HISTORY.md`.

### 12.3 New tests, by milestone

| Milestone | Unit (`tests/unit/`, no network) | Integration (`@pytest.mark.integration`) |
|---|---|---|
| M0 | `test_config.py` (settings load; missing required var raises clearly), **`test_layering.py`** (§4.2 import boundaries) | — |
| M2 | `test_layout.py` (key construction; no path traversal from a hostile URL/filename), `test_local_store.py`, `test_store_contract.py` (parametrized over local + R2 fake), `test_ingest_dedup.py`, `test_ingest_auth.py` (401 without token) | `test_r2_roundtrip.py` |
| M3 | `test_extract_pdf.py`, `test_extract_web.py`, `test_extract_youtube.py` (golden fixtures), `test_extract_failure.py` (corrupt input → source marked failed, no raise) | `test_extract_live_url.py` |
| M4 | `test_chunker.py`, `test_memory_vector.py`, `test_vector_contract.py` (parametrized over memory + Vectorize fake), `test_embedder_batching.py` | `test_workers_ai_embed.py` (asserts dimension), `test_vectorize_roundtrip.py` (polls for write visibility) |
| M5 | **`test_compiler_no_full_scan.py`** (§8.3-1), `test_compiler_prompt_bounded.py` (§8.3-2), `test_compiler_idempotent.py` (§8.3-3), `test_page_frontmatter.py`, `test_page_version_conflict.py`, `test_gists_index_render.py`, `test_compile_budget_abort.py` | `test_compile_real_source.py` (asserts cost recorded and `cache_read_input_tokens > 0` on the second call) |
| M6 | `test_agent_wiki_first.py` (asserts vector store NOT queried when the wiki suffices), `test_agent_rag_fallback.py`, `test_citations_resolve.py` (every citation maps to a real `raw/` object) | `test_query_end_to_end.py` |
| M7 | `test_routes.py` (each endpoint, auth, validation errors), `test_mcp_tools.py` (six tools listed with correct schemas), `test_tools_parity.py` (REST and MCP call the identical `tools.py` function) | `test_mcp_client_connect.py` |
| M8 | `test_cost_ledger.py` (jsonl append, `llmwiki cost` aggregation) | full-corpus run (manual, not in CI) |

**Unit tests make no real API calls** — `fake.py` embedder, `memory.py` vector store, `local.py`
store, and a scripted LLM double, all wired in `conftest.py` (§12.5). Integration tests are opt-in
(`pytest -m integration`), require a live `.env`, and are never part of the default run.

Three tests are load-bearing and called out as permanent: `test_layering.py` (guards §4.2, which is
what keeps design doc §4.6's split cheap), `test_compiler_no_full_scan.py` (guards design doc §4.4),
and `test_citations_resolve.py` (guards the "answer + citations" contract).

### 12.4 Documentation of added/removed tests

Each milestone's `HISTORY.md` entry lists tests added or removed under **Test coverage**. At M8,
`CLAUDE.md` gains a "Testing" section summarizing the suite and how to run each tier, and
`docs/testing.md` documents the fixture and doubles strategy.

### 12.5 Worked unit-test examples

These are the shapes to copy. Four files: shared fixtures, an ordinary test, the architecture guard,
and the load-bearing cost guard.

**`tests/conftest.py`** — every fake is wired once; no unit test ever constructs a real adapter.

```python
"""Shared fixtures. Nothing here touches the network."""
import pytest

from llmwiki.config import Settings
from llmwiki.embedding.fake import FakeEmbedder
from llmwiki.storage.local import LocalObjectStore
from llmwiki.vector.memory import MemoryVectorStore
from tests.doubles import ScriptedLLM, SpyObjectStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        storage_backend="local", vector_backend="memory",
        embedding_backend="fake", llm_backend="fake",
        local_storage_path=tmp_path, embedding_dim=768,
        compile_max_pages=5, compile_candidate_pages=8,
    )


@pytest.fixture
def store(settings) -> LocalObjectStore:
    return LocalObjectStore(root=settings.local_storage_path)


@pytest.fixture
def spy_store(store) -> SpyObjectStore:
    """Wraps a real store and records every get/put/list call."""
    return SpyObjectStore(store)


@pytest.fixture
def embedder(settings) -> FakeEmbedder:
    """Deterministic hash-based vectors: same text always embeds identically."""
    return FakeEmbedder(dim=settings.embedding_dim)


@pytest.fixture
def vectors(settings) -> MemoryVectorStore:
    return MemoryVectorStore(dim=settings.embedding_dim)


@pytest.fixture
def llm() -> ScriptedLLM:
    """LLM double. Tests push canned structured responses; it asserts they are consumed."""
    return ScriptedLLM()
```

**`tests/unit/test_chunker.py`** — an ordinary unit test, including the edge cases that bite.

```python
import pytest
from llmwiki.pipeline.ingest import chunk_text


def test_splits_on_headings_before_length(settings):
    text = "# Alpha\n" + "aaa. " * 200 + "\n# Beta\n" + "bbb. " * 200
    chunks = chunk_text(text, size=settings.chunk_size_chars,
                        overlap=settings.chunk_overlap_chars)
    assert {c.section for c in chunks} == {"Alpha", "Beta"}
    assert not any("aaa" in c.text and "bbb" in c.text for c in chunks), \
        "a chunk spans two sections — citations would name the wrong heading"


def test_consecutive_chunks_overlap(settings):
    text = "word " * 4000
    chunks = chunk_text(text, size=1000, overlap=200)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.text[-100:] in nxt.text, "overlap lost — retrieval will miss boundary facts"


def test_char_offsets_round_trip(settings):
    """char_start/char_end must index back into the source, or citations cannot be verified."""
    text = "# H\n" + "The quick brown fox. " * 100
    for c in chunk_text(text, size=500, overlap=50):
        assert text[c.char_start:c.char_end] == c.text


@pytest.mark.parametrize("text", ["", "   ", "x", "#\n", "# Only A Heading\n"])
def test_degenerate_inputs_never_yield_empty_chunks(text, settings):
    assert all(c.text.strip() for c in chunk_text(text, size=500, overlap=50))
```

**`tests/unit/test_layering.py`** — enforces §4.2 mechanically.

```python
"""Enforces the layer dependency rule in §4.2.

A failure here is an architecture bug, not a style nit: the inward-only rule is
what keeps the FastAPI/MCP split of design doc §4.6 cheap to maintain, and what
lets every unit test run offline.
"""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "llmwiki"

# layer -> layers it must never import
FORBIDDEN = {
    "models":     {"storage", "extractors", "embedding", "vector", "llm",
                   "wiki", "agent", "pipeline", "api", "mcp"},
    "storage":    {"extractors", "embedding", "vector", "llm", "wiki", "agent", "pipeline", "api", "mcp"},
    "extractors": {"storage", "embedding", "vector", "wiki", "agent", "pipeline", "api", "mcp"},
    "embedding":  {"storage", "extractors", "vector", "llm", "wiki", "agent", "pipeline", "api", "mcp"},
    "vector":     {"storage", "extractors", "embedding", "llm", "wiki", "agent", "pipeline", "api", "mcp"},
    "llm":        {"storage", "extractors", "embedding", "vector", "wiki", "agent", "pipeline", "api", "mcp"},
    "wiki":       {"api", "mcp", "pipeline", "agent"},
    "agent":      {"api", "mcp", "pipeline"},
    "pipeline":   {"api", "mcp"},
}


def _imported_layers(path: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("llmwiki."):
            found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[1] for a in node.names
                         if a.name.startswith("llmwiki."))
    return found


@pytest.mark.parametrize("layer,banned", sorted(FORBIDDEN.items()))
def test_layer_does_not_import_upward(layer, banned):
    for path in (SRC / layer).rglob("*.py"):
        violations = banned & _imported_layers(path)
        assert not violations, (
            f"{path.relative_to(SRC)} imports {sorted(violations)} — "
            f"'{layer}' may not depend on those layers (see plan §4.2)"
        )


def test_transport_layer_only_calls_tools():
    """api/, mcp/ and cli.py go through tools.py — never straight into the domain."""
    allowed = {"tools", "models", "config"}
    paths = [*(SRC / "api").rglob("*.py"), *(SRC / "mcp").rglob("*.py"), SRC / "cli.py"]
    for path in paths:
        leaked = _imported_layers(path) - allowed
        assert not leaked, f"{path.relative_to(SRC)} reaches past tools.py into {sorted(leaked)}"
```

**`tests/unit/test_compiler_no_full_scan.py`** — the assertion the project's economics rest on.

```python
"""Guards design doc §4.4 and plan §8.3-1: compilation must never scan the full wiki.

If this test fails, do NOT relax the bound. The whole cost model depends on
per-ingest work being independent of corpus size. A failure means the compiler
started reading pages it was not planned to touch.
"""
import pytest

from llmwiki.models.plan import CompileOp, CompilePlan
from llmwiki.wiki.compiler import Compiler
from tests.factories import make_extracted_doc, seed_gists


@pytest.mark.parametrize("wiki_size", [10, 200])
def test_page_body_reads_are_capped_regardless_of_wiki_size(
    wiki_size, spy_store, vectors, embedder, llm, settings
):
    seed_gists(spy_store, vectors, embedder, count=wiki_size)
    llm.push_summary(concepts=["retrieval-augmented generation", "chunking"])
    llm.push_plan(CompilePlan(ops=[
        CompileOp(kind="patch_page", slug=f"concept-{i}") for i in range(3)
    ]))
    llm.push_patches(3)
    spy_store.reset_counts()

    Compiler(spy_store, vectors, embedder, llm, settings).compile(
        make_extracted_doc(source_id="deadbeefdeadbeef")
    )

    body_reads = spy_store.gets(prefix="wiki/concepts/")
    assert len(body_reads) <= settings.compile_max_pages, (
        f"read {len(body_reads)} page bodies against a cap of "
        f"{settings.compile_max_pages} (wiki size {wiki_size})"
    )
    assert not spy_store.lists(prefix="wiki/"), (
        "compiler enumerated a wiki prefix — that is a full scan by another name"
    )


def test_page_reads_do_not_grow_with_wiki_size(spy_store, vectors, embedder, llm, settings):
    """The sharper form: identical work at 10 pages and at 200."""
    counts = []
    for size in (10, 200):
        seed_gists(spy_store, vectors, embedder, count=size, reset=True)
        llm.push_summary(concepts=["retrieval-augmented generation"])
        llm.push_plan(CompilePlan(ops=[CompileOp(kind="patch_page", slug="concept-0")]))
        llm.push_patches(1)
        spy_store.reset_counts()
        Compiler(spy_store, vectors, embedder, llm, settings).compile(
            make_extracted_doc(source_id="cafebabecafebabe")
        )
        counts.append(len(spy_store.gets(prefix="wiki/concepts/")))
    assert counts[0] == counts[1], f"page reads scaled with corpus size: {counts}"
```

### 12.6 End-to-end smoke flow script

`pytest` proves the units. This proves the **slice** — the thing design doc §8 step 3 actually asks
for. It runs the real path from capture to cited answer, prints one line per stage, and exits
non-zero at the first stage that breaks.

Run it three ways:

```bash
python scripts/smoke_flow.py --offline                    # zero network; run this in the pre-commit gate
python scripts/smoke_flow.py                              # real R2 + Vectorize + Anthropic, fixture PDF
python scripts/smoke_flow.py --url https://arxiv.org/abs/2005.11401
```

```python
#!/usr/bin/env python
"""End-to-end smoke check for the Phase 0 vertical slice.

Capture -> extract -> embed -> compile -> search -> cited answer, against whichever
backends the environment selects. One line per stage; exit code 0 means the slice
works end to end, non-zero means the last stage printed is where it broke.

    python scripts/smoke_flow.py --offline
    python scripts/smoke_flow.py --url https://arxiv.org/abs/2005.11401
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample.pdf"
TIMEOUT_S = 180


def step(n: int, label: str) -> None:
    print(f"\n[{n}] {label}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="ingest this URL instead of the fixture PDF")
    ap.add_argument("--offline", action="store_true",
                    help="force local/memory/fake backends — no network, no keys")
    ap.add_argument("--question", default="What is retrieval-augmented generation?")
    ap.add_argument("--keep", action="store_true", help="do not clean up the ingested source")
    args = ap.parse_args()

    if args.offline:
        os.environ.update(STORAGE_BACKEND="local", VECTOR_BACKEND="memory",
                          EMBEDDING_BACKEND="fake", LLM_BACKEND="fake")

    # Imported after the env is set so factory.py sees the right backends.
    from llmwiki import __version__, tools
    from llmwiki.config import settings

    print(f"llmwiki {__version__} | storage={settings.storage_backend} "
          f"vector={settings.vector_backend} embed={settings.embedding_backend} "
          f"llm={settings.llm_backend}")

    step(1, "ingest")
    ref = (tools.ingest_source(url=args.url) if args.url
           else tools.ingest_source(file=FIXTURE.read_bytes(), filename=FIXTURE.name))
    print(f"    source_id={ref.source_id} status={ref.status}")

    step(2, f"wait for the pipeline (timeout {TIMEOUT_S}s)")
    deadline = time.monotonic() + TIMEOUT_S
    status = tools.get_source_status(ref.source_id)
    while status.state not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(2)
        status = tools.get_source_status(ref.source_id)
        print(f"    {status.state}...", flush=True)
    if status.state != "done":
        print(f"    FAILED state={status.state} error={status.error}")
        return 1
    print(f"    chunks={status.chunk_count} pages_touched={status.pages_touched} "
          f"elapsed={status.elapsed_s:.1f}s")

    step(3, "wiki pages are well formed")
    concepts = tools.list_concepts()
    assert concepts, "the compiler produced no pages"
    page = tools.get_page(concepts[0].slug)
    assert page.front_matter.gist, "page has no gist — the hierarchical index would be useless"
    assert ref.source_id in page.front_matter.sources, "page does not record its source"
    print(f"    {len(concepts)} pages; sampled '{page.front_matter.slug}' "
          f"v{page.front_matter.version}")

    step(4, "search_wiki")
    hits = tools.search_wiki(args.question, k=3)
    assert hits, "search returned nothing"
    for h in hits:
        print(f"    {h.score:.3f}  {h.slug or h.source_id}  (via {h.origin})")

    step(5, "answer with citations")
    answer = tools.answer(args.question)
    assert answer.citations, "answer carries no citations — the core contract is broken"
    for c in answer.citations:
        assert tools.source_exists(c.source_id), f"dangling citation: {c.source_id}"
    print(f"    {answer.text[:200].strip()}...")
    print(f"    citations: {[c.source_id for c in answer.citations]}")

    step(6, "cost ledger")
    cost = tools.cost_summary(since=ref.created_at)
    print(f"    ${cost.total_usd:.4f} over {cost.call_count} LLM calls; "
          f"cache reads {cost.cache_read_tokens} tok")
    if not args.offline and cost.total_usd == 0:
        print("    WARNING: real backends but zero recorded cost — the ledger is not wired up")

    if not args.keep:
        tools.delete_source(ref.source_id)
        print("\n    cleaned up (pass --keep to retain)")

    print("\nSMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Notes on how it earns its place:

- **It is the milestone acceptance harness.** From M2 onward each milestone extends it by one step,
  so "M4 is done" means `smoke_flow.py` reaches step 4. The script is written once at M2 with later
  steps guarded by `hasattr`/try-skip, then filled in.
- **`--offline` runs in the pre-commit gate** alongside `pytest`. It catches wiring bugs — a factory
  returning the wrong adapter, a route not calling `tools.py` — that unit tests structurally cannot.
- **It is deliberately not a pytest test.** It is slow, ordered, stateful, and prints a narrative;
  forcing it into pytest would either break the "unit tests make no API calls" rule or hide the trace.
- The `--offline` run must stay under ~10 seconds. If it drifts slower, something is doing real I/O
  that should have been faked.

---

## 13. Environment Variables

`.env.example` is created in M0 with every key below and placeholder values, and is updated in the
**same change** as any code that adds or removes a key.

```bash
# --- Anthropic ---
ANTHROPIC_API_KEY=sk-ant-...
LLM_DEFAULT_MODEL=claude-haiku-4-5
COMPILE_EXECUTOR_MODEL=claude-haiku-4-5     # set to claude-sonnet-5 to escalate patch generation

# --- Cloudflare (R2 + Vectorize + Workers AI) — see §6.2–§6.4 ---
CF_ACCOUNT_ID=changeme
CF_API_TOKEN=changeme                        # Vectorize Write + Workers AI Read + Workers AI Edit (§6.3)
R2_ACCESS_KEY_ID=changeme                    # S3-compatible creds, separate from CF_API_TOKEN (§6.2)
R2_SECRET_ACCESS_KEY=changeme
R2_BUCKET=llmwiki
R2_ENDPOINT_URL=https://changeme.r2.cloudflarestorage.com
VECTORIZE_CHUNKS_INDEX=llmwiki-chunks
VECTORIZE_GISTS_INDEX=llmwiki-gists
EMBEDDING_MODEL=@cf/baai/bge-base-en-v1.5
EMBEDDING_DIM=768                            # MUST equal the shape printed by the §6.3 curl

# --- Service ---
INGEST_API_TOKEN=changeme                    # static bearer token for POST /ingest (Phase 0 only)
API_HOST=0.0.0.0                             # 0.0.0.0 so the Windows host can reach WSL2
API_PORT=8000

# --- Cost guardrails (§8.2) ---
COMPILE_MAX_PAGES=5
COMPILE_CANDIDATE_PAGES=8
INGEST_TOKEN_BUDGET=60000
CHUNK_SIZE_CHARS=3200
CHUNK_OVERLAP_CHARS=400

# --- Backend selection (§4.3, §6.7) — swap to the fakes for offline dev and tests ---
STORAGE_BACKEND=r2                           # r2 | local
VECTOR_BACKEND=vectorize                     # vectorize | memory
EMBEDDING_BACKEND=workers_ai                 # workers_ai | fake
LLM_BACKEND=anthropic                        # anthropic | fake
LOCAL_STORAGE_PATH=./.data
```

`config.py` is the only module that reads the environment, and it types secrets as
`pydantic.SecretStr` so a stray log line cannot leak a key. `.env` is never committed and never
edited by Claude — new variables are proposed, and you add them. `.env.example` is always committed.

---

## 14. Success Criteria (design doc §5, Phase 0)

Evaluated at M8 with measured numbers, not impressions.

| Criterion | Measurable target |
|---|---|
| Daily capture works | 50 mixed sources ingested with zero manual repair; failures visibly marked, not silent |
| Wiki compounds usefully | The 50th source updates existing pages more often than it creates new ones; `wiki/index.md` is navigable by hand |
| Cost stays very low | Median per-ingest LLM cost recorded from `cost.jsonl`; per-ingest cost does **not** grow with corpus size (compare sources 1–10 against 41–50 — flat is the pass condition) |
| Agents can connect via MCP | A stock MCP client connects and completes a cited answer using only the six tools |
| Citations are trustworthy | 100% of citations in a 20-answer sample resolve to a real `raw/` object containing the claim |

**Phase 1 gate:** do not start Phase 1 work until M8 is written up and its data has been used to
choose Phase 1 priorities (design doc §8 step 5).

---

## 15. Open Items

Answer before or during the milestone named. None block starting M0.

1. **Cloudflare account** — does one exist with R2 and Vectorize enabled, and who creates the API
   tokens (§6.2, §6.3)? Blocks M1. Until then, the offline mode in §6.7 keeps M0 and M2–M5 moving.
2. **Test corpus** — which ~50 sources are representative of the real research domain? Needed by M8;
   ideally chosen at M3 so extraction is tuned against real inputs.
3. **Wiki taxonomy** — is `concept` / `entity` sufficient, or does the domain need more page types?
   Cheap to decide at M5, expensive to change after content accumulates.
4. **Compilation latency tolerance** (design doc §7 q6) — the plan assumes async-with-polling is
   acceptable. If synchronous sub-5-second compilation is required, §8 needs rethinking.
5. **Obsidian vault sync** — is the R2 `wiki/` prefix synced locally for Obsidian (the `aws s3 sync`
   in §6.8), or is a read-through markdown viewer in FastAPI sufficient for Phase 0? Affects M7 scope.

---

## 16. Documentation Obligations

Non-negotiable, per `CLAUDE.md`:

- **`HISTORY.md`** — created in M0; every milestone (and every fix within one) logged with goal, root
  cause for bugs, implementation detail, related files, and test coverage. A milestone is not done
  until its entry exists.
- **`CLAUDE.md`** — updated at M0 (repository is no longer pre-implementation), at M7 (tool surface
  and how to run the service), and at M8 (testing section per §12.4).
- **`llmwiki-KB-design.md`** — bump version and date when M8's measurements lock in the Phase 0 stack
  decisions, recording the §2 decisions as resolved answers to design doc §7 questions 2, 3 and 7.
- **`.env.example`** — updated in the same change as any env var addition, rename, or removal.
- **`README.md`** — a short quickstart pointing at §6.6; the runbook itself stays here.
- **`docs/`** — `cloudflare-contracts.md` (M1, corrections to §6), `testing.md` (M8),
  `phase0-measurements.md` (M8).
