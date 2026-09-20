# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State

Phase 0 is **implemented**, plus plan-v1.4 §19's R1–R5 (multi-provider/per-op
LLM routing, SKILL.md-format prompts, query-agent skill invocation) and the
five-source-kind ingestion surface (2026-09-08: PDF file, blog URL, YouTube
URL, pure text, text file — reachable from REST, MCP, CLI and Python alike;
see the technical document §3.1.1). Phase 1 (KB design §5) is **in
progress**: Telegram and email capture channels (webhook mode, in-process
with FastAPI — `src/llmwiki/channels/`) landed 2026-09-11; local-LLM routing
(RTX 3090 host) has its own `vllm`/`llamacpp` provider entries as of
2026-09-14 (`config/providers.py`, each with its own `*_API_KEY`/`*_BASE_URL`
pair, distinct from real cloud OpenAI) but is not yet flipped on (see
`config/ops.py`'s commented example — no endpoint is reachable yet); the
LangGraph query graph, external search and LangSmith eval/correction loop
landed 2026-09-16 (workstream D — design v1.4 §4.9, plan §20, technical
document §3.3 and §10). See `HISTORY.md`'s 2026-09-11, 2026-09-14 and
2026-09-16 entries.

The query agent is a bounded LangGraph loop over the existing `LLMClient`
(`src/llmwiki/agent/graph.py`): `AGENT_MAX_TOOL_CALLS` (default 4; `0` = the
pre-graph single call, which is what the unit suite pins via an autouse
fixture), a shared context budget and a recursion limit are all code-enforced.
`search_web` is offered only by `AGENT_WEB_SEARCH_POLICY` and its results are
never citations. Two more ops exist (`agent_step`, `judge_answer`) — a new
`complete(op=...)` call site needs a `config/ops.py` row and the drift guard
in `tests/unit/test_routing_config.py` will say so.

`pytest` runs 459 unit tests with no network access (a handful skip when a
provider extra is absent, environment-dependent); `scripts/smoke_flow.py
--offline` walks the whole flow end to end with fake adapters,
`scripts/eval_answer.py --offline` scores the shipped golden set the same way
and `scripts/probe_query_graph.py --offline --matrix` runs the query graph's
bound/policy matrix against the doubles. The manual, pass/fail plan for
workstreams C and D — with `scripts/check_local_llm.py` (local vLLM/llama.cpp
diagnostic) and `scripts/probe_query_graph.py` (live bounds probe +
LangSmith trace check) — is `docs/phase1-manual-test-plan-C-D.md`
(2026-09-17); `tests/unit/test_phase1_scripts.py` pins both scripts' checks.
Integration tests exist but have never run as a suite — they need Cloudflare
and Anthropic credentials; `tests/integration/test_langsmith_eval.py` needs
only a LangSmith key.

The LLM layer is multi-provider: `LLM_PROVIDER` selects `anthropic` (native
adapter — prompt caching, measured USD cost), `openai`, `vllm`, `llamacpp`,
`google`, `nvidia`, `deepseek` or `openrouter` (all via LangChain), or `fake`.
`vllm`/`llamacpp` are self-hosted, OpenAI-compatible-route servers and reuse
`openai`'s `ChatOpenAI` class under a distinct registry key/env-var pair
(2026-09-14) — see `docs/implement-plan-v1.4.md` §7.4's addendum. As of
2026-09-09
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
python scripts/eval_answer.py --offline # answer-quality golden set, no keys (exit 1 on a gated failure)

# Same loop inside Docker, with this tree bind-mounted so edits need no rebuild
# (README "Dev mode"): the dev image installs llmwiki editable and reloads.
docker compose --profile dev up dev        # http://localhost:8011
docker compose --profile test run --rm pytest   # clean-env suite in that image
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

Five tests are load-bearing and must not be weakened to make a change pass:

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
- `tests/unit/test_agent_graph.py::test_tool_loop_is_bounded_by_agent_max_tool_calls`
  — guards design §4.9's cost bound. A question's LLM spend must be set by
  configuration, never by the model's appetite for another tool call.

Three autouse fixtures in `tests/conftest.py` isolate every test from this
checkout's real routing table, `skills/` catalog and tool loop; a test that
wants one opts in explicitly. The shared `settings` fixture reads the real
`.env`, so anything that reaches `factory.llm_client` must build its own
`Settings(_env_file=None, llm_provider="fake", ...)` (`test_routes.client`,
`test_eval.offline`).

One more is worth knowing when touching the capture path:
`tests/unit/test_tools_and_mcp.py::test_every_transport_can_ingest_all_five_source_kinds`
— a source kind reachable from one transport but not the others is how that
surface drifts. `tests/unit/test_channels.py` covers the Telegram/email
webhook channels (auth, message-shape → `ingest_source(...)` mapping,
optional mount) the same way.

YouTube capture from a cloud IP needs one of `YOUTUBE_PROXY_URL`
(`youtube-transcript-api` through a rotating residential proxy) or
`YOUTUBE_COOKIES_PATH` (yt-dlp captions with a logged-in session; wins when
both are set) - YouTube refuses anonymous transcript requests from cloud
egress IPs. A video with no captions at all falls through to
`YOUTUBE_WHISPER_MODEL` (opt-in `llmwiki[whisper]` extra + ffmpeg, or the image
built with `WITH_WHISPER=1`) - never a block. All three routes store the same
raw segment JSON; see `src/llmwiki/extractors/youtube.py`'s module docstring
and `HISTORY.md`'s 2026-09-20 entries. The Telegram webhook answers 200 before
capturing (a Starlette background task does capture → ack → process) because
Telegram re-delivers anything not acked within seconds and a capture can take
minutes; a failed fetch is acked to the sender (`Capture failed: ...`, or 406
on the email channel) rather than 500ing.

Telegram and email capture channels (`src/llmwiki/channels/`) are optional and
webhook-based — nothing to run locally, but each needs a one-time registration
step once `TELEGRAM_BOT_TOKEN`/`TELEGRAM_WEBHOOK_SECRET` or
`MAILGUN_SIGNING_KEY` are set in `.env` and the service is reachable over
public HTTPS (a tunnel for local dev): see the `curl setWebhook`/Mailgun-
dashboard notes next to those variables in `.env.example`.

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
