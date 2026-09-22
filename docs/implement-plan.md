# LLM Wiki KB — Implementation Plan

**Version:** 1.5
**Date:** 2026-09-21
**Merges:** the former `implement-plan.md` v1.1 (2026-08-30, Phase 0) → **Part I**, and the former
`implement-plan-v1.4.md` v1.3 (2026-09-21, Phase 0.5 packaging + Phase 1 behaviour changes) →
**Part II**. Both bodies are carried over verbatim; each Part keeps its own section numbering so
that every existing reference in code, tests, `HISTORY.md` and the other docs still resolves.
**This is the only implementation plan.** `implement-plan-v1.4.md` is deleted; this file keeps the
`implement-plan.md` name and carries the version number in this header, not in the filename.
**Design references:** Part I was derived from `llmwiki-KB-design.md` v1.2; Part II from
`llmwiki-KB-design_v1.4.md` (now at 1.6). `llmwiki-KB-design_v1.4.md` is the authoritative design.

> **Reference convention.** "Part I §X" / "plan I §X" is a section of Part I (the old
> `implement-plan.md` v1.1, a.k.a. "plan-1.1 §X"). "Part II §X" / "plan II §X" is a section of
> Part II (the old `implement-plan-v1.4.md`, a.k.a. "plan-v1.4 §X"). Inside a Part, a bare "§X" refers to
> that same Part — the two bodies were written that way and have not been rewritten. Part I's
> "design doc §X" points at `llmwiki-KB-design.md` v1.2; Part II's "design v1.4 §X" points at
> `llmwiki-KB-design_v1.4.md`. `HISTORY.md` entries older than 2026-09-21 that cite
> `implement-plan.md §X` mean Part I §X, and `implement-plan-v1.4.md §X` means Part II §X.

## How the two Parts relate

Part I is the record of Phase 0 as executed (M0–M8, all done). Part II was written as a
*restructuring* plan on top of it and later grew the Phase 1 behaviour changes. Where the same
topic appears in both, the later Part wins; a **Superseded** callout at the top of the affected
Part I sections says where to look. Status as of this version:

| Topic | Part I | Part II | Current state |
|---|---|---|---|
| Scope, locked decisions D1–D7 | §1, §2 | §1, §4 (P1–P10) | Both hold; D5's model choice is now per-op (Part II §19) |
| Repository layout | §3 | §5 | **Part I §3 is what exists.** N0–N8 (Part II §12) have not executed: there is no `packages/` tree, `pip install llmwiki` is still all-or-nothing |
| Package rules, layering guard | §4 | §3, §8–§10 | Part I §4's L0–L5 ladder is enforced by `tests/unit/test_layering.py`; Part II §10's extraction/sibling guards are not yet written |
| Data contracts, wiki page format | §5 | — | Current |
| Operational runbook (R2, Vectorize, Workers AI, day-to-day) | §6 | §21 (Obsidian view of the R2 wiki) | Current; `docs/cloudflare-vectorize-setup-plan.md` and `docs/deployment-plan-container-hosting.md` extend it |
| Incremental compiler, ingest sequence, tool surface | §8, §9, §10 | — | Current; the five-source-kind ingest surface is technical document §3.1.1 |
| LLM routing | §11 | §7, §19 | **Part II §19 is current** — multi-provider, per-op (`config/ops.py`), SKILL.md prompts |
| Query agent | §10 | §20 | **Part II §20 is current** — bounded LangGraph loop, `search_web`, LangSmith eval |
| Testing plan | §12 | §13, §19.7, §20.9 | Part I §12's rules still hold (500 unit tests, no network); Part II adds the packaging suites (not yet) and the §19/§20 tests (landed) |
| Environment variables | §13 | §14, §19.8 | `.env.example` is authoritative; Part II §14/§19.8 describe the provider-generic contract |
| Open items | §15 | §17 | Part I's are resolved; Part II §17's naming questions remain open with N0–N8 |

**Combined contents**

| Part I — Phase 0 (plan 1.1) | Part II — Phase 0.5 & Phase 1 (plan v1.4 → 1.3) |
|---|---|
| 1 What Phase 0 Is | 1 What This Plan Is (and Is Not) |
| 2 Locked Decisions | 2 What v1.4 Changed, and What It Obliges |
| 3 Target Repository Layout | 3 The Four Units |
| 4 Package Architecture & Code Handling | 4 Locked Decisions |
| 5 Data Contracts | 5 Target Repository Layout |
| 6 Operational Runbook | 6 Unit 1 — `agentkit-storage` |
| 7 Milestones M0–M8 | 7 Unit 2 — `agentkit-llm` |
| 8 The Incremental Compiler | 8 Unit 3 — `llmwiki` core |
| 9 Ingest Sequence | 9 Unit 4 — The Service |
| 10 Canonical Tool Surface | 10 Enforcement: The Boundary Guards |
| 11 LLM & Embedding Routing | 11 Cross-Repo Consumption |
| 12 Testing Plan | 12 Milestones N0–N8 |
| 13 Environment Variables | 13 Testing Plan |
| 14 Success Criteria | 14 Environment Variables & Config Injection |
| 15 Open Items | 15 Repo Split |
| 16 Documentation Obligations | 16 Risks · 17 Open Items · 18 Documentation Obligations |
| | 19 Multi-Provider Op Routing & Query-Agent Skill Invocation |
| | 20 Phase 1-D: LangGraph Query Graph + LangSmith Eval |
| | 21 Viewing the R2 Wiki in Obsidian |

---

# Part I — Phase 0 Implementation Plan

*Originally `implement-plan.md` — version 1.1, 2026-08-30. Derived from `llmwiki-KB-design.md`
v1.2 (§4.6 hybrid interface, §4.4 compilation strategy, §5 Phase 0). Status: executed (M0–M8).
Changes 1.0 → 1.1: added §4 (package architecture and code handling), §6 (operational runbook)
and §12.5–§12.6 (worked unit-test examples and the smoke-flow script).*

> Within this Part, "§X" means Part I §X and "design doc §X" means `llmwiki-KB-design.md` v1.2.

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

> **Superseded.** Part II §5 is the current target layout (three distributions under `packages/`); the tree below is the Phase 0 layout as built and is still what `src/llmwiki/` looks like today, because the N0–N8 packaging milestones have not executed (see the status table at the top of this document).

Follows the layout in `~/.claude/CLAUDE.md`.

```
llm-wiki-svc/
├── CLAUDE.md
├── HISTORY.md                     # created in M0 — mandatory, every change logged
├── README.md
├── implement-plan.md              # this plan (now docs/implement-plan.md, Part I)
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

> **Superseded.** The single-distribution rules here still hold for `src/llmwiki/`; Part II §3–§10 define the three-distribution architecture that replaces them once N2–N4 land, and Part II §7 replaces §4's Anthropic-only LLM client with the provider-generic `LLMClient`.

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
raw/{source_id}/original.{ext}      # immutable bytes exactly as captured; source_id is
                                    # {hash16}-{title-slug} since 2026-09-13 (bare hash before)
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

> **Superseded.** The Anthropic-only table below is the Phase 0 routing. Since 2026-09-05 routing is per-op and multi-provider — `config/providers.py` + `config/ops.py`, Part II §19 (design v1.4 §4.8) — and the adapter set is Part II §7.4–§7.5. The embedding row is unchanged.

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

> **Superseded.** `.env.example` is the authoritative list; the block below is the Phase 0 set as of 2026-08-30. The provider-generic LLM variables are Part II §14 and §19.8; capture-channel and YouTube variables were added by Phase 1 work (`HISTORY.md` 2026-09-11, 2026-09-20).

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

> **Superseded.** Item 5 (Obsidian vault sync) is answered by Part II §21. Items 1–4 were resolved during M1–M8; see `HISTORY.md`.

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

---

# Part II — Phase 0.5 Implementation Plan: Layer Separation, Shareable Packages & Phase 1 Behaviour

*Originally `implement-plan-v1.4.md` — version 1.3, 2026-09-21. Derived from
`llmwiki-KB-design_v1.4.md` (§2 layered architecture, §4.6 Core Wiki Package, §4.7 Shareable LLM
Integration Layer, §4.8 Application-Specific LLM Routing). §1–§18 (v1.0) change only packaging,
with no behaviour change, and have not yet executed; §19 (v1.1), §20 (v1.2) and §21 (v1.3) are
deliberate, scoped additions that have.*

> Within this Part, "§X" means Part II §X, "plan-1.1 §X" means Part I §X, and "design v1.4 §X"
> means `llmwiki-KB-design_v1.4.md`.

**Contents**

| § | Section | § | Section |
|---|---------|---|---------|
| 1 | What This Plan Is (and Is Not) | 11 | Cross-Repo Consumption: FUND-financial-Research |
| 2 | What v1.4 Changed, and What It Obliges | 12 | Milestones N0–N8 |
| 3 | The Four Units | 13 | **Testing Plan** |
| 4 | Locked Decisions | 14 | Environment Variables & Config Injection |
| 5 | Target Repository Layout | 15 | Repo Split: Graduation Criteria & Mechanics |
| 6 | Unit 1 — `agentkit-storage` | 16 | Risks |
| 7 | Unit 2 — `agentkit-llm` (design v1.4 §4.7) | 17 | Open Items |
| 8 | Unit 3 — `llmwiki` core (design v1.4 §4.6) | 18 | Documentation Obligations |
| 9 | Unit 4 — The Service (api / mcp / cli) | **19** | **Multi-Provider Op Routing & Query-Agent Skill Invocation (new, v1.1)** |
| 10 | Enforcement: The Boundary Guards | **20** | **Phase 1-D: LangGraph Query Graph + LangSmith Eval (new, v1.2)** |
| | | **21** | **Viewing the R2 Wiki in Obsidian (new, v1.3)** |

---

## 1. What This Plan Is (and Is Not)

Design v1.4 changed exactly one thing relative to v1.3: **the architecture diagram now separates
shareable packages from platform-specific services.** No capability was added, no algorithm changed,
no phase boundary moved. §4.4's cost constraint, §4.6's hybrid interface recommendation and §4.7's
LLM-layer design are all carried forward verbatim from v1.3.

That makes this a **restructuring plan**, and it inherits the restructuring plan's one hard rule:

> **No behaviour change.** Every milestone in §12 ends with the same 189 unit tests green, the same
> 6 integration tests collected, and `scripts/smoke_flow.py --offline` passing. A milestone that
> needs to change an assertion to go green is a milestone that has changed behaviour — stop and
> re-scope it.

**Why this is worth doing now, before Phase 1.** The Phase 0 code already satisfies v1.4's diagram
in spirit — `test_layering.py` has enforced an L0–L5 ladder since M0. What it does *not* yet satisfy
is v1.4's claim that two of those layers are **independently installable by other repositories**.
Today `pip install llmwiki` is all-or-nothing: it pulls FastAPI, fastmcp, uvicorn, boto3, pymupdf,
trafilatura and youtube-transcript-api, and there is no way for another repository to depend on the
storage adapters or the LLM client without depending on the wiki compiler. Design v1.4's central
claim — "Other platforms/repos can depend on either or both without pulling the FastAPI/MCP service"
— is currently false. This plan makes it true.

**Not in scope.** Phase 1 features (Telegram, local LLM inference, auth, workspaces), Phase 2+
features, and any change to the compiler algorithm. If restructuring appears to require one, stop
and confirm the phase, exactly as plan-1.1 §1 requires.

### 1.1 Verified starting state

Measured on 2026-09-01 in `/home/thomas/projects/llm-wiki-svc`, not assumed:

| Fact | Value | Consequence for this plan |
|---|---|---|
| Unit tests | 189 passing, 6 integration deselected, 1.6 s | The regression net is real and fast; use it after every step |
| `src/llmwiki/storage/**` imports from `llmwiki` | **only** `llmwiki.storage.base` | Storage extraction is a directory move. No untangling required |
| `src/llmwiki/llm/**` imports from `llmwiki` | `llmwiki.llm.base` + `llmwiki.models.plan.CostRecord` | LLM extraction is a directory move **plus one symbol**, `CostRecord` |
| Adapters read `os.environ` | never — `config.py` is the only reader; adapters take constructor args | Config injection (design v1.4 §4.7 item 6) already holds |
| Module-level I/O | none; guarded by `test_no_module_level_io` | Packages are import-safe out of the box |
| Repository is under git | **no** — `git rev-parse` fails | **Blocking.** A multi-directory refactor without version control is not acceptable. N0 fixes this |
| Bare `pytest` | ~~fails (`ModuleNotFoundError: tests.doubles`)~~ — **fixed 2026-09-01** | `pythonpath = ["."]` added to `[tool.pytest.ini_options]`; half of N0 is done |
| LLM layer has a dedicated unit test file | **no** — exercised only indirectly via `tests/doubles.py` | Extracting it to its own distribution requires *new* contract tests (§13.3) |

The first two rows are the important ones. **The extraction seams design v1.4 asks for already exist
in the code**; what is missing is the packaging metadata and the guard that stops them closing again.
This plan is therefore much smaller than the diagram redraw suggests.

---

## 2. What v1.4 Changed, and What It Obliges

| v1.4 diagram element | Obligation it creates | State today | Where handled |
|---|---|---|---|
| `Core Wiki Package` drawn as a box distinct from Interface | `llmwiki` must be installable **without** FastAPI / fastmcp / uvicorn | False — all three are hard dependencies | §8, N3 |
| `Shareable LLM Integration Layer` drawn as a peer box, `OtherRepos --> LLMlayer` | LLM layer must be a separate distribution with no wiki imports | False — one distribution, and `llm/` imports `models.plan` | §7, N4 |
| `Storage Layer` drawn below both packages | Storage adapters must be reachable without the compiler | False — same distribution | §6, N2 |
| `External Consumers → OtherRepos` edge | At least one real external consumer must be proven, not asserted | Unproven | §11, N5 |
| `AgentOrch` box naming LangGraph + LangSmith | A LangChain-native surface must exist for LangGraph consumers | Partly closed 2026-09-01: LangChain now backs five providers *behind* `complete()`; the public `get_llm()` and LangSmith are still absent | §7.5, N7 |
| Layer Responsibilities table marks Storage "Infra", not shareable | — | — | **Deviation, see §2.1** |

### 2.1 Deviation from design v1.4: Storage is a shareable layer

Design v1.4's Layer Responsibilities table classifies Storage as **"Infra"** and marks only the Core
Wiki Package and the LLM Layer as shareable. This plan **treats storage as a third shareable unit**,
on the instruction that it is wanted by the Storage Agent in `FUND-financial-Research`.

The evidence supports the deviation rather than the table:

- `src/llmwiki/storage/` imports nothing from `llmwiki` except its own `base.py`. It is the *least*
  coupled thing in the repository — cheaper to share than either package v1.4 does mark shareable.
- A concrete consumer exists today. `FUND-financial-Research` contains `utils/storage.py` (573 lines)
  and `fund_models/storage.py` (173 lines) — **two parallel implementations of the same abstraction**,
  both with a `local | r2` backend switch, both with the same `R2_*` environment variable names as
  this repository. That is duplicated infrastructure across two repos and, within FUND, across two
  modules.
- What v1.4's table means by "Infra" is the *buckets and indexes* — the provisioned R2 bucket and
  Vectorize indexes. That genuinely is infra and is not shareable. The **client code that talks to
  them** is ordinary library code.

**Action:** design v1.4's §2 Layer Responsibilities table gains a Storage row marked
"**Yes — versioned package (adapters only; buckets/indexes remain infra)**" when this plan's N2
lands. Logged in `HISTORY.md`, per §18.

---

## 3. The Four Units

```
                    ┌──────────────────────────────────────────────┐
   OTHER REPOS ────►│  agentkit-storage      (shareable, no deps   │
   (FUND, …)        │  ObjectStore protocol   beyond boto3-extra)  │
                    │  Local · R2 · Async façade                   │
                    └──────────────────────────────────────────────┘
                                      ▲ used by
                    ┌──────────────────────────────────────────────┐
   OTHER REPOS ────►│  agentkit-llm          (shareable, §4.7)     │
   (FUND, …)        │  LLMConfig · get_llm() · complete() ·        │
                    │  cost · callbacks · prompts · skills ·       │
                    │  memory/context · AgentBase · eval           │
                    └──────────────────────────────────────────────┘
                                      ▲ used by
                    ┌──────────────────────────────────────────────┐
   OTHER REPOS ────►│  llmwiki               (shareable, §4.6)     │
   (later)          │  models · layout · extractors · vector ·     │
                    │  wiki pages · gists · compiler · lint ·      │
                    │  agent · pipeline · tools                    │
                    └──────────────────────────────────────────────┘
                                      ▲ used by
                    ┌──────────────────────────────────────────────┐
                    │  llmwiki[service]      (platform-specific)   │
                    │  api/ · mcp/ · cli.py  — thin glue, no logic │
                    └──────────────────────────────────────────────┘
```

Dependencies point downward in that picture and nowhere else. `agentkit-storage` and `agentkit-llm`
are siblings and **must never import each other** — an LLM package that needs an object store is an
LLM package that has grown a wiki-shaped opinion.

**One-line summary of each unit's job:**

| Unit | Owns | Explicitly does not own |
|---|---|---|
| `agentkit-storage` | Bytes in and out of a flat keyspace | What the keys mean; tables; databases |
| `agentkit-llm` | Talking to models, and what one call cost | Wiki prompts; retrieval; the compiler |
| `llmwiki` | Raw sources, extraction, vectors, pages, the incremental compiler | Transport, auth, serialization |
| `llmwiki[service]` | HTTP, MCP, CLI, auth, serialization | Every decision above |

---

## 4. Locked Decisions

Numbered `P` for *packaging*, to keep them distinct from plan-1.1's `D` decisions, all of which
remain in force.

| # | Decision | Rationale | Reversal cost |
|---|---|---|---|
| **P1** | **Monorepo of distributions now; separate repos on demand.** Shareable units become independently-buildable distributions under `packages/` in *this* repository, each with its own `pyproject.toml`, tests, README and version — but not their own git repository yet | Design v1.4 §4.7 item 5 names exactly this as the acceptable path ("path dependency during early development, then graduate"). A three-repo split today costs three CI setups, three release processes and a cross-repo lockstep problem, to serve zero shipped consumers. The monorepo gets 100% of the *decoupling* benefit and defers 100% of the *distribution* cost | **Low by construction** — that is the point. §15 defines the graduation criteria and the mechanical split procedure |
| **P2** | **Namespace `agentkit`; distributions `agentkit-storage` and `agentkit-llm`** (imports: `agentkit.storage`, `agentkit.llm`) | Design v1.4 §4.7 leaves the name open and offers `llmwiki-llm` / `fund-llm-core` / `agent-llm-kit`. `llmwiki-llm` is wrong the moment FUND imports it; `fund-llm-core` is wrong in this repository. A neutral shared namespace is the only name that is not a lie in one of the two consumers, and it gives future shared layers an obvious home | Medium — a rename after external adoption is a breaking change. Decide before N2 (§17 item 1) |
| **P3** | **Shareable packages never import `llmwiki`.** Enforced by an AST test that fails the build, not by convention | This is the single property that makes extraction a copy rather than a project. Guarded mechanically or it will not survive three months of feature work | N/A — this is the invariant |
| **P4** | **Storage stays sync at the core; async is a thin façade** (`AsyncObjectStore`, `asyncio.to_thread`) | `llmwiki` is sync throughout; FUND's `StorageBackend` is async and already implements its async methods by delegating to sync ones via `asyncio.to_thread`. A sync core with an async wrapper is what both callers already are underneath, and it keeps the fakes trivially testable | Low — the façade is ~40 lines |
| **P5** | **`agentkit-llm`'s stable core is the narrow `complete()` protocol.** LangChain `get_llm()` → `BaseChatModel` is an **optional extra**, added in N7 (LangChain arrived early as an *implementation* of `complete()` for non-Anthropic providers on 2026-09-01; the public accessor is still N7), and the compiler is *not* migrated onto it in Phase 0.5 | Design v1.4 §4.7 asks for LangChain-native models, and FUND is already a LangChain shop (`langchain`, `langgraph`, `langchain-core` in `fund-models`' dependencies). But `complete()` carries the prompt-cache breakpoints, forced-tool structured output and measured per-call cost that plan-1.1 §8.2's cost guarantees depend on, and the whole suite is written against it. Adding a second surface is additive; replacing the first is a rewrite of the load-bearing cost machinery for no Phase 0 benefit | Low — both surfaces coexist; §7.5 defines the contract between them |
| **P6** | **`pip install llmwiki` pulls no web framework, no cloud SDK, and no extractor engine.** `[service]`, `[r2]`, `[extractors]`, `[anthropic]` are extras | This is what design v1.4's diagram *means* by drawing Core Wiki Package separate from Interface Layer. Verified by a test that asserts the import graph, not by reading `pyproject.toml` | Low |
| **P7** | **No `os.environ` reads inside shareable packages, except one explicit `from_env()` classmethod per config object** | Design v1.4 §4.7 item 6. Contrast with FUND's `fund_models/storage.py`, which reads seven env vars at *module import* — a package that cannot be configured by its caller, only by its process | Low now; high once consumers exist |
| **P8** | **Independent semantic versions, one CHANGELOG each.** `agentkit-*` start at `0.1.0`; `llmwiki` bumps to `0.10.0` at N3 and `1.0.0` only at the plan-1.1 §14 criteria | Design v1.4 §4.7 item 4. Versions that move in lockstep are one package wearing three hats | Low |
| **P9** | **`git init` before any file moves** | A refactor that renames and moves ~15 files across 4 directories, with no ability to diff or revert, is one bad `mv` from losing the green baseline | N/A — prerequisite |

---

## 5. Target Repository Layout

```
llm-wiki-svc/
├── CLAUDE.md
├── HISTORY.md
├── README.md
├── docs/implement-plan.md               # Part I: Phase 0 behaviour · Part II: this plan (packaging + Phase 1)
├── llmwiki-KB-design.md                 # v1.3
├── llmwiki-KB-design_v1.4.md            # v1.4 — authoritative design
├── pyproject.toml                       # the llmwiki distribution (§8)
├── requirements.txt                     # pinned lockfile for the dev venv (all three)
├── .env.example                         # §14
│
├── packages/                            # ← new: independently-buildable distributions
│   ├── agentkit-storage/
│   │   ├── pyproject.toml               # name = "agentkit-storage"
│   │   ├── README.md                    # contract + FUND migration notes
│   │   ├── CHANGELOG.md
│   │   └── src/agentkit/storage/
│   │       ├── __init__.py              # ObjectStore, ObjectNotFound, exports
│   │       ├── base.py                  # ← moved from llmwiki/storage/base.py
│   │       ├── local.py                 # ← moved from llmwiki/storage/local.py
│   │       ├── r2.py                    # ← moved from llmwiki/storage/r2.py
│   │       ├── asyncio.py               # NEW: AsyncObjectStore façade (P4)
│   │       ├── config.py                # NEW: StorageConfig + from_env() (P7)
│   │       └── memory.py                # NEW: in-memory store, for consumers' tests
│   │   └── tests/                       # ← test_local_store.py, test_store_contract.py move here
│   │
│   └── agentkit-llm/
│       ├── pyproject.toml               # name = "agentkit-llm"
│       ├── README.md                    # provider matrix + stability contract
│       ├── CHANGELOG.md
│       └── src/agentkit/llm/
│           ├── __init__.py
│           ├── base.py                  # ← moved: LLMClient, LLMResponse, TokenBudgetExceeded
│           ├── cost.py                  # NEW home of CostRecord (← llmwiki/models/plan.py) + PRICING
│           ├── config.py                # NEW: LLMConfig + from_env() (design v1.4 §4.7)
│           ├── factory.py               # NEW: get_client() — provider mapping
│           ├── providers/
│           │   ├── anthropic.py         # ← moved from llmwiki/llm/anthropic_client.py
│           │   └── fake.py              # ← moved from llmwiki/llm/fake.py
│           ├── callbacks.py             # NEW (N6): TPS / usage handlers
│           ├── prompts.py               # NEW (N6): versioned prompt registry
│           ├── skills.py                # NEW (N6): skill scan + load_skill
│           ├── memory.py                # NEW (N6): working memory
│           ├── context.py               # NEW (N6): context-window management
│           ├── agent_base.py            # NEW (N6): AgentBase
│           ├── langchain.py             # NEW (N7, extra): get_llm() -> BaseChatModel
│           └── eval/                    # NEW (N7, extra): LangSmith datasets + runners
│       └── tests/
│
├── src/llmwiki/                         # the core wiki package (§8)
│   ├── __init__.py  py.typed  config.py  factory.py
│   ├── models/                          # source.py  chunk.py  page.py  plan.py
│   ├── storage/
│   │   └── layout.py                    # ← STAYS. Key construction is wiki domain knowledge
│   ├── extractors/  embedding/  vector/FPhase
│   ├── wiki/        chains/prompts/
│   ├── pipeline/    agent/    tools.py
│   ├── api/  mcp/  cli.py                # the service (§9) — installed by extra [service]
│   └── (llm/ removed — re-exported from agentkit.llm for one deprecation cycle)
│
├── tests/                               # llmwiki's own suite; unchanged in count
├── scripts/                             # bootstrap_indexes · smoke_flow · backfill
└── docs/
```

### 5.1 Two boundary calls worth stating explicitly

**`storage/layout.py` does not move.** It builds `raw/<sha>/original`, `wiki/<slug>.md`,
`wiki/_meta/cost.jsonl` — that is the wiki's keyspace, and a financial-research repo has no use for
it. `agentkit-storage` owns *bytes at a key*; `llmwiki` owns *what the keys mean*. It keeps living
under `src/llmwiki/storage/` and importing `agentkit.storage`. `tests/unit/test_layout.py` stays put.

**`chains/prompts/*.md` do not move.** `summarize_source.md`, `plan_compile.md`, `patch_page.md`,
`create_page.md`, `answer_query.md` are wiki-compiler prompts. `agentkit.llm.prompts` (N6) provides
the *registry mechanism*; `llmwiki` provides the *content*. Design v1.4 §4.7 makes the same cut:
"The layer does not own wiki storage, vector indexes, or compilation logic."

---

## 6. Unit 1 — `agentkit-storage`

### 6.1 Contract

The Phase 0 `ObjectStore` protocol is already the right shape and is **kept verbatim** — `put`,
`get`, `exists`, `list`, `delete`, plus `ObjectNotFound`. It is deliberately bytes-only and flat:
no directories, no metadata sidecar, no tables. Three additions, each justified by a named consumer:

| Addition | Shape | Why |
|---|---|---|
| Text convenience | `put_text(key, text, content_type="text/markdown")` / `get_text(key) -> str` | Both consumers hand-roll `.encode()`/`.decode()` at nearly every call site. FUND's abstraction is text-first (`write_text` / `read_text`) |
| Key prefix | `ObjectStore(..., prefix: str = "")`, applied on every operation | FUND runs with `R2_PREFIX=fund-data`; llmwiki uses the bucket root. Without this, FUND cannot adopt the package without changing its object layout — a data migration, which this plan will not ask for |
| Async façade | `AsyncObjectStore(store)` with `await put/get/exists/list/delete/put_text/get_text` | FUND's `StorageBackend` is async (P4). The façade is `asyncio.to_thread` around the sync store — exactly what FUND's `LocalStorage` already does internally |

Plus a `MemoryObjectStore` (dict-backed) so that a *consumer's* tests can run offline without
touching a filesystem. `llmwiki` keeps using `LocalObjectStore` for its own tests, unchanged.

### 6.2 What explicitly stays out

FUND's `utils/storage.py` mixes file I/O with `query_database`, `insert_into_table`, `update_table`,
per-table `asyncio.Lock`s, CSV report schemas and a MySQL path. **None of that comes into
`agentkit-storage`.** A shared package that grows a table abstraction to satisfy one consumer is how
shared packages become unshareable. Those stay in FUND's `StorageAgent`, layered *on top of* the
`ObjectStore` it gets from this package.

### 6.3 Extraction cost — measured, not estimated

```
src/llmwiki/storage/base.py     →  agentkit/storage/base.py     0 import rewrites
src/llmwiki/storage/local.py    →  agentkit/storage/local.py    1 import rewrite
src/llmwiki/storage/r2.py       →  agentkit/storage/r2.py       1 import rewrite
```

Both rewrites are `from llmwiki.storage.base import ObjectNotFound` →
`from agentkit.storage.base import ObjectNotFound`. Ten call sites in `src/llmwiki/**` change their
import line; none change a single expression. This is the cheapest of the three extractions and is
why it goes first (N2) — it proves the mechanism on the lowest-risk unit.

---

## 7. Unit 2 — `agentkit-llm` (design v1.4 §4.7)

### 7.1 There are already two implementations, not zero

Design v1.4 §4.7 describes this package as new work "inspired by the `AgentBase` / `AgentConfig`
pattern used in other projects". That pattern is not hypothetical — it is
`FUND-financial-Research/fund_models/agent_base.py`, 320 lines, already shipping. And this repository
has an independent partial implementation in `src/llmwiki/llm/`. The package is therefore a **merge
of two working codebases**, and the merge direction matters:

| §4.7 component | FUND `agent_base.py` | llmwiki `llm/` | Seed from | Note |
|---|---|---|---|---|
| `LLMConfig` | `AgentConfig` — env-driven dataclass, provider/model/key/temp/max_tokens/base_url/timeout/retries | `Settings` fields, pydantic-settings | **FUND** | But drop FUND's `api_host`/`api_port`/`log_level` — those are service config, not LLM config |
| `get_llm()` factory | `AgentBase.get_llm()` — LangChain, multi-provider | `factory.llm_client()` — Anthropic + fake | **Both** (§7.5) | Two surfaces, one config |
| Streaming (`astream_collect`) | absent | absent | **New** | §4.7 calls it "directly reusable from the example"; the example is not in either repo. Deferred to N6 and gated |
| Cost accounting | absent | `CostRecord`, `PRICING`, `price()`, cache-read/write split | **llmwiki** | FUND has no per-call cost record. This is the most valuable thing llmwiki brings to the merge |
| Prompt caching | absent | `cache_control` on the system block, `CACHE_MIN_CHARS` | **llmwiki** | Anthropic-specific; lives in the provider adapter, not the generic surface |
| Structured output | absent | forced tool call (`tool_choice`), no parse-retry loop | **llmwiki** | |
| Retry | `with_retry(fn, max_attempts)` — generic | `_call_with_retries` — typed on Anthropic error classes | **llmwiki**, with FUND's generic helper alongside | llmwiki's distinguishes transient from bug; FUND's retries everything |
| Skills | `_load_skills`, `get_skills_context` | absent | **FUND** | N6 |
| Memory | `remember` / `recall` / `forget` | absent | **FUND** | N6 |
| Metrics / lifecycle | `start_run` / `end_run` / `get_metrics` / `setup` / `teardown` | absent | **FUND** | N6 |
| LangSmith eval | absent | absent | **New** | N7, gated |

**Read the table as a two-way trade.** FUND gets measured cost accounting, prompt caching and
reliable structured output — three things it does not have and that directly attack its LLM bill.
llmwiki gets provider-agnosticism, skills and memory — three things it does not have and that Phase 1
will want. Neither repo is donating.

### 7.2 What moves out of `llmwiki` in N4

```
src/llmwiki/llm/base.py             → agentkit/llm/base.py            (LLMClient, LLMResponse, TokenBudgetExceeded)
src/llmwiki/llm/anthropic_client.py → agentkit/llm/providers/anthropic.py
src/llmwiki/llm/fake.py             → agentkit/llm/providers/fake.py
CostRecord (models/plan.py)         → agentkit/llm/cost.py
```

`CostRecord` is the only genuinely entangled symbol. It currently lives in `models/plan.py` next to
`CompilePlan` and `CompileOp`, which is a mis-filing: it describes *one LLM call*, not one compile
plan. Moving it to `agentkit.llm.cost` puts it where it belongs and severs the last `llm/ → llmwiki/`
edge. `llmwiki.models.plan` re-exports it (`from agentkit.llm.cost import CostRecord`) so
`wiki/compiler.py`, `wiki/lint.py` and the cost-ledger code do not change at all.

**`llmwiki/factory.py` keeps `llm_client()`** — it is llmwiki's wiring of llmwiki's `Settings` onto
the package's factory, and `tools.py` calls it. What changes is one function body.

### 7.3 The stability contract

Design v1.4 §4.7 item 4 asks for an explicit stable API. It is:

```
STABLE (semver-protected from 0.1.0):
    LLMConfig                     — field names and defaults
    LLMClient                     — the complete() signature
    LLMResponse                   — .text, .data, .usage
    CostRecord                    — field names (they are written to cost.jsonl on disk)
    get_client(config)            — returns an LLMClient
    ObjectNotFound / TokenBudgetExceeded

UNSTABLE until 1.0.0 (may change in a minor release, documented in CHANGELOG):
    everything under providers/, callbacks, prompts, skills, memory, context,
    agent_base, langchain, eval
```

`CostRecord`'s fields are stable for a reason that is easy to miss: they are serialized to
`wiki/_meta/cost.jsonl` and read back by `llmwiki cost`. Renaming a field silently invalidates every
historical cost record — which is the data plan-1.1 §14's "cost does not grow with corpus size"
success criterion is measured from.

### 7.4 Provider extras

Per design v1.4 §4.7 item 3, and matching the actual providers the two consumers use today
(llmwiki: Anthropic; FUND: OpenAI default, plus NVIDIA endpoints and Anthropic in its requirements):

```toml
[project]
dependencies = ["pydantic>=2"]                  # that is the whole hard dependency list

[project.optional-dependencies]
anthropic  = ["anthropic>=1.0"]
langchain  = ["langchain-core>=0.3"]
openai     = ["agentkit-llm[langchain]", "langchain-openai"]
google     = ["agentkit-llm[langchain]", "langchain-google-genai"]
nvidia     = ["agentkit-llm[langchain]", "langchain-nvidia-ai-endpoints"]
deepseek   = ["agentkit-llm[langchain]", "langchain-deepseek"]
openrouter = ["agentkit-llm[langchain]", "langchain-openrouter"]
langsmith  = ["langsmith"]
all-providers = ["agentkit-llm[openai,google,nvidia,deepseek,openrouter]"]
```

**Landed early, 2026-09-01 (before N0), in `llmwiki`'s own `pyproject.toml`.** The five provider
extras above exist today under the `llmwiki[...]` name and move to `agentkit-llm[...]` unchanged at
N4. `langchain-core` is also in `[dev]`, so the adapter's unit tests run against a scripted
`BaseChatModel` with no provider SDK and no network.

There is no `local` extra. A self-hosted endpoint - vLLM, Ollama, LM Studio, llama.cpp - is
`LLM_PROVIDER=openai` with `LLM_BASE_URL` pointed at its OpenAI-compatible route, which is one code
path fewer than a dedicated adapter and the way all four of those servers expect to be called.

**Superseded, 2026-09-14 (Phase 1 local-LLM routing).** The reasoning above traded a dedicated
adapter for simplicity, but sharing the single `"openai"` row also meant a self-hosted endpoint and
real cloud OpenAI could never both be active at once (one `OPENAI_BASE_URL`, one value). The user
chose clarity over that one-code-path saving: `vllm` and `llamacpp` are now their own
`llmwiki.llm.providers.REGISTRY` entries (`src/llmwiki/llm/providers.py`), each still wrapping
`ChatOpenAI`/`langchain_openai` - no new adapter, no new dependency - but resolved in
`config/providers.py` to its own `*_API_KEY`/`*_BASE_URL` pair (`VLLM_*`, `LLAMACPP_*`), leaving
`OPENAI_API_KEY`/`OPENAI_BASE_URL` free for real cloud OpenAI. See `HISTORY.md`'s 2026-09-14 entry.

`pydantic` alone as the hard dependency is the design goal: importing `agentkit.llm` must not import
any provider SDK. `providers/anthropic.py` already does `import anthropic` *inside* `__init__`, so
this holds today without modification.

### 7.5 P5 in detail: two surfaces, one config

```
                       LLMConfig  (one config object, one set of env vars)
                            │
             ┌──────────────┴───────────────┐
             ▼                              ▼
   get_client() -> LLMClient        get_llm() -> BaseChatModel
   .complete(op=…, system=…,        LangChain chat model, plugs
    prompt=…, schema=…)             directly into LangGraph nodes
             │                              │
   cost accounting, prompt          provider breadth, tool-calling,
   caching, forced-tool JSON        streaming, LangSmith tracing
             │                              │
   used by: llmwiki compiler,       used by: LangGraph agents (Phase 1),
   ingest, query agent              FUND agents, notebooks
```

The contract between them, so this does not become two divergent libraries:

1. **One config.** Both are constructed from the same `LLMConfig`. A provider configured for one is
   configured for the other.
2. **`get_client()` may be implemented over `get_llm()`, never the reverse.** If a provider has no
   native adapter, `get_client()` falls back to wrapping a `BaseChatModel` (accepting that cost
   accounting degrades to LangChain's `usage_metadata` and prompt caching is unavailable). Anthropic
   keeps its native adapter because that is where the cost machinery earns its keep.
3. **`complete()` never gains a LangChain type in its signature.** That is what keeps
   `agentkit-llm[anthropic]` installable with no LangChain present at all.

**Rule 2 landed early, 2026-09-01 (before N0).** `LLM_PROVIDER` values other than `anthropic` and
`fake` now build a `BaseChatModel` from `llmwiki.llm.providers.REGISTRY` and wrap it in
`llmwiki.llm.langchain_client.LangChainLLM`, which implements the existing `LLMClient` protocol.
`complete()`'s signature is untouched, so `wiki/compiler.py` and `agent/query.py` did not change,
and P5 still holds: the compiler was **not** migrated onto a LangChain surface.

What that early landing does *not* include, and what N7 still owes: the public `get_llm() ->
BaseChatModel` accessor (rule 1's second surface), the `[langsmith]` eval helpers, and the move of
all of this under `agentkit.llm`. Only the inward-facing half - LangChain as an implementation
detail of `complete()` - shipped.

The degradation rule 2 anticipated is real and measured, not theoretical:

| | native `anthropic` | via LangChain |
|---|---|---|
| Prompt caching | `cache_control` breakpoint on the system block | none - no portable concept; the stable prefix is re-billed every call |
| Structured output | forced tool call | forced tool call (`bind_tools(..., tool_choice="emit")`) - the same JSON schemas, unmodified |
| Token counts | SDK `usage` | LangChain `usage_metadata` - real either way |
| USD cost | every model priced | `0.0` unless the model is in `llmwiki.llm.pricing.RATES` |
| Retries | explicit backoff on transient errors | left to the integration, which already retries |

`cost_usd = 0.0` means "not priced here", never "free"; the ledger records real token counts
regardless, and `RATES` is one dict entry away from pricing any model. Anthropic keeps its native
adapter precisely because that top-left cell is where the cost machinery earns its keep.

### 7.6 The provider-generic environment contract

`agentkit-storage` is cheap for FUND to adopt largely because six of its seven environment variables
already match (§11.1). That alignment was luck — both repos were written against the same R2 setup.
The LLM layer does not get the same luck, so its contract is set deliberately, **now**, while this
repository is the only consumer and renaming is still free:

| Variable | Meaning | llmwiki (was) | FUND `AgentConfig` |
|---|---|---|---|
| `LLM_PROVIDER` | Which adapter to build | `LLM_BACKEND` (`anthropic \| fake`) | `LLM_PROVIDER` (`openai` default) ✅ |
| `LLM_API_KEY` | Credential for whichever provider is named | `ANTHROPIC_API_KEY` | `LLM_API_KEY` ✅ |
| `LLM_MODEL` | Default model id | `LLM_DEFAULT_MODEL` | `LLM_MODEL` ✅ |
| `LLM_BASE_URL` | Gateway, proxy, or OpenAI-compatible endpoint | — | `LLM_BASE_URL` ✅ |

**The generic names are FUND's names.** `fund_models/agent_base.py` already reads exactly these four,
so the shared contract costs FUND no environment migration at all — the same property that makes the
storage adoption cheap. What changed is on this side: a provider-specific `ANTHROPIC_API_KEY` cannot
be the credential variable of a provider-agnostic layer.

Two names stay out of the shared contract on purpose:

- **`COMPILE_EXECUTOR_MODEL`** — the compiler's escalation to a stronger model for patch generation
  (plan-1.1 D5). That is a wiki compilation policy, not an LLM-layer concern, and it stays in
  `llmwiki.config`.
- **`EMBEDDING_*`** — design v1.4 places Vector Ops in the Core Wiki Package, not the LLM layer, so
  embeddings are not part of `agentkit-llm`. `EMBEDDING_BACKEND` keeps its current spelling.

**Adopted ahead of the adapters (landed 2026-09-01, before N0), then implemented the same day.**
The four names are read by `llmwiki.config.Settings`. `LLM_PROVIDER` now accepts seven values, and
every one of them routes to something real - `test_every_provider_the_config_accepts_can_actually_be_built`
asserts that the `Provider` literal and `providers.REGISTRY` cannot drift apart:

| `LLM_PROVIDER` | Adapter | Install |
|---|---|---|
| `anthropic` (default) | native `AnthropicLLM` | built in |
| `openai` | `ChatOpenAI` | `llmwiki[openai]` |
| `google` | `ChatGoogleGenerativeAI` | `llmwiki[google]` |
| `nvidia` | `ChatNVIDIA` | `llmwiki[nvidia]` |
| `deepseek` | `ChatDeepSeek` | `llmwiki[deepseek]` |
| `openrouter` | `ChatOpenRouter` | `llmwiki[openrouter]` |
| `fake` | `FakeLLM` | built in |

`local` was dropped from the literal rather than implemented: it is `openai` plus `LLM_BASE_URL`
(§7.4). A configured provider whose extra is absent raises a `RuntimeError` naming the exact
`pip install` command, never a bare `ModuleNotFoundError`.

**Deprecated aliases, removed at N4.** `ANTHROPIC_API_KEY`, `LLM_DEFAULT_MODEL` and `LLM_BACKEND`
remain real settings fields — not properties — so that both a pre-rename `.env` *and* a pre-rename
constructor kwarg keep working. That detail is load-bearing: `Settings` sets `extra="ignore"`, so a
field removed outright but still passed as a kwarg would be silently swallowed, and a test fixture
asking for `llm_backend="fake"` would quietly build a real Anthropic client against a live API key.
After validation both spellings hold the same value, and an explicitly-set generic name wins over its
alias. N4 deletes the aliases, the mirroring validator, and the tests covering them.

---

## 8. Unit 3 — `llmwiki` core (design v1.4 §4.6)

### 8.1 The dependency budget

Design v1.4 §4.6's Core Wiki Package box is only meaningful if it can be installed alone. Today's
`pyproject.toml` has 14 hard dependencies. After N3:

| Package | Today | After N3 | Extra |
|---|---|---|---|
| `pydantic`, `pydantic-settings`, `python-frontmatter`, `numpy`, `httpx`, `python-dotenv` | hard | **hard** | — |
| `agentkit-storage`, `agentkit-llm` | — | **hard** | — |
| `fastapi`, `uvicorn`, `fastmcp` | hard | extra | `[service]` |
| `boto3` | hard | extra | `[r2]` (→ `agentkit-storage[r2]`) |
| `pymupdf`, `trafilatura`, `youtube-transcript-api` | hard | extra | `[extractors]` |
| `anthropic` | hard | extra | `[anthropic]` (→ `agentkit-llm[anthropic]`) |

Six hard dependencies plus the two sibling packages. `pip install llmwiki` then gives a consumer the
models, the layout, the page/gist machinery, the compiler and the query agent, with fakes for
everything external — which is exactly the offline mode `scripts/smoke_flow.py --offline` already
exercises, so this configuration is *already tested*, it just is not currently *installable*.

`pip install llmwiki[service,r2,extractors,anthropic]` reproduces today's install byte for byte. The
Dockerfile and `docker-compose.yml` switch to that form in N3 and nothing about the running service
changes.

### 8.2 Verified, not declared

A `pyproject.toml` that lists extras proves nothing; an import can still sneak a hard dependency in.
N3 adds `tests/unit/test_dependency_budget.py`, which walks the AST of `src/llmwiki/**` excluding
`api/`, `mcp/`, `cli.py` and the concrete adapter modules, and asserts that no module imports
`fastapi`, `fastmcp`, `uvicorn`, `boto3`, `pymupdf`, `trafilatura`, `youtube_transcript_api` or
`anthropic` at module level. The adapter modules are exempt because they already import their SDK
function-locally (plan-1.1 §4.3) — and the test asserts *that* too.

---

## 9. Unit 4 — The Service (`api/` · `mcp/` · `cli.py`)

**Unchanged.** Design v1.4 draws the Interface Layer as platform-specific and non-shareable, which is
what it already is. `test_transport_layer_only_calls_tools` has enforced since M0 that these three
modules validate input, call `tools.py`, and serialize the result.

The only change is packaging: they ship inside the `llmwiki` distribution but their dependencies move
behind the `[service]` extra (§8.1). Importing `llmwiki.api.app` without `[service]` installed raises
`ModuleNotFoundError: fastapi` — which is correct and legible.

They do **not** move to a `packages/llmwiki-service/` distribution. That would be a fourth
distribution serving no consumer: nobody wants the FastAPI app without the wiki.

---

## 10. Enforcement: The Boundary Guards

P3 is the invariant the whole plan rests on. It gets a test, in the same spirit as the existing
`test_layering.py` — and for the same stated reason: *the boundary must be mechanical rather than
aspirational.*

### 10.1 `tests/unit/test_package_boundaries.py` (new, N1)

Three assertions, walking the AST of all three source trees:

```python
# 1. EXTRACTION GUARD — the load-bearing one.
#    A shareable package that imports llmwiki cannot be lifted into its own repo.
def test_shareable_packages_never_import_llmwiki():
    for tree in (PKGS / "agentkit-storage/src", PKGS / "agentkit-llm/src"):
        for module in tree.rglob("*.py"):
            assert "llmwiki" not in _imported_roots(module)

# 2. SIBLING GUARD — storage and llm are peers, not a stack.
def test_shareable_packages_never_import_each_other():
    ...  # agentkit.storage must not import agentkit.llm, and vice versa

# 3. CONFIG-INJECTION GUARD (P7, design v1.4 §4.7 item 6).
#    os.environ may be read in exactly one place per package: config.py's from_env().
def test_shareable_packages_read_env_only_in_from_env():
    ...
```

Guard 3 is the one that prevents the specific failure mode visible in
`FUND-financial-Research/fund_models/storage.py`, which reads `STORAGE_BACKEND`, `R2_BUCKET`,
`R2_PREFIX`, `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` at module import time.
That module cannot be configured by a caller at all — only by the process environment — which makes
it untestable against two configurations in one test run and unusable by a consumer that needs two
stores. It is a good abstraction defeated by where it reads its config.

### 10.2 `tests/unit/test_layering.py` (rewritten, N1)

The existing test survives, with its `FORBIDDEN` map re-expressed over v1.4's layers rather than
Phase 0's directory names. Concretely: the `storage` and `llm` keys are deleted (those layers leave
the distribution), and both names are removed from every other layer's forbidden set — but the ladder
`models → extractors/embedding/vector → wiki/agent → pipeline → tools → api/mcp/cli` is unchanged and
every remaining edge keeps its current strictness.

**This is a modification, not a weakening**, and the distinction matters because `CLAUDE.md` names
this test as load-bearing. The rule stated there — "If a new import fails it, move the code, do not
widen the rule" — still applies. What N1 does is delete rows about code that no longer lives in the
tree; §13.2 records exactly which rows and why.

### 10.3 The split rehearsal (N8)

The guards say extraction is *possible*. N8 proves it, in CI-able form:

```bash
scripts/split_rehearsal.sh agentkit-storage
#   copies packages/agentkit-storage/ to a temp dir, with no other repo file
#   python -m venv && pip install . && pytest
#   must pass with zero access to src/llmwiki
```

If the rehearsal passes, the graduation to a real repository (§15) is a `git filter-repo` and a CI
config — hours, not a project. If it fails, a coupling has crept back in and the guards missed it,
which is itself the finding.

---

## 11. Cross-Repo Consumption: `FUND-financial-Research`

Design v1.4 draws an `OtherRepos --> CoreWiki` / `OtherRepos --> LLMlayer` edge. An edge nobody has
traversed is a hypothesis. N5 traverses it.

### 11.1 Why FUND is the right first consumer

- It is already a package publisher, not just an app: `fund-models` has a real `pyproject.toml`
  with `[tool.setuptools.packages.find]`. Adding a path dependency is routine there.
- It has a **duplication problem this package solves**: `utils/storage.py` (573 lines) and
  `fund_models/storage.py` (173 lines) are two implementations of one abstraction, in one repo.
- Its `StorageAgent` (`agents/storage/src/main.py`, 763 lines) is explicitly the single access point
  for shared file I/O — "only this container holds R2 credentials". That is precisely the shape
  `agentkit-storage` is designed to sit under.
- Its environment variables already match this repository's, without negotiation:

| Variable | llmwiki | FUND | Shared package |
|---|---|---|---|
| `STORAGE_BACKEND` | `r2 \| local` | `local \| r2` | ✅ same values, same meaning |
| `R2_BUCKET` | ✅ | ✅ | ✅ |
| `R2_ENDPOINT_URL` | ✅ | ✅ | ✅ |
| `R2_ACCESS_KEY_ID` | ✅ | ✅ | ✅ |
| `R2_SECRET_ACCESS_KEY` | ✅ | ✅ | ✅ |
| `R2_PREFIX` | — | `fund-data` | ✅ optional, default `""` (§6.1) |
| Local root | `LOCAL_STORAGE_PATH` | constructor arg / `DATA_DIR` | ⚠️ **the only conflict** — resolved by constructor arg; `from_env()` reads `STORAGE_ROOT`, falling back to `LOCAL_STORAGE_PATH` then `DATA_DIR` |

Six of seven variables already agree. That is not luck — both were written against the same R2
setup — and it means adoption needs no environment migration in FUND's docker-compose files.

### 11.2 N5 scope: deliberately one call site

N5 does **not** rewrite FUND's storage layer. It:

1. Adds `agentkit-storage` as a path dependency in FUND (`pip install -e ../llm-wiki-svc/packages/agentkit-storage`).
2. Re-implements **`fund_models/storage.py` only** — the smaller, 173-line module — as a thin
   adapter over `agentkit.storage.AsyncObjectStore`, preserving its public surface
   (`get_storage()`, `write_text`, `write_bytes`, `read_text`, `write_text_sync`, `describe`) exactly.
3. Runs FUND's existing test suite. Green, or the adoption is reverted and the reason recorded.

`utils/storage.py` and the `StorageAgent`'s table/database half stay untouched. The point of N5 is to
find out what a real second consumer breaks — mismatched async signatures, a missing prefix, a
different error type — while the blast radius is one 173-line module and the package is still
unversioned and unpublished.

**The finding is the deliverable.** If N5 reveals that FUND needs something `agentkit-storage`
refuses to provide (P4's sync core, §6.2's exclusion of tables), that is the signal to stop expanding
the package, and it arrives before anything is published.

### 11.3 LLM layer adoption is gated on N5

FUND adopting `agentkit-llm` is a larger step — its agents are LangChain-native, so it needs the N7
surface (P5), and its `AgentConfig` is used across the whole agent fleet. One part of it is already
paid for: §7.6 aligned this repository's LLM variables onto the names FUND's `AgentConfig` already
reads, so the eventual adoption needs no environment migration on either side. It is scheduled *after* the
storage adoption has taught us how cross-repo consumption actually behaves here. Attempting both at
once means a failure in either one is un-diagnosable.

---

## 12. Milestones N0–N8

Sizes: `[S]` under a day, `[M]` one to three days, `[L]` more. Every milestone's exit criteria
include the standing rule from §1: **189 unit tests green, 6 integration collected,
`smoke_flow.py --offline` passing.** Only the *additional* exit criteria are listed.

| # | Milestone | Size | Additional exit criteria |
|---|---|---|---|
| **N0** | **Safety net** | `[S]` | **Partly done 2026-09-01:** `pythonpath = ["."]` added so bare `pytest` works (§1.1); `.gitignore` verified (`.env`, `.venv`, `.data/` covered, `.python-version` deliberately not); development interpreter pinned to 3.11.14 in `.python-version` so `[tool.mypy] python_version` is true and plain `mypy` passes. **Still owed:** `git init`, baseline committed, tag `phase0-baseline`, `HISTORY.md` entry for the commit itself |
| **N1** | **Boundary guards** | `[S]` | `test_package_boundaries.py` exists and passes against the *current* tree (guards 1–3 are vacuously true before extraction — that is the point: they must be green before and after). `test_layering.py` rewritten per §10.2. Test count rises; no test weakened |
| **N2** | **Extract `agentkit-storage`** | `[M]` | `packages/agentkit-storage/` builds a wheel. Its own tests pass in isolation (`cd packages/agentkit-storage && pytest`). `llmwiki` imports `agentkit.storage`. `test_local_store.py` + `test_store_contract.py` relocated. Async façade, prefix, text helpers, `MemoryObjectStore` implemented and tested. `storage/layout.py` unmoved |
| **N3** | **Slim the core wiki package** | `[S]` | `pyproject.toml` extras per §8.1. `test_dependency_budget.py` passes. A clean venv with `pip install .` (no extras) can `import llmwiki.wiki.compiler` and run the offline smoke flow. Dockerfile/compose use `.[service,r2,extractors,anthropic]`. `llmwiki` → `0.10.0` |
| **N4** | **Extract `agentkit-llm` core** | `[M]` | `packages/agentkit-llm/` builds. `LLMConfig`, `get_client()`, `complete()`, `CostRecord`, Anthropic + fake providers moved. `CostRecord` re-exported from `llmwiki.models.plan`; no call site outside `models/plan.py` changes. **New** `test_llm_contract.py` (§13.3) — the first direct test of this layer. `cost.jsonl` records written before and after are byte-identical for the same input |
| **N5** | **Cross-repo proof: FUND adopts `agentkit-storage`** | `[M]` | `fund_models/storage.py` reimplemented over `agentkit.storage` with its public surface unchanged; FUND's test suite green; findings written to `docs/cross-repo-adoption.md` and `HISTORY.md`. **Gate: N6–N8 do not start until this is written up** |
| **N6** | **`agentkit-llm` breadth** — prompts registry, skills, memory, context, `AgentBase`, callbacks | `[M]` | **Gated on N5.** Seeded from FUND's `agent_base.py` per §7.1, generalized: no FUND-specific config fields, no `os.getenv` outside `from_env()`. Each component has a test. **Nothing here is imported by `llmwiki` in Phase 0.5** — it is built for the consumer identified in N5, not speculatively |
| **N7** | **LangChain / LangGraph / LangSmith surface** | `[S]` | **Gated on N6.** Reduced by the 2026-09-01 early landing, which already ships LangChain *inward* (five providers behind `complete()`, §7.5). Still owed: the public `get_llm() -> BaseChatModel` accessor behind `[langchain]`, `eval/` behind `[langsmith]`, and §7.5's three contract rules asserted by tests, including: `import agentkit.llm` with no LangChain installed still works. The compiler is **not** migrated (P5) |
| **N8** | **Release & split rehearsal** | `[S]` | `scripts/split_rehearsal.sh` passes for both packages. CHANGELOGs and READMEs (contract + provider matrix + stability, per design v1.4 §4.7 item 4). Versions tagged. §15 graduation checklist evaluated and the answer recorded |

### 12.1 Ordering rationale

Storage before LLM (N2 before N4) because storage has **zero** coupling to untangle (§1.1) — it
proves the packaging mechanism on the unit where a failure means "the mechanism is wrong", not "this
particular extraction was hard". Core slimming (N3) sits between them because it is the smallest
change that makes design v1.4's §4.6 claim literally true, and it is independent of both extractions.

The N5 gate before N6/N7 is deliberate and is the plan's main defence against speculative generality.
§4.7 specifies a broad surface — skills, memory, context management, `AgentBase`, eval helpers — and
**llmwiki needs none of it**. The compiler needs `complete()`. Building the rest before a real
consumer has exercised the packaging is how a shared package acquires seven abstractions and one user.

---

## 13. Testing Plan

Per `~/.claude/CLAUDE.md`, all four mandated points are addressed explicitly.

### 13.1 Must pass all existing tests

The suite is **189** unit tests (1.6 s, no network) plus 6 integration tests collected but deselected
— 182 at the time this plan was written, plus the 7 that landed with §7.6's env contract, which
changed no existing assertion.
**Every one of them must still pass, unmodified, at every milestone.** This is a restructuring plan:
if an assertion has to change, behaviour changed, and the milestone is wrong (§1).

Three tests are named load-bearing in `CLAUDE.md` and get extra scrutiny here because this plan
touches the layer they guard:

| Test | What this plan does to it | Guard against weakening |
|---|---|---|
| `test_layering.py` | **Rewritten** (§10.2) — `storage` and `llm` rows removed because those layers leave the distribution. Every remaining edge keeps its strictness | The removed rows are replaced by *stricter* rules in `test_package_boundaries.py`: a package that cannot import `llmwiki` at all is more constrained than a layer that merely could not import upward |
| `test_compiler_no_full_scan.py` | **Untouched.** It imports `llmwiki.storage` (2 references) — those become `agentkit.storage` import lines only | Design v1.4 §4.4's cost constraint is unaffected by packaging. If this test needs any change beyond an import path, stop |
| `test_agent.py::test_every_citation_resolves_to_a_real_raw_object` | **Untouched** (1 storage import line) | Same |

The pre-commit gate is unchanged and runs at every milestone:
`pytest && ruff check . && mypy && python scripts/smoke_flow.py --offline`, extended from N2 with
`cd packages/<pkg> && pytest` for each extracted package.

### 13.2 Obsolete tests announced for removal

No test file is deleted. Two categories of change are announced:

**(a) Rows removed from `test_layering.py`'s `FORBIDDEN` map (N1).** Obsolete because the code they
describe no longer lives in `src/llmwiki/`:

- the `"storage": {...}` entry and the `"llm": {...}` entry (whole rows);
- the strings `"storage"` and `"llm"` from every other layer's forbidden set;
- `"storage"` and `"llm"` from `test_transport_layer_only_calls_tools`'s `allowed` set.

Justification: `_layer_of()` derives layers from directories under `src/llmwiki/`. After N2 and N4
those directories are gone (`storage/` retains only `layout.py`, which keeps its existing `storage`
layer rules), so the rows match nothing and would silently pass forever — a dead rule that reads like
a live one. Their protection is replaced and strengthened by `test_package_boundaries.py` (§10.1).

**(b) Tests relocated, not removed (N2).** `tests/unit/test_local_store.py` and
`tests/unit/test_store_contract.py` move to `packages/agentkit-storage/tests/`. They test the
`ObjectStore` contract, which is now that package's contract to keep. Total test count across the
repository does not fall; the pre-commit gate runs both suites.

`tests/unit/test_layout.py` **stays** in `tests/unit/` — `layout.py` does not move (§5.1).

**Removed 2026-09-01, before N0:** `test_config.py::test_unimplemented_provider_names_the_milestone_that_adds_it`.
It asserted that `LLM_PROVIDER=openai` fails with a message naming milestone N7. `openai` now works,
so the assertion was documenting a gap that no longer exists. Replaced in place by
`test_every_provider_the_config_accepts_can_actually_be_built`, which asserts the stronger property
the old test was standing in for: every value the config accepts routes to a real adapter.

Every removal and relocation is logged in `HISTORY.md` with its milestone, per `CLAUDE.md`.

### 13.3 New tests announced for new code paths

| Milestone | New test | Covers |
|---|---|---|
| pre-N0 | **7 tests in `test_config.py`** (landed 2026-09-01) | §7.6 — generic names canonical; deprecated aliases still configure them; both spellings agree after resolution; generic name wins over its alias; `LLM_API_KEY` absent from `repr`; the `Provider` literal and `providers.REGISTRY` cannot drift apart; a missing key is reported as `LLM_API_KEY` |
| pre-N0 | **`test_providers.py`** — 11 tests (landed 2026-09-01) | §7.4/7.5 — importing the registry (and `factory`) imports **no** provider SDK, checked in a fresh interpreter; the five LangChain providers are registered and Anthropic is not; every registered provider is an accepted config value; a missing extra names its `pip install` command; and, for whichever integrations are installed, the class really accepts `model` / `api_key` / `base_url` / the token-cap keyword |
| pre-N0 | **`test_langchain_client.py`** — 9 tests (landed 2026-09-01) | §7.5 — text and forced-tool paths against a scripted `BaseChatModel`; prose despite `tool_choice` is still parsed; usage carries op/model/version; cached tokens are subtracted from `input_tokens` so a cache hit is not billed twice; an unpriced model records real tokens and `cost_usd == 0.0`; a priced one matches `RATES`; a per-call `model` builds its own chat model (D5 escalation); chat models are cached per (model, token cap) |
| N0 | — (config fix only; verified by the suite running under bare `pytest`) | |
| N1 | `test_package_boundaries.py` — 3 guards | P3, P7; §10.1 |
| N2 | `test_async_facade.py` | `AsyncObjectStore` delegates correctly and does not block the loop |
| N2 | `test_key_prefix.py` | Prefix applied on put/get/exists/list/delete; `list()` returns **unprefixed** keys; empty prefix is byte-identical to today's behaviour |
| N2 | `test_text_helpers.py` | `put_text`/`get_text` round-trip, encoding, content-type default |
| N2 | `test_memory_store.py` | `MemoryObjectStore` passes the same parametrized contract test as local + R2 |
| N3 | `test_dependency_budget.py` | §8.2 — no module-level import of an extra's dependency outside its adapter; adapters import their SDK function-locally |
| N4 | **`test_llm_contract.py`** | The first direct test of the LLM layer. Parametrized over fake + a mocked Anthropic (via `respx`): `complete()` returns `LLMResponse`; `schema` produces `.data`; usage is populated; `TokenBudgetExceeded` raises where documented |
| N4 | `test_cost_record_compat.py` | A `cost.jsonl` line written by the pre-N4 code deserializes into the post-N4 `CostRecord` with identical field values — protects the §7.3 on-disk contract and plan-1.1 §14's cost measurement |
| N4 | `test_llm_config_from_env.py` | `LLMConfig.from_env()` precedence; no env read outside it |
| N5 | (FUND-side) FUND's existing storage tests, run unmodified | §11.2 — the adoption is proven by the consumer's own suite, not by a new test we write to pass |
| N6 | `test_prompts.py`, `test_skills.py`, `test_memory.py`, `test_context.py`, `test_agent_base.py` | One per §4.7 component built |
| N7 | `test_langchain_surface.py` | `get_llm()` returns a `BaseChatModel`; §7.5 rules 1–3, including **`import agentkit.llm` succeeds with LangChain absent** |
| N7 | `test_eval_helpers.py` | LangSmith helpers are import-safe and no-op without `[langsmith]` |
| N8 | `scripts/split_rehearsal.sh` (not pytest, but gate-blocking) | §10.3 — each package builds and tests with zero access to `src/llmwiki` |

**Unit tests make no real API calls**, unchanged: the fake embedder, memory vector store, local
store and scripted LLM stay wired in `tests/conftest.py`. Integration tests remain `@pytest.mark.integration`
and opt-in.

### 13.4 Documentation of added/removed tests

Per `CLAUDE.md`, each milestone's `HISTORY.md` entry lists tests added, relocated or removed under
**Test coverage**. In addition:

- `CLAUDE.md`'s "Three tests are load-bearing" list gains a fourth entry at N1 —
  `test_package_boundaries.py`, with the same do-not-weaken instruction, because it is what makes
  design v1.4's shareable-package claim enforceable.
- `CLAUDE.md`'s "Working in this repository" block gains the per-package test commands at N2.
- `docs/testing.md` gains a section on the three-suite layout (root + two packages) at N2.
- `docs/cross-repo-adoption.md` is created at N5 with what the first real consumer broke.

---

## 14. Environment Variables & Config Injection

**The LLM variables went provider-generic on 2026-09-01, ahead of N0; nothing else changes through
N4.** §7.6 has the contract and the rationale. In short: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`
and `LLM_BASE_URL` are the shared `agentkit-llm` names, `ANTHROPIC_API_KEY` / `LLM_DEFAULT_MODEL` /
`LLM_BACKEND` keep working as deprecated aliases until N4, and `COMPILE_EXECUTOR_MODEL` and
`EMBEDDING_*` stay wiki-specific.

The provider work later the same day added **no new variable**: `LLM_PROVIDER` simply accepts more
values, and `.env.example` gained a table of which extra each one needs. Beyond that, no variable is
added or removed by N0–N4. P7 is why: shareable packages are configured
by their caller, so `llmwiki.config.Settings` remains the only env reader in this repository, and it
maps its fields onto `StorageConfig` / `LLMConfig` at construction time in `factory.py`.

```
.env  ──►  llmwiki.config.Settings  ──►  factory.py  ──►  StorageConfig(...)  ──►  ObjectStore
 LLM_PROVIDER   (the only env reader   resolves aliases  └─►  LLMConfig(...)  ──►  LLMClient
 LLM_API_KEY     in this repo)         to the generic
 LLM_MODEL                             names (§7.6)
 LLM_BASE_URL
```

The `from_env()` classmethods on `StorageConfig` and `LLMConfig` exist **for other consumers**
(FUND's `get_storage()` has no pydantic-settings layer and will use it) and are never called by
`llmwiki`. They are the single permitted `os.environ` site per package, and guard 3 in §10.1 enforces
that.

Env vars are added only at N6/N7, and only if a component needs one:

| Variable | Milestone | Default | Read by |
|---|---|---|---|
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | **landed pre-N0** | `anthropic` / empty / `claude-haiku-4-5` / empty | `llmwiki.config.Settings`, then `agentkit.llm.config.LLMConfig.from_env()` at N4 |
| `SKILLS_DIR` | N6 | unset (skills disabled) | `agentkit.llm.config.LLMConfig.from_env()` |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | N7 | unset (tracing off) | LangSmith SDK directly, not by us |
| `AGENT_MAX_TOOL_CALLS`, `AGENT_WEB_SEARCH_POLICY`, `AGENT_MAX_WEB_SEARCHES`, `WEB_SEARCH_BACKEND`, `TAVILY_API_KEY`, `LANGSMITH_EVAL_DATASET` | **landed 2026-09-16 (§20)** | `4` / `off` / `1` / `none` / empty / `llmwiki-answer-quality` | `llmwiki.config.Settings` (§20.5) |

Per `CLAUDE.md`, `.env.example` is updated in the *same change* that adds any of these, or not at all.

---

## 15. Repo Split: Graduation Criteria & Mechanics

The instruction behind this plan is that §4.7's layer "may be moved to a separated REPO in the
future". P1 defers that move; this section defines when it stops being deferred and how it happens.

### 15.1 Graduation criteria — move a package to its own repo when **any two** hold

1. **A second repository depends on it in a merged, non-experimental state** (N5's FUND adoption
   promoted from a path dependency to a real version constraint).
2. **Its release cadence has diverged** — three consecutive releases of the package that carried no
   `llmwiki` change, or vice versa. Lockstep versions mean it is still one thing.
3. **A consumer needs a version this repo is not on** — the first time FUND needs
   `agentkit-storage==0.4` while llmwiki is on `0.6`, path dependencies stop working and a real
   package index is required.
4. **A third consumer appears.** Two consumers can coordinate informally; three cannot.

Until then, the monorepo is strictly better: one `pytest`, one `ruff`, one venv, one review.

### 15.2 Mechanics when the criteria are met

Because §10.3's rehearsal has been passing continuously, the split is mechanical:

```bash
# 1. History-preserving extraction (the reason P9 exists)
git filter-repo --path packages/agentkit-storage/ --path-rename packages/agentkit-storage/:

# 2. New repo, push, add CI running the package's own suite
# 3. Publish to the chosen index (private PyPI / GitHub Packages — §17 item 3)
# 4. In llm-wiki-svc AND in FUND, replace the path dependency with a version constraint:
#      agentkit-storage>=0.4,<0.5
# 5. Delete packages/agentkit-storage/ here; test_package_boundaries.py drops that tree
```

Step 4 is the only one that can regress anything, and it is a one-line change in two files that the
existing suites verify.

### 15.3 What must be true before step 3, per design v1.4 §4.7 item 4

- Semantic versioning in effect, with the §7.3 stable/unstable split documented in the README.
- A provider matrix in the README stating which providers are tested and which are best-effort.
- A CHANGELOG with entries from `0.1.0`, including any breaking change and its migration.
- The package's own test suite green **in isolation**, which §10.3 already runs on every commit.

---

## 16. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A file move loses code or a test, unrecoverably | Medium **today** | High | P9/N0: git init and a tagged baseline *before* any move. This is why N0 is first and non-negotiable |
| Speculative generality — §4.7's full surface built for no consumer | **High** | Medium | The N5 gate (§12.1). N6/N7 do not start until a real consumer has exercised N2's packaging |
| `agentkit-llm` slowly re-acquires wiki assumptions (a `page_slug` parameter, a wiki prompt) | Medium | High — silently un-shares the package | Guard 1 in §10.1 catches imports; it cannot catch a wiki-shaped *parameter*. Compensating control: §5.1's rule that prompt *content* stays in `llmwiki`, and README review of the public surface at each release |
| `CostRecord`'s move breaks historical `cost.jsonl` parsing | Low | High — destroys the plan-1.1 §14 cost baseline | `test_cost_record_compat.py` (§13.3) asserts a pre-N4 line deserializes identically. Field names are semver-stable (§7.3) |
| Naming (P2) is chosen, adopted by FUND, then regretted | Medium | Medium | §17 item 1 forces the decision *before* N2, when the rename is free. After N5 it is a breaking change in two repos |
| P5's two surfaces diverge into two libraries | Medium | Medium | §7.5's three contract rules, asserted by tests in N7 |
| FUND adoption (N5) is blocked by something structural (async, tables, prefix) | Medium | Low — by design | N5 is scoped to one 173-line module precisely so this is cheap to discover and cheap to revert. A blocked N5 is a successful N5: it produced the finding before publication |
| Restructuring drifts into behaviour change | Medium | High | §1's rule: any milestone that needs an assertion changed is re-scoped, not merged |

---

## 17. Open Items

Answer before the milestone named. None block N0 or N1.

1. **Package naming (P2)** — is `agentkit` the right shared namespace, and are `agentkit-storage` /
   `agentkit-llm` the right distribution names? Design v1.4 §4.7 lists this as an open decision and
   suggests `llmwiki-llm`, `fund-llm-core`, `agent-llm-kit`. **Blocks N2.** Free to change now,
   breaking after N5.
2. **Does FUND actually want this?** §11 plans an adoption in a repository this plan does not own.
   The FUND side needs to agree that replacing `fund_models/storage.py` is worth the review.
   **Blocks N5**, not N2 — the package is independently useful here.
3. **Where do these packages get published** when §15 triggers — private PyPI, GitHub Packages, or
   git+ssh URLs? Design v1.4 §4.7 item 1 lists all three. **Blocks N8's publication step only**;
   path dependencies need no answer.
4. **LangSmith: hard or soft dependency?** Design v1.4 §7 question 10, restated in §4.7's open
   decisions. This plan assumes **soft** (`[langsmith]` extra, import-safe when absent) — confirm
   before N7.
5. **`AgentBase`: class to inherit, or composable helpers?** Design v1.4 §4.7's last open decision.
   FUND's existing `AgentBase` is an inheritance base with 15 methods, and FUND's agents already
   inherit from it — so N6 inherits that shape by default unless the FUND side wants the break.
   **Blocks N6.**
6. **`LLM_API_KEY` holds its placeholder verbatim (`sk-ant-...`) in the working `.env`.**
   `Settings.require()` rejects empty values and the literal `changeme`, so this one passes the
   guardrail and fails later against the API instead. Either fill it, or widen `require()` to reject
   any value that still equals its `.env.example` placeholder. Blocks nothing offline — unit tests
   and `smoke_flow --offline` never read it — but it will be the first thing to bite on a real call.
7. **Is the Core Wiki Package genuinely wanted by another repo, or only in principle?** §8 makes it
   *installable* standalone, which is worth doing regardless. But extracting it to its own repository
   (§15) should wait for a named consumer. No milestone blocked; revisit at the Phase 1 gate.

---

## 18. Documentation Obligations

Non-negotiable, per `CLAUDE.md`:

- **`HISTORY.md`** — one entry per milestone N0–N8, each with goal, implementation detail, related
  files, and test coverage (added / relocated / removed). A milestone is not done until its entry
  exists. The §2.1 deviation from design v1.4's Layer Responsibilities table is logged at N2.
- **`CLAUDE.md`** — updated at N1 (fourth load-bearing test), N2 (per-package test commands in the
  "Working in this repository" block; the three-distribution layout), N3 (extras-based install), and
  N4 (`agentkit.llm` is where the LLM client lives now).
- **`llmwiki-KB-design_v1.4.md`** — version 1.5 was taken 2026-09-05 by §19's work (§4.8, below), ahead
  of N2. Bump to **1.6** when N2 lands, recording (a) the §2.1 Storage-row change to the Layer
  Responsibilities table, and (b) the resolved answers to §7 questions 8, 9 and 10 and §4.7's open
  decisions, as they are locked by §17 above.
- **`.env.example`** — no new variables through N4 by design (§14); its provider table is kept in
  step with `llmwiki.llm.providers.REGISTRY`, and it is updated in the same change as any N6/N7
  variable.
- **`README.md`** — at N3, the install line becomes explicit about extras.
- **`packages/*/README.md`** — created with each package: the contract, the stability split (§7.3),
  and for `agentkit-llm` the provider matrix. These are what an external consumer reads first.
- **`docs/`** — `cross-repo-adoption.md` (N5, findings from the first real consumer),
  `testing.md` (N2, three-suite layout), `packaging.md` (N8, the split rehearsal and §15 procedure).

---

## 19. Multi-Provider Op Routing & Query-Agent Skill Invocation (new, v1.1)

### 19.1 Scope and relationship to §1–§18

Everything above this section is the Phase 0.5 **packaging** plan, whose one hard rule is "no
behaviour change" (§1). This section is the deliberate exception: it changes what the LLM layer does,
not just where its code lives. It is **independent of N0–N8** — it does not require, and is not
blocked by, any packaging milestone, and it does not touch `packages/agentkit-*` (those do not exist
yet). It can land before, after, or interleaved with N0–N8.

Design reference: design v1.4 §4.8 (both subsections). Read that first — this section is the
"how", not the "why".

### 19.2 `config/providers.py` and `config/ops.py`

New directory, repo root, **outside `src/llmwiki`** — not part of the installed package, not scanned
by `test_layering.py` or `test_package_boundaries.py` (§10), not shipped in the wheel:

```
config/
├── providers.py   # PROVIDERS: list[dict] — provider name -> credential env-var names
└── ops.py         # OPS: list[dict]       — op name -> provider/model/temperature/max_tokens
```

```python
# config/providers.py — committed, no secrets (only env-var *names*)
PROVIDERS = [
    {"provider": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "base_url_env": "ANTHROPIC_BASE_URL"},
    {"provider": "openai",    "api_key_env": "OPENAI_API_KEY",    "base_url_env": "OPENAI_BASE_URL"},
    {"provider": "fake"},  # offline double — no credentials, always active
]
```

```python
# config/ops.py — committed; one row per op the codebase calls llm.complete(op=...) with
OPS = [
    {"op": "summarize_source", "provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 1.0, "max_tokens": 2048},
    {"op": "plan_compile",     "provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 1.0, "max_tokens": 2048},
    {"op": "create_page",      "provider": "anthropic", "model": "claude-sonnet-5",  "temperature": 1.0, "max_tokens": 4096},
    {"op": "patch_page",       "provider": "anthropic", "model": "claude-sonnet-5",  "temperature": 1.0, "max_tokens": 4096},
    {"op": "answer_query",     "provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 1.0, "max_tokens": 2048},
]
```

Note on field naming: the original request spelled the token cap `max_token`; both files above use
`max_tokens` to match every existing spelling in this codebase (`Settings.llm_max_tokens`,
`complete(max_tokens=...)`, `CostRecord.output_tokens`). Flagging the deliberate deviation rather than
silently picking one.

**Loading.** `config/providers.py` and `config/ops.py` are loaded by path (`importlib.util
.spec_from_file_location`), not imported as a package — they are not on `sys.path` and have no
`__init__.py`. Default paths are `./config/providers.py` and `./config/ops.py`, resolved relative to
the current working directory, exactly like `.env` already is. Two new env vars override the paths
(§19.9) for deployments that keep `config/` elsewhere.

**Resolution, at startup, in `factory.py`:**

1. Absent-file check: if `config/providers.py` does not exist, **skip all of §19 entirely** — build
   today's single-provider client from `Settings`, unchanged (§19.3 "Fallback mode"). If exactly one
   of the two files exists, that is a broken half-migration — fail loudly naming which file is
   missing.
2. Otherwise, for each `PROVIDERS` row, resolve `api_key`/`base_url` by reading the named env vars
   (`fake` needs neither) — **real `os.environ` first, `.env` as a fallback** (`dotenv_values()`,
   never `os.environ` mutation), matching `Settings`' own precedence. Fixed 2026-09-06 (HISTORY.md):
   the first implementation read only real `os.environ`, which cannot see a variable that exists
   solely in `.env` — the documented, intended way to hold one — since nothing in this codebase calls
   `load_dotenv()`. A row whose `api_key_env` resolves empty either way is **not an error** — it is
   dropped from the *active* provider set. This is the literal answer to "undefined PROVIDER should
   not be in the provider_list."
3. Validate `OPS`: every op name the codebase actually calls (`answer_query`, `summarize_source`,
   `plan_compile`, `create_page`, `patch_page` — this list itself is asserted against the live call
   sites by a test, §19.8) must appear exactly once. A missing op, a duplicate op, or an op naming a
   provider absent from the active set is a startup `RuntimeError` naming the op and the reason —
   same posture as `Settings.require`.
4. Build one concrete adapter per **distinct active provider actually referenced by at least one
   `OPS` row** (lazy — an active-but-unused provider builds nothing), reusing today's per-provider
   construction logic in `factory._build_llm_client` (native `AnthropicLLM` for `anthropic`,
   `LangChainLLM` for everything in `llm.providers.REGISTRY`, `FakeLLM` for `fake`).
5. Wrap the per-provider adapters and the `OPS` table in a new `RoutingLLMClient`.

### 19.3 `RoutingLLMClient` and the call-site simplification

New class, `src/llmwiki/llm/router.py`, implementing the existing `LLMClient` protocol unchanged in
shape:

```python
class RoutingLLMClient:
    def complete(self, *, op: str, system: str, prompt: str, schema: dict | None = None,
                 model: str | None = None, max_tokens: int | None = None,
                 temperature: float | None = None) -> LLMResponse:
        row = self._ops[op]                      # KeyError here is a bug, not a config error —
                                                   # __init__ already validated every call-site op exists
        client = self._clients[row["provider"]]
        return client.complete(op=op, system=system, prompt=prompt, schema=schema,
                                model=model or row["model"],
                                max_tokens=max_tokens or row["max_tokens"],
                                temperature=row["temperature"] if temperature is None else temperature)
```

**Call sites simplify.** `agent/query.py` and `wiki/compiler.py`'s five `llm.complete(...)` calls drop
`model=`, `max_tokens=`, `temperature=` — they pass only `op=`, `system=`, `prompt=`, and (where used)
`schema=`. This is the direct implementation of "config fully owns it."

**Fallback mode (`config/providers.py` absent) must not regress.** Today, `max_tokens=`/`temperature=`
are supplied by every call site from `Settings.llm_max_tokens`/`llm_temperature`. If call sites stop
passing them, something must still apply those settings, or a deployment's `LLM_MAX_TOKENS=4000` in
`.env` would silently stop mattering. Fix: `LLMClient.complete()`'s `model`/`max_tokens`/`temperature`
become `Optional`, defaulting to `None` (protocol change, `llm/base.py`) — a caller passing nothing
gets the adapter's own construction-time defaults, not a hardcoded `2048`/`1.0`. `AnthropicLLM`,
`LangChainLLM`, and `FakeLLM` already take `default_model`; each gains `default_max_tokens` /
`default_temperature` alongside it, and `factory._build_llm_client`'s existing (unrouted) branch
passes `cfg.llm_max_tokens` / `cfg.llm_temperature` there, same as it passes `cfg.llm_model` today.
This is the one change in this section that touches the *fallback* path — everything else in §19 is
additive and inert until `config/providers.py` exists.

**`COMPILE_EXECUTOR_MODEL` is removed**, in both modes: `Settings.compile_executor_model`, the
`.env.example` line, and the constructor argument in `factory._build_llm_client` all go. A deployment
that wants `create_page`/`patch_page` on a stronger model must now define `config/ops.py` — there is
no equivalent knob left in the zero-config fallback path. This is called out because it is a real,
user-visible capability change for anyone currently setting that variable, not an internal refactor.

### 19.4 SKILL.md-format prompts

`chains/prompts/*.md` gain YAML frontmatter:

```markdown
---
name: answer-query
description: Answer a research question from retrieved wiki pages and raw chunks, with citations.
---

You are answering a research question from a compiled knowledge base.
...
```

`load_prompt()` (`chains/prompts_loader.py`) parses frontmatter with `python-frontmatter` (already a
hard dependency, used today for wiki pages) and returns the body only — every existing caller
(`load_prompt("answer_query")` etc.) is unaffected; the `@cache` decorator and the file-not-found
error message are unchanged. The four compiler-stage prompts are otherwise untouched: same file, same
fixed op→prompt call, same tests.

### 19.5 Query-agent skill invocation

Scoped to `agent/query.py` only, per the confirmed answer. `QueryAgent.answer()` currently always
calls `load_prompt("answer_query")` as a fixed system prompt. It instead:

1. Discovers the skill set — the frontmatter (`name`, `description`) of every file under
   `chains/prompts/` (or a dedicated `chains/skills/` directory if the compiler-stage prompts and the
   query-agent's skills should not share one directory — **open item, §19.10**).
2. Offers them to the model as tool choices on the `answer_query` call (or a preceding call), letting
   the model pick — and, across turns, chain — among them, rather than always resolving to the same
   fixed prompt body.
3. **Keeps every existing guarantee.** `WIKI_CONFIDENCE`/wiki-first-with-RAG-fallback retrieval,
   `_build_context`'s citation building, and — critically — the load-bearing
   `test_agent.py::test_every_citation_resolves_to_a_real_raw_object` all sit **above** this change in
   `answer()` and are unaffected by which skill produced the final text: `source_exists()` still
   filters every citation before it reaches the caller, regardless of skill path taken.

This is new agentic surface in a codebase that has had none until now (the compiler is deliberately
non-agentic; the query agent has been a fixed retrieve-then-single-call shape). Treated accordingly:
own subsection, own tests (§19.8), and explicitly not extended to the compiler (§4.8.2's boundary).

### 19.6 Milestones

Independent of N0–N8's numbering (§19.1) — labeled `R` for "routing" to avoid implying an ordering
dependency on N-anything:

| # | Milestone | Size | Exit criteria |
|---|---|---|---|
| # | Milestone | Size | Exit criteria | Status |
|---|---|---|---|---|
| **R1** | `config/` loader + validation | `[M]` | `config/providers.py`/`config/ops.py` absent → byte-identical behaviour to pre-R1 (existing suite green, unmodified). Present → active-set resolution, missing/duplicate/inactive-provider op validation, all with named `RuntimeError`s, each covered by a test | **Landed 2026-09-05** |
| **R2** | `RoutingLLMClient` + protocol `Optional` change | `[M]` | `llm/base.py` signature updated; `AnthropicLLM`/`LangChainLLM`/`FakeLLM` gain `default_max_tokens`/`default_temperature`; fallback-mode output is unchanged for a fixed `Settings` (regression test, §19.8) | **Landed 2026-09-05** |
| **R3** | Call-site simplification + `COMPILE_EXECUTOR_MODEL` removal | `[S]` | `query.py`/`compiler.py`'s five calls drop `model=`/`max_tokens=`/`temperature=`; `Settings.compile_executor_model` and its `.env.example` line removed; `.env.example` gains the `config/` explanation | **Landed 2026-09-05** |
| **R4** | SKILL.md frontmatter + `load_prompt` parsing | `[S]` | All 5 prompt files get frontmatter; `load_prompt()` returns body only; compiler call sites and their tests are unchanged | **Landed 2026-09-07** |
| **R5** | Query-agent skill invocation | `[L]` | `QueryAgent.answer()` discovers and can select among skills; citation-resolution contract test still passes unmodified; new tests per §19.8 | **Landed 2026-09-07** |

R1–R3 landed together 2026-09-05 (§13's "must pass all existing tests" rule held: the full suite passed
unmodified, plus new tests). R4 was originally planned to land alongside R1–R3 but was explicitly held
back to land with R5 instead, keeping `chains/prompts/*.md` untouched until the query-agent skill work
is actually ready to consume real frontmatter. R4 and R5 landed together 2026-09-07, with three
deviations from the letter of §19.4/§19.5 worth recording (full detail in `HISTORY.md`):

1. **Skill selection and per-step generation reuse the `answer_query` op**, not a new op. §19.5 item 2
   explicitly allows either "the `answer_query` call (or a preceding call)"; reusing it keeps `KNOWN_OPS`,
   `config/ops.py` and the AST drift guard (§19.2 step 3) untouched, at the cost of every skill-related
   call sharing one cost-ledger op label rather than each getting its own.
2. **A real `skills/` directory ships**, populated with two skills (`answer-query`, `compare-concepts`),
   rather than landing inert. §19.9 item 1's directory decision is a location, not a promise to leave it
   empty; per §4.8.2, R5 is explicitly a real behaviour change to the query agent (the same posture R3
   already took removing `COMPILE_EXECUTOR_MODEL`), so shipping a populated default is what makes that
   true rather than aspirational.
3. **`tests/conftest.py` gained `_isolate_agent_skills_dir`**, mirroring R1's `_isolate_llm_routing_config`,
   and `test_config.py::test_agent_skills_dir_defaults_outside_src` was updated to bypass it — the same
   pattern §19.7.2 already used for the routing-config default test, now needed because item 2 above
   makes `./skills` a real, populated directory in this checkout.

### 19.7 Testing plan

**19.7.1 Must pass all existing tests.** Same standing rule as §13.1. R1 and R2 are explicitly
designed so the existing 207 unit tests need **no assertion changes** — `config/` absent and the
`Optional` defaults resolving to today's literal values are what make that true. R3's removal of
`compile_executor_model` is the one place an existing test changes on purpose (below).

**19.7.2 Obsolete tests announced for removal / change.** *(Updated to the actual outcome, landed
2026-09-05 — two rows below were not anticipated when this section was first drafted; grepping for
`model=`/`max_tokens=` at call sites missed them because they assert on `.calls[...]["max_tokens"]`
in the double, not on call-site syntax. Recorded here rather than silently fixed, since undercounting a
plan's own test impact is itself worth a paper trail.)*

| Test | Change | Reason |
|---|---|---|
| `tests/unit/test_agent.py::test_answer_passes_settings_max_tokens_and_temperature` | **Rewritten** → `test_answer_leaves_model_and_sampling_params_to_the_configured_adapter`: asserts the recorded call's `model`/`max_tokens`/`temperature` are all `None`, instead of equal to a `Settings.model_copy`-injected value | Its premise — the agent reads `Settings.llm_max_tokens`/`llm_temperature` and forwards them itself — is exactly what "config fully owns it" (R3) removed |
| `tests/unit/test_compiler_behaviour.py::test_llm_max_tokens_and_temperature_reach_every_stage` | **Rewritten** → `test_compiler_leaves_model_and_sampling_params_to_the_configured_adapter`: same change, across all four compiler-stage calls; doubles as the `COMPILE_EXECUTOR_MODEL` regression check (`create_page`/`patch_page` pass no `model=` either) | Same reason, plus §19.3's `COMPILE_EXECUTOR_MODEL` removal |
| `llm/fake.py::FakeLLM.complete` and `tests/doubles.py::ScriptedLLM.complete` | **Updated**: record `max_tokens`/`temperature` exactly as passed (`None` included) rather than pre-resolving to `2048`/`1.0` before appending to `.calls` | Needed for the two rewrites above to actually observe "nothing was passed" — a double that silently fills in a default would hide the very regression these tests exist to catch |
| `tests/unit/test_config.py` — a test asserting `Settings.compile_executor_model`'s default | *(planned, not needed)* | No such test existed — the field had zero test coverage, so its removal needed no deletion |
| `tests/unit/test_config.py::test_agent_skills_dir_defaults_outside_src` | **Rewritten** (R5, 2026-09-07): now `monkeypatch.delenv("AGENT_SKILLS_DIR")` before building `Settings`, and its docstring no longer says "not consumed by any code path yet" | R5's populated `skills/` directory means `conftest.py`'s new `_isolate_agent_skills_dir` autouse fixture now sets that env var for every other test in the suite; this one test verifies the true default and must bypass it, exactly as `test_routing_config_paths_default_under_a_config_directory` already does for `LLMWIKI_PROVIDERS_CONFIG`/`LLMWIKI_OPS_CONFIG` |

**19.7.3 New tests announced for new code paths.** *(Updated to the actual filenames landed
2026-09-05.)*

| Milestone | New test | Covers |
|---|---|---|
| R1 | `tests/unit/test_routing_config.py` | Absent `config/` → fallback (`None`); half-present config fails loudly; a full valid config resolves every known op; op-row defaults for `temperature`/`max_tokens`; active-set resolution drops a provider with an unset env var; credentials resolved from the named env var; missing op / duplicate op / op naming an inactive provider / unknown provider kind / a malformed `PROVIDERS`/`OPS` module each raise a named `RuntimeError`; the `KNOWN_OPS`-vs-real-call-sites AST drift guard |
| R2 | `tests/unit/test_anthropic_client.py`, `tests/unit/test_langchain_client.py` (extended, not a new file) | `complete()` with `model`/`max_tokens`/`temperature` omitted resolves to the adapter's construction-time default (`default_max_tokens`/`default_temperature`), not a hardcoded value; an explicit call-site value still overrides it |
| R2 | `tests/unit/test_router.py` | `RoutingLLMClient` dispatches each op to the right provider adapter with the right model/temperature/max_tokens; an explicit call-site override wins; `schema` is forwarded |
| R3 | (see §19.7.2 rewrites above) | |
| R3 | `tests/unit/test_factory.py` (extended) | Routed mode builds a working `RoutingLLMClient` end to end; a provider named in `config/providers.py` but referenced by no `OPS` row is never constructed; absent routing config leaves the fallback path (a plain `FakeLLM`, not a `RoutingLLMClient`) untouched |
| R3 | `tests/unit/test_config.py` (extended) | The two new config paths default outside `src/llmwiki` and are absent in this repository; `agent_skills_dir`'s default; `compile_executor_model` no longer exists |
| R4 | `test_prompts_loader.py` (new file, landed 2026-09-07) | Frontmatter is parsed and stripped; a prompt file with no frontmatter still loads (back-compat during migration); `name`/`description` round-trip for external SKILL.md consumption; a missing prompt still names the file |
| R5 | `test_agent_skill_invocation.py` (new file, landed 2026-09-07) | `discover_skills()`: finds every frontmatter'd file, empty for an absent directory, skips an unnamed or duplicate-named file; `QueryAgent.answer()`: no discoverable skills is byte-identical to the pre-R5 fixed-prompt call, the model's skill choice is honored, a chained multi-skill turn carries the previous step's output forward, an invalid/hallucinated choice retries once then falls back to the fixed `answer_query` skill (§19.9 item 2), and `test_every_citation_resolves_to_a_real_raw_object`'s assertion re-run specifically against a skill-invoked answer |

**19.7.4 Documentation of added/removed tests.** Each of R1–R5 gets a `HISTORY.md` entry (goal, root
cause where applicable, implementation detail, related files, test coverage — per `CLAUDE.md`), and
the removed/rewritten rows in §19.7.2 are named there explicitly, same obligation as §13.4 imposes on
N0–N8.

### 19.8 Environment variables & config injection

| Variable | Default | Read by | Notes |
|---|---|---|---|
| `LLMWIKI_PROVIDERS_CONFIG` | `./config/providers.py` | `Settings.llm_providers_config`, consulted by `factory.py` | Path to the providers manifest; absence of the *file* (not the env var) is what triggers fallback mode |
| `LLMWIKI_OPS_CONFIG` | `./config/ops.py` | `Settings.llm_ops_config`, consulted by `factory.py` | Path to the ops table |
| `AGENT_SKILLS_DIR` | `./skills` | `Settings.agent_skills_dir` | Query-agent skill directory (§19.9 item 1) — outside `src/llmwiki`, like the two paths above. Added in R1–R3; not read by any code path until R4/R5 |
| *(per-provider, named by `config/providers.py`, not fixed here)* | — | resolved by `llm/routing_config.py`, per provider row | e.g. `OPENAI_API_KEY`, `OPENAI_BASE_URL` — whatever `api_key_env`/`base_url_env` name. `.env.example` must list one per provider actually present in the committed default `config/providers.py`, same sync obligation `CLAUDE.md` already imposes |
| `COMPILE_EXECUTOR_MODEL` | — | *(removed, R3)* | No replacement variable — its role moves entirely into `config/ops.py` |

`config/providers.py` and `config/ops.py` are themselves not env-driven — they are checked-in code,
read once at the same startup point `factory.py` already reads `Settings`. They do not add a second
`os.environ` reader to this repository; they add a second *file* `factory.py` consults, which then
reads named env vars on `config/providers.py`'s behalf. In the implementation, that reading happens in
`llm/routing_config.py` (invoked from `factory.py`, never at import time) rather than in `factory.py`
itself — `os.environ` access in this application-specific slice is not literally confined to one
module the way §10.1's guard 3 requires of the (separate, not-yet-extracted) shareable packages; it is
confined to `config.py` (the four/five `LLM_*` fallback names) plus `llm/routing_config.py` (whatever
names `config/providers.py` itself declares), which is the closest equivalent available given that the
set of env vars to read is not knowable until `config/providers.py` is read.

### 19.9 Open items

1. **Skill directory — resolved.** The query agent's discoverable skills do **not** share
   `chains/prompts/` with the compiler's fixed prompts, and do **not** move to a `chains/skills/`
   under `src/llmwiki` either: they live in a repo-root `skills/` directory, outside `src/llmwiki`
   entirely — the same posture as `config/` (§19.2) — configurable via a new `AGENT_SKILLS_DIR`
   env var (`Settings.agent_skills_dir`, default `./skills`). The setting was added in R1–R3 (it is
   inert, read by no code path yet) so the location is locked in before R4 writes any SKILL.md
   frontmatter, rather than improvised at R5. `chains/prompts/*.md` remains exactly what it is today:
   the compiler's four fixed, non-agentic prompts.
2. **Skill-selection failure mode.** If the model's tool choice names something not in the discovered
   skill set (malformed tool call, hallucinated name), R5 needs a defined fallback — most likely
   "retry once, then fall back to the fixed `answer_query` skill" — rather than a hard failure on a
   user-facing query.
3. **Should `RoutingLLMClient` participate in LangSmith tracing (§7.6) per sub-adapter, or only at the
   router level?** Affects whether a routed call shows as one span or two in a trace. Not exit-criteria
   blocking for R1–R5; worth deciding before this sees real traffic.

---

## 20. Phase 1-D: LangGraph Query Graph + LangSmith Eval (new, v1.2)

> Design decision: design v1.4 §4.9 (v1.6). This section is the implementation record: module layout,
> the graph specification, the ops and env vars, the eval and correction loops, the milestones and the
> tests. Landed 2026-09-16 in one change set; `HISTORY.md` has the entry.

### 20.1 Scope and relationship to §1–§19

The fourth and last Phase 1 workstream (KB design §5; A/B capture channels landed 2026-09-11, C
local-LLM routing is wired but not flipped). Like §19 it is a scoped *behaviour* change, not
packaging: nothing moves under `packages/`, and the N0–N8 milestones are untouched. It consumes
`LLMClient` (P5 rule 3 holds — `complete()` gains no LangChain type) and §19's routing table (two new
op rows). What it does **not** do: migrate the compiler (still non-agentic, §4.4), add the N7
`get_llm()` surface, or write query-side cost into `wiki/_meta/cost.jsonl` (pre-existing gap, now
visible per run in LangSmith instead).

### 20.2 Locked decisions

| # | Decision | Consequence |
|---|---|---|
| D1 | LangGraph is orchestration only; every model call is `LLMClient.complete()` | routing, cost, caching, `FakeLLM`/`ScriptedLLM`, `test_layering.py` unchanged; N7 stays deferred |
| D2 | The tool decision is one forced-schema call (`op="agent_step"`) whose schema is derived from real `langchain_core` tools; the transcript is real `AIMessage(tool_calls)`/`ToolMessage` | canonical ReAct trace; the same tools bind natively once `get_llm()` exists |
| D3 | Wiki-first is the first node, verbatim from Phase 0 | `test_wiki_is_searched_before_the_chunk_index` and `test_chunk_index_is_untouched_when_the_wiki_answers` pass unchanged |
| D4 | Three code-enforced bounds: `AGENT_MAX_TOOL_CALLS` (0 = Phase 0 exactly), one shared context budget, `recursion_limit = 2n + 8` | a question's cost is configuration, not model appetite — load-bearing test in §20.9 |
| D5 | Citations are a property of what was retrieved; the last node filters against `raw/` | `test_every_citation_resolves_to_a_real_raw_object` unchanged |
| D6 | Skills keep their §19.5 role: tools gather, skills write | `select_skills`/`generate` reuse `_select_skills`/`_run_skill_chain` |
| D7 | Two new routable ops: `agent_step`, `judge_answer` | `KNOWN_OPS` = 7; `config/ops.py` rows; the AST drift guard scans `agent/graph.py`, `agent/judge.py` |
| D8 | `langgraph` and `langchain-tavily` are core dependencies (2026-09-09 posture); `langsmith` stays function-locally imported | `import llmwiki.eval` never imports `langsmith` (test) |
| D9 | One LangSmith root run per answer, one child per node, one LLM run per call named by op; the router adds no span | closes §19.9 item 3; `LangChainLLM` passes `run_name=op` |
| D10 | The golden set is a repo JSONL; LangSmith holds a pushed copy; the shipped sample runs offline | `scripts/eval_answer.py --offline` can gate a commit |
| D11 | External search is a fourth tool in the same loop, offered by code policy, results never citations | `Answer.external_refs`; contract test unchanged |
| D12 | The correction loop has a defined place for each failure cause; nothing self-rewrites | `Answer.run_id`, `POST /feedback`, `--export-failures`, `--promote-feedback` |

### 20.3 Module layout

```
L0 models/plan.py     AgentStep, ExternalRef, Verdict; Answer += steps, context, external_refs, run_id
L1 llm/               KNOWN_OPS += agent_step, judge_answer; FakeLLM synthesises both; LangChainLLM names runs
L1 websearch/  (new)  base.py WebSearcher · tavily.py (langchain-tavily, imported only here) · fake.py
L2 agent/query.py     QueryAgent — public surface unchanged; owns the graph (cached) and the helpers nodes reuse
L2 agent/graph.py     QueryState, build_query_graph(agent), recursion_limit, STEP_ATTEMPTS
L2 agent/toolkit.py   ToolResult, build_tools, offered_tools (the policy gate), action_schema, dispatch
L2 agent/judge.py     Judge — the eval-only groundedness grader
L2 chains/prompts/    agent_step.md, judge_answer.md (SKILL.md format, like the five before)
L4 tools.py           + judge_answer(), record_feedback(); health() reports routes + query_graph bounds
L4 eval/       (new)  dataset.py · evaluators.py · run.py · feedback.py — imports tools/models/config only
L5 api/routes.py      /answer returns the richer Answer; + POST /feedback (bearer)
L5 cli.py             `ask` prints tools/external/run_id; + `feedback <run_id> --score --correction`
scripts/              eval_answer.py; docker-compose `eval` service (ops profile)
config/ops.py         + agent_step (cheapest, temp 0.2), judge_answer (temp 0.0)
tests/fixtures/eval/  answer_quality.jsonl — over the offline fixture docs
```

`test_layering.py` gained `websearch` (L1, same banned set as `vector`) and `eval` (L4 peer of `cli`,
may import only `tools`/`models`/`config`), and both names in every other layer's banned set.

### 20.4 Graph specification

```
START → retrieve ──(context empty)──► no_answer → END
           │
           ├──(AGENT_MAX_TOOL_CALLS == 0)──► select_skills
           ▼
         agent ◄──────── tools           agent: 1 × complete(op="agent_step", schema=ACTION)
           │                 ▲           tools: dispatch AIMessage.tool_calls[0] → ToolMessage;
           ├──(tool call)────┘                  merge context_block + citations + external_refs
           └──(answer | cap | budget | invalid ×2)──► select_skills → generate → resolve_citations → END
```

**State** (`QueryState`, `TypedDict`, deltas): `query, k, wiki_hits, chunk_hits, used_rag_fallback,
context, citations{source_id→Citation}, budget_left, messages (reducer `add_messages`),
tool_calls_made, web_calls, seen_calls, notes, steps, external_refs, stop_reason, skills,
chosen_skills, text, answer`.

**`ACTION` schema** — built per pass from the *offered* tools:
`{"action": enum[offered names + "answer"], "args": {union of the offered tools' arg schemas},
"reason": str}`. The prompt (`chains/prompts/agent_step.md` + `_render_step_prompt`) shows the
question, the evidence so far with the budget left, the compact call log (`tool(args) → n chars`),
"tool notes" for results that added no evidence, and the offered tools with their argument names.

**Tools** (`toolkit.build_tools`):

| Tool | Args | Registers |
|---|---|---|
| `search_wiki` | `query`, `k=5` | page blocks + page-source citations (`_build_context(hits, [])`) |
| `search_chunks` | `query`, `k=5` | chunk blocks + `source_id`/`url` citations (`_build_context([], hits)`) |
| `get_page` | `slug` | one page body + its citations; unknown slug → observation |
| `search_web` | `query` | `external_refs` only — no context block, no citations; built only when a `WebSearcher` exists |

**Bounds and failure modes, all in code:** cap and budget checked *before* the decision call (no LLM
call is made once either is hit); a tool result is truncated to `budget_left`; an identical repeated
call is refused (observation) but counted; bad arguments or a tool exception become an observation
(`dispatch` never raises); an unusable decision (prose instead of the tool call — seen with a small
reasoning model on a long prompt) is retried once with a nudge (`STEP_ATTEMPTS = 2`), then the loop
ends; `recursion_limit(n) = 2n + 8` (a full run is `2n + 5` node executions).

**`Answer.run_id`**: minted with `uuid4()` and passed as `RunnableConfig.run_id`, so with tracing on
it *is* the LangSmith root run — not read back from a collector (whose `traced_runs` are in completion
order and gave the first leaf, as the 2026-09-16 verification found). `None` when tracing is off.

### 20.5 Ops, prompts and environment

| Op | Route (shipped `config/ops.py`) | Notes |
|---|---|---|
| `agent_step` | `openrouter` / `z-ai/glm-5.3-flash`, temp 0.2, 2048 tok | cheapest model; reasoning tokens count against the cap — 512 truncated the tool call |
| `judge_answer` | same, temp 0.0, 1024 tok | eval only |
| `answer_query` | unchanged route, **8192 tok** | at 2048 a reasoning model spent the budget thinking and returned an empty answer once tool results grew the context |

| Variable | Default | Read by |
|---|---|---|
| `AGENT_MAX_TOOL_CALLS` | `4` | `Settings.agent_max_tool_calls` — `0` disables the loop |
| `AGENT_WEB_SEARCH_POLICY` | `off` | `off` / `weak` / `always` — applied in `toolkit.offered_tools` |
| `AGENT_MAX_WEB_SEARCHES` | `1` | per-question cap |
| `WEB_SEARCH_BACKEND` | `none` | `none` / `tavily` / `fake` — `factory.web_searcher` |
| `TAVILY_API_KEY` | empty | `websearch/tavily.py`, via the factory |
| `LANGSMITH_EVAL_DATASET` | `llmwiki-answer-quality` | `scripts/eval_answer.py --push/--langsmith` |

`tests/conftest.py` pins `AGENT_MAX_TOOL_CALLS=0` for the whole suite (third instance of the
isolation-fixture pattern); loop tests opt in per test.

### 20.6 Evaluation

- **Example**: `{"question", "expected_sources": [id], "must_mention": [term], "notes"}`; LangSmith
  `inputs={"question"}`, `outputs={"expected_sources","must_mention"}`.
- **Target**: `eval.run.answer_target` → `tools.answer` flattened (`text`, `citations` as ids,
  `used_rag_fallback`, `context`, `steps`, `external_refs`, `run_id`).
- **Evaluators** (`eval/evaluators.py`, `(inputs, outputs, reference_outputs)`): `citations_resolve`
  (gated 1.0), `expected_source_cited` (gated 1.0), `must_mention` (gated 1.0), `tool_calls` (metric),
  `judge_grounded` (opt-in; `tools.judge_answer` → `Judge.grade`).
- **Runners**: `run_local` (no LangSmith; rows + failures + means) and `run_experiment`
  (`langsmith.evaluate`, metadata = version, git sha, bounds, `route_*` from `/healthz`).
- **Script**: `scripts/eval_answer.py` — `--offline`, `--judge`, `--push`, `--langsmith`,
  `--experiment-prefix`, `--dataset`, `--export-failures`, `--promote-feedback`; exit 1 on a gated
  failure in a local run. Compose: `docker compose --profile ops run --rm eval …`.

### 20.7 Correction loop

`tools.record_feedback(run_id, score, correction)` → `langsmith.Client.create_feedback(key=
"correctness")`; raises by name when tracing is off (REST maps it to 409). `eval/feedback.py:
corrected_examples` lists root `answer_query` runs in `LANGSMITH_PROJECT`, reads their `correctness`
feedback, and turns each commented one into an `Example` (source ids in the comment that exist under
`raw/` → `expected_sources`; a `must mention: a, b` line → `must_mention`). Verified live 2026-09-16:
ask → `run_id` → feedback → `--promote-feedback` produced a valid example.

### 20.8 Milestones (all landed 2026-09-16)

| # | Milestone | Exit |
|---|---|---|
| G1 | deps, models, config, ops rows, prompts, `FakeLLM`, `KNOWN_OPS` | suite green, no behaviour change |
| G2 | `toolkit.py`, `graph.py`, `query.py` over the graph | `test_agent_graph.py` incl. the bound test; `max=0` parity test |
| G2b | `websearch/`, `search_web`, policy gate, `external_refs` | `test_websearch.py`, toolkit policy tests |
| G3 | `judge.py`, `eval/`, script, fixture set, compose service | `eval_answer.py --offline` exit 0 |
| G4 | run naming, `Answer.run_id`, live trace check | D9 verified in LangSmith (trace tree in `HISTORY.md`) |
| G5 | `record_feedback`, `POST /feedback`, `llmwiki feedback`, `--export-failures`, `--promote-feedback` | routes/eval tests; live loop verified |
| G6 | docs (§18 obligations below), `.env.example`, `CLAUDE.md`, `HISTORY.md` | this section |

### 20.9 Testing plan

**No regressions.** The four load-bearing tests are untouched; `test_layering.py` gained rows only;
`test_routing_config.py`'s drift guard scans two more files. 444 passed after the change (362 before).

**Obsolete tests removed:** none. Two existing tests changed shape without weakening:
`test_langchain_client.ScriptedChatModel.invoke` accepts `config` (the real `Runnable` signature);
`test_config.py::test_configure_langsmith_exports_the_env_vars` cleans up with `os.environ.pop` —
its `monkeypatch.delenv` in `finally` had been *restoring* `LANGSMITH_TRACING=true` at teardown and
leaking it into every later test, harmless until the graph honoured it.

**New tests:** `test_agent_graph.py` (19; **`test_tool_loop_is_bounded_by_agent_max_tool_calls` is
load-bearing**, added to `CLAUDE.md`), `test_agent_toolkit.py` (14), `test_websearch.py` (6),
`test_judge.py` (4), `test_eval.py` (15), `test_fake_llm.py` (3), `test_routes.py` (+6: richer
`/answer`, `/healthz` bounds, `/feedback` auth/409/422/forwarding), `test_config.py` (+2),
`test_langchain_client.py` (+1 run naming), `test_pages_and_gists.py` (+2, §20.10),
`tests/integration/test_langsmith_eval.py` (needs only `LANGSMITH_API_KEY`).

### 20.10 Found along the way

Two pages in the real corpus had unparseable front matter: `render_page` wrote `title:`/`gist:` as
bare YAML scalars, and a model-written `Pi Agent vs OpenCode: Same Model` or `Stub page: no source…`
is not YAML. Every consumer then failed on read, which took every answer down. Fixed at both ends:
`render_page` JSON-quotes the two free-text fields; `read_page` treats an unreadable page as absent
with a warning (`lint` already reports it as an `orphan` finding; the next compile rewrites it).

### 20.11 Documentation obligations discharged

Design v1.4 → 1.6 (§4.9, §4.2, §5, §7 q10); this plan → 1.2 (§20, §14 rows); technical document
(§2.1, §2.2, §2.4, §3.3, §3.5, §5.9–§5.11, §6, §8, §9.2, new §10, Known Gap → §11);
`phase1-testing-guide.md` (§1 D, new §5); `CLAUDE.md`; `HISTORY.md`; `README.md`;
`scripts/README.md`; `.env.example`.

### 20.12 Resolved open items

- §19.9 item 3 (router tracing granularity) → D9: node spans + one LLM span per call, router adds none.
- §17 item 4 (LangSmith hard or soft) → D8: soft, function-local imports; always present transitively.

---

## 21. Viewing the R2 Wiki in Obsidian (new, v1.3)

### 21.1 Why a section is needed

Karpathy's original LLM-wiki pattern keeps `wiki/` on a local disk, so Obsidian opens it directly
and its graph view shows the whole KB. Here `raw/`, `status/` and `wiki/` live in Cloudflare R2
(`STORAGE_BACKEND=r2`), and Obsidian only reads a local folder. Nothing about the wiki itself is
in the way: pages use plain `[[slug]]` wikilinks (`wiki/gists.py`, `wiki/pages.py`,
`wiki/compiler.py`), and the R2 key layout — `wiki/index.md`, `wiki/concepts/`, `wiki/entities/`,
`wiki/sources/` — *is* an Obsidian vault layout (plan-1.1 §5.2, §6.7). The only gap is transport,
and it is closed with a one-directional mirror of the `wiki/` prefix. The local backend needs none
of this: `.data/wiki/` already opens as a vault (plan-1.1 §6.7).

`scripts/sync_wiki.py` (2026-09-22) does §21.2 and §21.3 in one command — it creates the remote
from `.env` (or `--env-file .env.prod`, one `--remote` name per environment) when missing, refuses
an existing remote whose stored key id/endpoint disagree with the env file, and runs the mirror.
Its default mirrors the **whole bucket** (`raw/`, `status/`, `wiki/` side by side — see the
2026-09-22 note in §21.3); `--wiki-only` is §21.3's original `wiki/`-only form.

### 21.2 One-time rclone remote

R2 is S3-compatible, so `rclone` (or `aws s3 sync`, plan-1.1 §6.8) talks to it. `rclone` is
preferred: it is incremental, handles `--exclude`, and shows progress. The remote is a *name* in
rclone's config, not a URL scheme — `rclone sync r2://llmwiki ...` fails with
`didn't find section in config file ("r2")`; the form is `r2:<bucket>`.

Create the remote once, from the same four `R2_*` values `.env` already holds (plan-1.1 §6.2;
`.env.example` §"Cloudflare"):

```bash
# pull R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET / R2_ENDPOINT_URL out of .env
set -a; source /path/to/llm-wiki-svc/.env; set +a

rclone config create r2 s3 \
  provider=Cloudflare \
  access_key_id="$R2_ACCESS_KEY_ID" \
  secret_access_key="$R2_SECRET_ACCESS_KEY" \
  endpoint="$R2_ENDPOINT_URL" \
  acl=private \
  no_check_bucket=true

rclone lsd r2:$R2_BUCKET        # expect: raw  status  wiki
```

This writes `~/.config/rclone/rclone.conf` (mode 0600). The interactive `rclone config` → `n` →
`s3` → `Cloudflare` path produces the same file. The R2 token needs only **Object Read** on the
bucket for a read-only mirror; the `R2_ACCESS_KEY_ID` pair the service uses has read+write and works
too, but a dedicated read-only token is the better choice for a desktop machine.

### 21.3 Mirror only `wiki/`, then open it

```bash
rclone sync r2:$R2_BUCKET/wiki ./vault --exclude "_meta/**" -P
```

- **Only the `wiki/` prefix.** `raw/` is the immutable source store (PDFs, transcripts — large,
  and not part of the graph) and `status/` is per-source pipeline state. Neither belongs in a vault.
  *Revised 2026-09-22:* in practice a `wiki/`-only folder did not open as a usable vault — every
  source note points at `` `raw/{id}/` `` (`wiki/compiler.py`), and Karpathy's layout keeps `raw/`
  beside `wiki/` in the one vault so `raw/{id}/extracted.md` opens next to the page that cites it.
  `scripts/sync_wiki.py` therefore mirrors the bucket root by default
  (`rclone sync r2:$R2_BUCKET ./vault --exclude "wiki/_meta/**"`) and keeps this `wiki/`-only form
  as `--wiki-only` for a light view when `raw/` is too big to carry.
- **`--exclude "_meta/**"`** drops `wiki/_meta/gists.json` and `wiki/_meta/cost.jsonl`
  (`storage/layout.py` `GISTS_KEY`, `COST_KEY`). Obsidian ignores non-markdown anyway; excluding
  them just keeps the vault clean and `cost.jsonl` — which grows on every compile — off the mirror.
- **`sync` vs `copy`.** `sync` makes `./vault` an exact mirror: a page deleted or renamed by
  `llmwiki lint --apply` disappears locally too. That is the right semantics for a *view*. If you
  intend to keep your own notes in the same vault, use `rclone copy` instead — it never deletes —
  and keep them outside `concepts/`, `entities/` and `sources/` so a later compile cannot clash.
- Open `./vault` in Obsidian as a vault (File → Open folder as vault). Graph view, backlinks,
  local graph and search work on the full KB; `index.md` is the hierarchical entry point.

Re-run the same command whenever fresh pages are wanted; only changed objects move. For a
hands-off mirror, a cron entry alongside the weekly lint (plan-1.1 §6.8) is enough:

```cron
# Mirror the compiled wiki for Obsidian, every 15 minutes
*/15 * * * * rclone sync r2:llmwiki/wiki ~/vault --exclude "_meta/**" --quiet
```

### 21.4 The one rule: the compiler owns `wiki/`

The mirror is **one-directional, R2 → local**. `wiki/` is written by the incremental compiler and
the scheduled lint (plan-1.1 §8, §6.8); an edit made in Obsidian and pushed back would be
overwritten by the next compile of any source that touches the page, or — worse — would overwrite a
freshly compiled page with a stale one. Never run `rclone sync ./vault r2:$R2_BUCKET/wiki`.

The same applies to Obsidian's **Remotely Save** community plugin, which can sync a vault straight
against an S3-compatible bucket (custom endpoint = `R2_ENDPOINT_URL`) and so gives desktop *and*
mobile Obsidian a view with no CLI step. It is bidirectional by default; if used, point it at a
read-only R2 token so a stray local edit is rejected at the bucket rather than silently accepted.
Human curation of the wiki goes through capture (a note ingested as pure text becomes a source,
technical document §3.1.1) or through a future git-backed `wiki/` (design v1.4 §5, Phase 1+), not
through the mirror.

### 21.5 Alternatives, and what is deliberately not built

| Option | Cost | When it fits |
|---|---|---|
| `rclone sync` + Obsidian (§21.2–§21.3) | zero code | **Default.** Full Obsidian feature set on a desktop |
| Remotely Save plugin, read-only token (§21.4) | zero code, one plugin config | Same vault on a phone / tablet |
| Quartz, Obsidian Publish, Foam, Logseq over the mirrored folder | zero code | A static, shareable site with a graph; or a non-Obsidian editor |
| `GET /graph` + a D3 force-graph page in FastAPI | one endpoint, one static page, one unit test on edge extraction from `[[links]]` (`wiki/lint.py` already parses them) | Graph in a browser with no sync step at all. Not built now; it is the natural answer to plan-1.1 §15 item 5 ("read-through markdown viewer in FastAPI") if the sync step becomes a nuisance |

No env var is added by this section. Its one piece of code is `scripts/sync_wiki.py` (with
`tests/unit/test_sync_wiki_script.py`), which wraps §21.2–§21.3 and reads the `R2_*` values the
service already has; everything else here is operational guidance.

---

*End of Implementation Plan v1.5*
