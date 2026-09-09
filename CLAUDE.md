# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

Phase 0 is **implemented**, plus plan-v1.4 §19's R1–R5 (multi-provider/per-op
LLM routing, SKILL.md-format prompts, query-agent skill invocation). `pytest`
runs 291 unit tests with no network access (a handful skip when the optional
`langsmith` extra is absent, environment-dependent); `scripts/smoke_flow.py
--offline` walks the whole flow end to end with fake adapters. Integration
tests exist but have never run — they need Cloudflare and Anthropic
credentials that do not exist yet.

The LLM layer is multi-provider: `LLM_PROVIDER` selects `anthropic` (native
adapter — prompt caching, measured USD cost), `openai`, `google`, `nvidia`,
`deepseek` or `openrouter` (all via LangChain), or `fake`. As of 2026-09-09
langchain-core and every provider integration are core dependencies (not
extras) — `uv sync` / `pip install llmwiki` installs all of them, and
switching `LLM_PROVIDER` needs no separate install step. See `.env.example`
for the table and `docs/implement-plan-v1.4.md` §7.5.

- `llmwiki-KB-design.md` — architecture and scope. **Authoritative.** Read it
  before making design decisions; bump version + date when major decisions lock.
- `implement-plan.md` — the Phase 0 plan: locked decisions, layer rules, the
  operational runbook (§6), milestones, and the testing plan.
- `HISTORY.md` — every change, including deviations from the plan and why.

### Working in this repository

```bash
uv venv && uv pip install -e ".[dev]"   # rebuild .venv from scratch, any time
source .venv/bin/activate               # python 3.11.14; the system python is 3.10
pytest                                  # unit tests only (integration is opt-in)
pytest -m integration                   # needs a populated .env; costs money
python scripts/smoke_flow.py --offline  # end-to-end, no keys, under 2 seconds
ruff check . && mypy                    # the rest of the pre-commit gate

# Every LangChain provider ships in the base install already - trying a
# non-default provider is just LLM_PROVIDER=openai (etc.) in .env, no install.
```

The interpreter is pinned in `.python-version` (committed), and
`[tool.mypy] python_version` must be kept equal to it. They are not decorative:
on 3.13 the resolver pulls a numpy whose stubs use 3.12-only syntax, and mypy
dies inside them under a `3.11` pin before reaching any project file.
`requires-python` stays `>=3.11` — the *library* supports newer, the *dev env*
is pinned.

Four tests are load-bearing and must not be weakened to make a change pass:

- `tests/unit/test_layering.py` — the L0–L5 import boundaries. If a new import
  fails it, move the code, do not widen the rule.
- `tests/unit/test_compiler_no_full_scan.py` — guards design doc §4.4. Per-ingest
  cost that grows with corpus size is the one failure mode that makes the project
  uneconomic.
- `tests/unit/test_agent.py::test_every_citation_resolves_to_a_real_raw_object` —
  guards the answer-with-citations contract.
- `tests/unit/test_providers.py::test_importing_the_registry_imports_no_provider_sdk` —
  guards lazy loading. Every provider SDK is a core dependency now, but each is
  still imported only inside `build()`; if this fails, a provider SDK has
  acquired a module-level import and merely importing `llmwiki` got heavier
  for everyone.

## What This Project Is

A cost-effective, cloud-hosted, multimodal research **knowledge base** inspired by Karpathy's
"LLM Wiki" pattern. Two things compound over time and must stay conceptually distinct:

- **`raw/`** — immutable original sources (PDFs, web pages, YouTube transcripts, images) plus
  light metadata. Never mutated after capture.
- **`wiki/`** — LLM-compiled, interlinked markdown pages (concepts, entities, indexes) derived
  from raw sources. Human-readable and Obsidian-friendly.

The system is explicitly **hybrid**: the compiled wiki provides synthesis; a vector store
provides RAG-style retrieval at scale. The agent is **wiki-first with RAG fallback**.

## Core Architecture (target design)

The end-to-end flow is a one-directional pipeline — preserve this separation of concerns when
implementing:

```
Capture → raw/ (object storage) → Ingestion Pipeline → Extract & Normalize
   → Embed + Chunk → Vector Store        (retrieval path)
   → Incremental Wiki Compiler → wiki/   (synthesis path)
Agent reads wiki/ first, falls back to Vector Store, answers with citations.
```

Layers (see design doc §2–§3 for the full diagrams):

- **Capture Layer** — Telegram bot (primary, Phase 0) + webhook / email-to-ingest. Custom app
  is deferred to later phases.
- **Storage Layer** — object storage (S3 / R2 / GCS) for both `raw/` and `wiki/`. Wiki may
  later be git-backed for versioning.
- **Processing Layer** — serverless/queue-driven ingestion; extractors per modality; embedding
  + chunking; **incremental** wiki compiler.
- **Retrieval & Agent Layer** — vector store + tool-using agent; cheap cloud models by default,
  optional local LLM (Ollama/vLLM) for bulk work in later phases.
- **Interface Layer** — see the Hybrid interface decision below.

## Two Architectural Decisions That Drive Everything

1. **Interface = Hybrid (design doc §4.6).** Build a clean **Python core package** as the
   reusable "brain" (storage helpers, extractors, incremental compiler, vector retrieval, wiki
   page management, domain routing). Expose it primarily via **FastAPI** (REST, auth, uploads,
   webhooks, web UI), and add an **MCP server layer on top** (FastMCP; supports `from_fastapi()`
   and mounting MCP inside a FastAPI app so both live in one process). Canonical tool surface:
   `search_wiki`, `get_page`, `ingest_source`, `compile_update`, `list_concepts`, `lint_wiki`.
   Keep business logic in the core package, never in the FastAPI or MCP glue.

2. **Compilation must avoid full-wiki scans (design doc §4.4).** This is the central
   cost-control constraint. The compiler must: use hierarchical indexes + one-line page gists
   (progressive disclosure); semantically retrieve only relevant wiki pages before compiling;
   apply **delta/incremental** page updates (create or patch affected pages only); optionally
   two-phase plan-then-generate (cheap model plans, stronger model executes); and run global
   lint/synthesis on a schedule, **not** on every ingest.

## Phase Awareness

Work is staged (design doc §5). Phase 0 is a single-researcher vertical slice: object storage,
one capture channel, basic extraction, simple vector store, incremental compiler, core package
+ a simple MCP or light FastAPI. Do not build Phase 2/3 features (multi-tenancy, domain
partitioning, reranking, custom mobile app) unless explicitly asked — the design deliberately
defers them. When implementing a feature, confirm which phase it belongs to.

## Cost Philosophy (constrains implementation choices)

LLM token spend is the dominant, variable cost. Every design choice should favor: cheap cloud
models by default (Haiku-class), incremental over full-corpus operations, aggressive caching,
and serverless/event-driven orchestration to keep fixed costs near zero. Prefer pay-as-you-go
object-storage-native vector options.

## Project-Specific Rules (from ~/.claude/CLAUDE.md — apply here)

- **`HISTORY.md`** is mandatory: every code/config/architectural change must be logged there
  (goal, root cause for bugs, implementation detail, related files, test coverage) before the
  change is considered complete. This file does not exist yet — create it with the first change.
- **Every change plan must include a test section** covering: no regressions, obsolete tests
  announced for removal, new tests announced for new code paths, and added/removed tests
  documented in `CLAUDE.md` / `HISTORY.md`.
- Keep `.env.example` in sync with every env var the code reads; never commit `.env`.
- Unit tests must not make real API calls (mock the LLM/vector/storage clients); mark
  integration tests with `@pytest.mark.integration`.
