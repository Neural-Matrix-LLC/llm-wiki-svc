# llmwiki — Technical Document

**Audience:** an engineer who needs to support, extend, fix, or test this
repository, without having read the design doc or implementation plan first.
**Scope:** the codebase as it exists on disk today (Phase 0, with the R1–R3
multi-provider LLM routing feature landed). This document describes
*implemented behaviour*. Where the roadmap differs from today's code, that is
called out explicitly rather than blended in.

**Primary sources this document distills**, and where to go for more:

| Document | What it's for |
|---|---|
| `docs/llmwiki-KB-design_v1.4.md` | Why the system is shaped this way — architecture rationale, phase plan. Read before making a *design* decision. |
| `docs/implement-plan-v1.4.md` | The packaging/extraction plan (mostly not yet executed — see §9). |
| `docs/HISTORY.md` | Chronological log of every change, bug, and deviation. The ground truth for "why is this line like this". |
| **This document** | The map: which class calls which, how to extend each seam, and the full API surface. Optimized for "I need to change X" and "what does Y expose". |

---

## 1. System Overview

llmwiki is a **research knowledge base**: sources you capture (PDFs, web
pages, YouTube transcripts, plain text) are stored immutably, then an LLM
incrementally compiles them into an interlinked markdown wiki. A query agent
answers questions from that wiki first, falling back to raw vector search,
and every citation it returns is verified to resolve back to a real captured
source.

```
Capture → raw/ (object storage) → Extract → Chunk + Embed → Vector Store
                                          └→ Incremental Wiki Compiler → wiki/
Query Agent reads wiki/ first, falls back to Vector Store, answers with citations.
```

Three things make the codebase tractable to work in:

1. **A strict, mechanically-enforced layer ladder** (§2) — you cannot import
   "upward" and have the test suite pass.
2. **One function surface, three transports** — `tools.py` contains the
   entire business API; the REST routes, the MCP tools, and the CLI are thin
   serialization shims over the exact same functions (§3.4, §6).
3. **Everything that touches the network is a `Protocol` with a real adapter
   and a fake** — `ObjectStore`, `VectorStore`, `Embedder`, `LLMClient`. Tests
   and `--offline` runs swap in the fakes; nothing else changes (§2.3).

---

## 2. Module Map and Layering

### 2.1 The layer ladder

`src/llmwiki/` is organized as directories, each one a "layer". Dependencies
point **inward/downward only**. This is not a convention — it is enforced by
`tests/unit/test_layering.py`, which walks the AST of every module (including
function-local imports) and fails the build on a forbidden edge. If you add
an import that violates the ladder, **move the code**, don't widen the rule
— `CLAUDE.md` names this test load-bearing.

```
L0  models/                (pure pydantic schemas, zero I/O, imports nothing else in llmwiki)
     │
L1  storage/  extractors/  embedding/  vector/  llm/     (adapters — each Protocol-based, siblings, never import each other)
     │
L2  wiki/     agent/                                     (domain logic — compiler, page I/O, query agent)
     │
L3  pipeline/                                             (orchestrates extract → chunk → embed → compile)
     │
L4  tools.py                                               (the entire public function surface — the "brain")
     │
L5  api/  mcp/  cli.py                                     (transports — validate input, call tools.py, serialize)
```

Two allowed exceptions, both serialization not logic: `api/` and `cli.py` may
import `wiki.pages.render_page` to render a page as markdown text, and
`cli.py` imports `factory` (it is an entry point, not a domain layer). See
`ALLOWED_EXCEPTIONS` in `test_layering.py` for the exact list — do not add to
it without also adding the reason there.

`factory.py` and `config.py` sit **outside** the ladder deliberately:
`config.py` is the only module allowed to read `os.environ` (besides the
routing-config loader, §5.6), and `factory.py` is the only place concrete
adapters (`boto3`, `anthropic`, `pymupdf`, …) get imported — always
function-locally, so an offline run never even imports them. Only `tools.py`
and the entry points (`cli.py`) call `factory`; domain classes (`Compiler`,
`QueryAgent`, `IngestPipeline`) take already-built protocol instances in
their constructors and never call the factory themselves. This is what makes
them testable with a spy store or a scripted LLM.

### 2.2 Directory-by-directory

| Path | Layer | Responsibility |
|---|---|---|
| `models/` | L0 | Pydantic schemas only: `source.py`, `chunk.py`, `page.py`, `plan.py`. No I/O. |
| `storage/` | L1 | `ObjectStore` protocol (`base.py`), `LocalObjectStore`, `R2ObjectStore`, and `layout.py` (the *only* module that builds object keys). |
| `extractors/` | L1 | `Extractor` protocol + modality detection (`base.py`), and one module per modality: `pdf.py`, `web.py`, `youtube.py`, `text.py`. |
| `embedding/` | L1 | `Embedder` protocol (`base.py`), `WorkersAIEmbedder`, `FakeEmbedder`. |
| `vector/` | L1 | `VectorStore` protocol (`base.py`), `VectorizeStore`, `MemoryVectorStore`. |
| `llm/` | L1 | `LLMClient` protocol (`base.py`), `AnthropicLLM`, `LangChainLLM`, `FakeLLM`, the provider registry, pricing, and the R1–R3 multi-provider router (`router.py`, `routing_config.py`). |
| `wiki/` | L2 | `compiler.py` (the incremental compiler), `pages.py` (read/write/parse), `gists.py` (the manifest + index), `lint.py` (scheduled global check). |
| `agent/` | L2 | `query.py` — `QueryAgent`, wiki-first retrieval with RAG fallback. |
| `chains/` | L1/L2-adjacent | `prompts_loader.py` + `prompts/*.md` — the five prompt templates the compiler and query agent use. |
| `pipeline/` | L3 | `ingest.py` (`IngestPipeline` — capture/extract/embed/compile orchestration), `chunker.py` (heading-aware text chunking). |
| `tools.py` | L4 | Every function any transport calls. This *is* the public Python API (§6.1). |
| `api/` | L5 | `app.py` (FastAPI app + MCP mount), `routes.py` (HTTP handlers). |
| `mcp/` | L5 | `server.py` — the six agent-facing MCP tools, same functions as `api/routes.py`. |
| `cli.py` | L5 | `llmwiki` console script. |
| `factory.py` | outside ladder | Builds concrete adapters from `Settings`. Only `tools.py` and `cli.py` call it. |
| `config.py` | outside ladder | `Settings` (pydantic-settings) — the only module reading `.env`/`os.environ`, except `llm/routing_config.py` (§5.6). |

Repo-root `config/` (note: **not** `src/llmwiki/config.py`) is a separate,
uninstalled thing — see §5.6.

### 2.3 The adapter pattern (why tests never touch the network)

Every external dependency is a `typing.Protocol`:

```python
# storage/base.py, extractors/base.py, embedding/base.py, vector/base.py, llm/base.py
class ObjectStore(Protocol): ...
class Extractor(Protocol): ...
class Embedder(Protocol): ...
class VectorStore(Protocol): ...
class LLMClient(Protocol): ...
```

`factory.py` maps `Settings` fields to concrete instances, with **function-local
imports of the concrete SDKs** (`boto3`, `anthropic`, `pymupdf`, `trafilatura`,
`youtube_transcript_api`) so importing `factory` itself never pulls one in.
Domain classes (`Compiler`, `QueryAgent`, `IngestPipeline`) are constructed
with already-built protocol instances — they never call `factory` themselves.
That is the seam tests exploit: pass a `MemoryVectorStore`, a
`LocalObjectStore`, a `FakeEmbedder`, a `FakeLLM` (or a scripted spy for any
of them) and you get the real control flow with zero network calls.

`--offline` (CLI flag, or `docker compose --profile offline`) sets four env
vars (`STORAGE_BACKEND=local`, `VECTOR_BACKEND=memory`,
`EMBEDDING_BACKEND=fake`, `LLM_PROVIDER=fake`) and, since the R1–R3 routing
feature landed, **also** points `LLMWIKI_PROVIDERS_CONFIG`/`LLMWIKI_OPS_CONFIG`
at a guaranteed-nonexistent path — otherwise a real `config/providers.py` in
the checkout would silently outrank `LLM_PROVIDER=fake` (see §5.6 and the
2026-09-06 `HISTORY.md` entry for the bug this fixed).

---

## 3. End-to-End Workflows (class/file → class/file)

### 3.1 Ingest → Compile

Entry points: `POST /ingest` or `POST /upload` (`api/routes.py`), the MCP
tool `ingest_source`, or `llmwiki ingest` (`cli.py`). All three call into
`tools.py`.

```
api/routes.py:ingest()              ┐
mcp/server.py:ingest_source()       ├─► tools.py:ingest_source()  ──► IngestPipeline.capture()
cli.py (ingest command)             ┘        (or tools.ingest_now, which also runs process())

IngestPipeline.capture()  [pipeline/ingest.py]
  ├─► storage.layout.source_id_for_bytes / source_id_for_url   (content-address the source)
  ├─► extractors.base.detect_modality                          (pick pdf/web/youtube/text)
  ├─► extractors.web.fetch / extractors.youtube.fetch_transcript  (URL sources only, at capture time)
  └─► ObjectStore.put()  → raw/{id}/original.*, raw/{id}/meta.json     (immutable, written once)
  returns SourceRef{source_id, status="queued"} immediately

--- background task (api) or synchronous continuation (CLI/MCP) ---

IngestPipeline.process(source_id)  [pipeline/ingest.py]
  ├─► IngestPipeline.extract()
  │     └─► extractors.base.get_extractor(modality).extract()   → ExtractedDoc
  │           (PdfExtractor / WebExtractor / YouTubeExtractor / TextExtractor)
  │         ObjectStore.put()  → raw/{id}/extracted.md
  │
  ├─► IngestPipeline._embed()
  │     ├─► pipeline.chunker.chunk_document(doc)                → list[Chunk]
  │     ├─► Embedder.embed([chunk texts])                        → vectors
  │     └─► VectorStore.upsert(chunks index, ...)
  │
  └─► Compiler.compile_source(doc)     [wiki/compiler.py]  — see §3.2
```

### 3.2 The Incremental Compiler (five stages, `wiki/compiler.py:Compiler`)

This is the core of the design — it exists to satisfy one constraint:
**compilation must never scan the full wiki** (guarded by
`tests/unit/test_compiler_no_full_scan.py`, load-bearing per `CLAUDE.md`).

```
Compiler.compile_source(doc)
  │
  ├─ 1. _summarize(doc)          one LLM call, op="summarize_source"
  │       LLMClient.complete(schema=SUMMARY_SCHEMA)  →  SourceSummary
  │
  ├─ 2. _locate(summary, manifest)   NO LLM call, NO page bodies read
  │       gists.load_gists(store)               → dict[slug, PageGist]  (one object read)
  │       Embedder.embed(probes)  →  VectorStore.query(gists_index)     (gist vectors only)
  │
  ├─ 3. _plan(summary, candidates)   one LLM call, op="plan_compile"
  │       LLMClient.complete(schema=PLAN_SCHEMA)  →  CompilePlan (capped at COMPILE_MAX_PAGES ops)
  │
  ├─ 4. _execute(doc, summary, plan, manifest, result)
  │       for each CompileOp:
  │         _create_page()  → LLMClient.complete(op="create_page")  → wiki.pages.write_page()
  │         _patch_page()   → wiki.pages.read_page() [the ONE counted page-body read]
  │                          → LLMClient.complete(op="patch_page")  → wiki.pages.write_page()
  │       write_page() enforces an optimistic version check (VersionConflict on a race)
  │
  └─ 5. _record(doc, result, manifest)     NO LLM calls
          writes the source note (wiki/sources/{id}.md)
          gists.upsert_gist() + gists.save_gists() + gists.write_index()   (mechanical, no LLM)
          appends CostRecord rows to wiki/_meta/cost.jsonl
```

Every LLM call in the compiler goes through `chains.prompts_loader.load_prompt(name)`
for its `system` prompt and a JSON schema (`SUMMARY_SCHEMA`, `PLAN_SCHEMA`,
`PAGE_SCHEMA` — all defined at the top of `wiki/compiler.py`) forcing
structured output. Global lint/synthesis (`wiki/lint.py:lint_wiki`) is
**not** part of this path — it is the one place allowed to call
`ObjectStore.list()` on `wiki/`, and it only runs from a schedule or
`llmwiki lint`.

### 3.3 Query

```
api/routes.py:answer()  /  mcp does NOT expose this (see §6.3)  /  cli.py "ask" command
      │
      ▼
tools.py:answer(query)  ──►  agent.query.QueryAgent.answer(query)
      │
      ├─ Embedder.embed([query])
      ├─ VectorStore.query(gists_index)             ← wiki search, tried FIRST
      │     if best hit score ≥ WIKI_CONFIDENCE (0.35): wiki alone is used
      │     else: VectorStore.query(chunks_index)    ← RAG fallback, only now
      ├─ _build_context()
      │     wiki.pages.read_page() for each wiki hit's slug
      │     citations built from each page's front_matter.sources
      │     (+ chunk hits' source_id/url if the fallback ran)
      ├─ LLMClient.complete(op="answer_query")        (no schema — free text)
      └─ resolved = [c for c in citations if self.source_exists(c.source_id)]
            source_exists() checks ObjectStore.exists(raw/{id}/meta.json)
            → Answer{text, citations, used_rag_fallback}
```

The citation-resolution step is what
`tests/unit/test_agent.py::test_every_citation_resolves_to_a_real_raw_object`
guards (load-bearing per `CLAUDE.md`): an `Answer` can never cite a source
that isn't really in `raw/`.

### 3.4 One function surface, three transports

`tools.py` defines every operation once. `api/routes.py`, `mcp/server.py`,
and `cli.py` each call the *same function objects* — `test_layering.py`'s
`test_transport_layer_only_calls_tools` enforces that these three modules
import nothing from `storage/extractors/embedding/vector/llm/pipeline/factory`
directly, and `tests/unit/test_tools_and_mcp.py` separately asserts the REST
and MCP surfaces cannot drift apart. See §6 for the full surface.

### 3.5 LLM call resolution (single-provider vs. multi-provider routing)

Every call site in the domain layer (`wiki/compiler.py`, `agent/query.py`)
calls `LLMClient.complete(op=..., system=..., prompt=..., schema=...)` — it
never knows or cares which concrete client answers it. `factory.llm_client()`
decides that once, at construction time:

```
factory.llm_client(cfg)
  └─► factory._build_llm_client(cfg)
        └─► llm.routing_config.load_routing_config(cfg.llm_providers_config, cfg.llm_ops_config)
              │
              ├─ config/providers.py + config/ops.py BOTH exist
              │     → RoutingConfig{providers, ops}
              │     → factory._build_routed_llm_client(cfg, routing)
              │           builds one concrete client per *active* provider
              │           → llm.router.RoutingLLMClient(routing, clients)
              │                 .complete(op=...) looks up routing.ops[op] → dispatches
              │                 to the right client with that op's model/temperature/max_tokens
              │
              └─ neither file exists (the default, zero-config case)
                    → single-provider fallback, driven entirely by Settings
                    → factory._construct_provider_client(cfg.llm_provider, ...)
                          "anthropic" → llm.anthropic_client.AnthropicLLM     (native: caching, cost)
                          other       → llm.langchain_client.LangChainLLM    (via llm.providers.build())
                          "fake"      → llm.fake.FakeLLM
```

See §5 for how to extend either path.

---

## 4. Data Model Reference (`models/`)

All schemas are pydantic `BaseModel`s, L0 (no I/O, no imports from anywhere
else in `llmwiki`). This is the vocabulary every layer above speaks.

| Model | File | Purpose |
|---|---|---|
| `SourceMeta` | `models/source.py` | Written once to `raw/{id}/meta.json`, never mutated. |
| `SourceRef` | `models/source.py` | What `ingest_source` returns immediately (`source_id`, `status`, `duplicate`). |
| `SourceStatus` | `models/source.py` | Pipeline progress, polled via `GET /sources/{id}`. |
| `ExtractedDoc` | `models/source.py` | Normalized text from an extractor, before chunking. |
| `Chunk` / `ChunkMetadata` | `models/chunk.py` | One embeddable slice; `Chunk.make_id()` is deterministic so re-ingest overwrites, never duplicates. |
| `SearchHit` | `models/chunk.py` | One retrieval result, from either the gist or chunk index. |
| `PageFrontMatter` / `WikiPage` | `models/page.py` | The YAML block + body of every wiki page. |
| `PageGist` | `models/page.py` | One row of `wiki/_meta/gists.json` — the progressive-disclosure index; this is what the compiler reads instead of page bodies. |
| `LintFinding` / `LintReport` | `models/page.py` | Output of the scheduled global lint. |
| `CompileOp` / `CompilePlan` | `models/plan.py` | The planner's output — sees gists only, never page bodies. |
| `SourceSummary` | `models/plan.py` | The compiler's only view of the raw source text. |
| `CompileResult` | `models/plan.py` | What one compile pass did — returned by `compile_update`. |
| `CostRecord` | `models/plan.py` | One line of `wiki/_meta/cost.jsonl` — fields are effectively stable, since they're serialized to disk and read back. |
| `CostSummary` | `models/plan.py` | Aggregation for `llmwiki cost`. |
| `Citation` / `Answer` | `models/plan.py` | The query agent's output; every `Citation` is checked to resolve. |

`wiki/_meta/gists.json` deserves emphasis: it is the single object that lets
the compiler, `list_concepts`, and `wiki/index.md` rendering all avoid
scanning `wiki/` — a cost that would otherwise grow with corpus size.

---

## 5. Extension Guide

### 5.1 Adding a new LLM provider (via LangChain — the common case)

This is the path for any provider LangChain already integrates
(`langchain-*` package exists). Follow `llm/providers.py`'s existing entries
as the template.

1. **`src/llmwiki/llm/providers.py`** — add a `ProviderSpec` row to
   `REGISTRY`:
   ```python
   "mistral": ProviderSpec(
       "langchain_mistralai", "ChatMistralAI", "mistral", "langchain-mistralai"
   ),
   ```
   Set `max_tokens_arg="..."` only if that integration's constructor spells
   the token cap differently than `max_tokens` (see `openai`'s
   `max_completion_tokens` for precedent). Do **not** import the provider
   module at the top of the file — `load_class()` imports it function-locally
   inside `build()`, which is what keeps `pip install llmwiki` free of every
   provider SDK; `tests/unit/test_providers.py::test_importing_the_registry_imports_no_provider_sdk`
   is load-bearing per `CLAUDE.md` and will fail if you break this.

2. **`src/llmwiki/config.py`** — add the literal to `Provider`:
   ```python
   Provider = Literal["anthropic", "fake", "openai", "google", "nvidia",
                       "deepseek", "openrouter", "mistral"]
   ```

3. **`pyproject.toml`** — add the extra:
   ```toml
   mistral = ["llmwiki[langchain]", "langchain-mistralai"]
   all-providers = ["llmwiki[openai,google,nvidia,deepseek,openrouter,mistral]"]
   ```

4. **`.env.example`** — add a row to the provider table comment (this is
   mandated by `CLAUDE.md` §"`.env.example` is mandatory and must stay in
   sync" independent of this repo's own conventions).

5. **`config/providers.py.example`** — add a commented-out row so a
   multi-provider deployment can enable it by uncommenting.

6. **`src/llmwiki/llm/pricing.py`** — optional but recommended: add
   `RATES` entries for the models you'll actually use, or every call through
   this provider records `cost_usd = 0.0` (which means "unpriced", not
   "free" — see the module docstring).

7. **Tests** — `tests/unit/test_providers.py` already parametrizes over
   `REGISTRY`, so a correctly-shaped new entry is exercised automatically
   (skipped if the extra isn't installed). No new test file needed unless the
   provider's constructor genuinely disagrees with the shared keyword
   contract (`model`, `api_key`, `base_url`, `temperature`) — if it does,
   that disagreement is exactly what `test_registry_keywords_match_the_installed_class`
   checks for.

No change to `factory.py`, `llm/router.py`, `wiki/compiler.py`, or
`agent/query.py` is needed — the domain layer only ever sees `LLMClient`.

### 5.2 Adding a new LLM provider with a *native* adapter (like Anthropic)

Only do this if you need something LangChain's generic surface can't give
you (Anthropic's case: prompt caching via `cache_control`, and precise
`cache_read`/`cache_write` token accounting). Otherwise prefer §5.1.

1. Create `src/llmwiki/llm/<provider>_client.py`, implementing `LLMClient`
   (the `complete()` signature in `llm/base.py`). Use `anthropic_client.py`
   as the template: import the SDK inside `__init__` (never at module level),
   build a `CostRecord` from the SDK's real usage fields, price it via
   `llm/pricing.py`.
2. Wire it into `factory._construct_provider_client()` alongside the
   `"anthropic"` branch.
3. Add the literal to `config.Provider`.
4. `cfg.require(...)` for whatever credentials it needs, mirroring the
   Anthropic branch's pattern in `factory._build_llm_client`.

### 5.3 Adding a new compiler/agent operation (a new `op=` value)

The five existing ops (`summarize_source`, `plan_compile`, `create_page`,
`patch_page`, `answer_query`) are enumerated in exactly one place that
matters for validation: `llm/routing_config.py:KNOWN_OPS`. If you add a sixth
call site that does `llm.complete(op="new_thing", ...)`:

1. Add `"new_thing"` to `KNOWN_OPS` in `llm/routing_config.py`.
2. Add a row for it to `config/ops.py.example` (and any real
   `config/ops.py` a deployment has — `_resolve_ops` fails loudly at startup
   if a known op has no row, by design).
3. If it needs a prompt template, add `chains/prompts/new_thing.md` and call
   `load_prompt("new_thing")` at the call site.
4. Existing single-provider fallback needs no change — `Settings.llm_model`/
   `llm_max_tokens`/`llm_temperature` apply uniformly regardless of `op`.

### 5.4 Adding a new extractor (modality)

1. Create `src/llmwiki/extractors/<modality>.py` with a class implementing
   `Extractor` (`extract(meta, data) -> ExtractedDoc` — see `extractors/base.py`).
   Concrete parsing libraries import function-locally, matching every
   existing extractor.
2. Add the modality to `models/source.py:Modality` (a `Literal`).
3. Wire detection into `extractors/base.py:detect_modality()` — mind the
   comment about ordering ("URL shape beats mime").
4. Wire dispatch into `extractors/base.py:get_extractor()`.
5. If the modality needs a network fetch at capture time (like `web.py`'s
   `fetch()` or `youtube.py`'s `fetch_transcript()`), add that function here
   and call it from `pipeline/ingest.py:IngestPipeline._fetch()`. Capture
   must store the fetched bytes under `raw/` first — extraction is required
   to stay a pure function of stored bytes, so it can be re-run offline
   later without hitting the network again.
6. Add a fixture under `tests/fixtures/` and a case in
   `tests/unit/test_extractors.py`.

### 5.5 Adding a new storage or vector backend

Both follow the same shape as §5.1/§5.4: implement the `Protocol`
(`storage/base.py:ObjectStore` or `vector/base.py:VectorStore`), add a
`Literal` value to `config.Settings.storage_backend` / `.vector_backend`,
and wire a branch into `factory._build_object_store()` /
`factory._build_vector_store()`, importing the concrete SDK function-locally.
`tests/unit/test_store_contract.py` and `test_vector_contract.py` run **one**
shared contract test against every registered implementation — add yours to
their parametrization rather than writing a parallel test file, so it's held
to the same behavioural contract (idempotent upsert, `ObjectNotFound` on a
missing key, etc.) automatically.

### 5.6 Multi-provider / per-op LLM routing (deployment-time extension, no code change)

This is not a code extension but a **deployment configuration** extension —
worth documenting here because it's the mechanism that makes §5.1's new
providers actually usable in combination. It exists outside `src/llmwiki`
entirely (design v1.4 §4.8: this is *this application's* concern, not part
of the shareable LLM contract).

```bash
cp config/providers.py.example config/providers.py
cp config/ops.py.example config/ops.py
```

- `config/providers.py` defines `PROVIDERS: list[dict]` — which providers are
  available and which **environment variable name** (not value) carries each
  one's credentials. It holds no secrets.
- `config/ops.py` defines `OPS: list[dict]` — one row per op in `KNOWN_OPS`
  (§5.3), naming provider/model/temperature/max_tokens.
- **Presence of both files is the switch.** Absent (the default, a fresh
  clone) → today's single-provider path from `Settings` — zero behaviour
  change. Present → `llm/routing_config.load_routing_config()` builds a
  `RoutingConfig`, and `factory._build_routed_llm_client()` constructs one
  concrete client per *active* provider and wraps them in
  `llm.router.RoutingLLMClient`.
- A provider row whose `api_key_env` resolves to empty (unset in both the
  real environment and `.env`) is silently **inactive**, not an error — only
  an `ops.py` row that names an inactive/undefined provider fails, loudly, at
  load time. Same "fail by name" posture as `Settings.require`.
- **Gotcha, already hit once (see `HISTORY.md` 2026-09-06):** once
  `config/providers.py` exists in the checkout, it also governs *this
  repository's own* `pytest` run and `scripts/smoke_flow.py --offline`,
  because both build a default `Settings()` that resolves the same relative
  path. `tests/conftest.py` has an autouse fixture that points both config
  paths at a nonexistent path for every test, and `--offline` does the same
  — so this is handled, but if you ever see a test unexpectedly trying real
  network calls, check whether that guard got bypassed (e.g. a test
  constructing `Settings` some new way).
- Credential resolution precedence: a real `os.environ` value wins over one
  read from `.env` (via `dotenv_values()`, which never mutates
  `os.environ`) — matching pydantic-settings' own source order. This is why
  `llm/routing_config.py` is the *second* module (after `config.py`) allowed
  to touch environment/`.env` state directly: a provider row's
  `api_key_env` names an arbitrary variable not known until the file itself
  is read, so `Settings`' fixed fields can't cover it.

### 5.7 Adding a new tool / API endpoint / MCP tool

1. Write the function in `tools.py`. It should call one or two collaborators
   (`_pipeline(cfg)`, `_agent(cfg)`, or `factory.*` directly) and contain no
   business logic itself beyond wiring — the logic belongs in `wiki/`,
   `agent/`, or `pipeline/`.
2. To expose it over REST: add a route in `api/routes.py` that calls it and
   nothing else (`test_transport_layer_only_calls_tools` will fail the build
   if you import a domain module here directly).
3. To expose it to agents over MCP: add an `@mcp.tool()` function in
   `mcp/server.py:build_server()` that calls the same `tools.py` function,
   serializing pydantic results with `.model_dump(mode="json")`. **Think
   before doing this** — the design deliberately keeps the MCP surface to
   six tools (`search_wiki`, `get_page`, `ingest_source`, `compile_update`,
   `list_concepts`, `lint_wiki`) on the stated reasoning that "a small,
   well-named tool set is easier for a model to use correctly than a large
   one" (`mcp/server.py` docstring). Helper operations (`answer`, status,
   cost) are intentionally REST/CLI-only.
4. To expose it on the CLI: add a subparser in `cli.py:build_parser()` and a
   branch in `main()`.
5. Add a test: for REST, a case in `tests/unit/test_routes.py`; for MCP
   parity, `tests/unit/test_tools_and_mcp.py` already asserts the tool
   registry and REST reach the same function objects — extend its assertions
   if you add or remove a tool.

### 5.8 Not yet implemented: R4/R5 (roadmap)

**This section documents what is coming next, not what exists today.**
Design v1.4 §4.8.2 and `implement-plan-v1.4.md` §19 describe two further
milestones, deliberately deferred out of the R1–R3 change that shipped the
routing feature above (`HISTORY.md`, 2026-09-05 entry, is explicit that
`chains/prompts/*.md` and `chains/prompts_loader.py` are untouched by R1–R3):

- **R4 — SKILL.md-format prompts.** The five files under
  `chains/prompts/*.md` gain real YAML frontmatter (`name`, `description`) so
  each becomes an independently valid [Agent
  Skill](https://code.claude.com/docs/en/skills), discoverable by an external
  harness (Claude Code, the Claude Agent SDK, an MCP client), not only by
  this codebase's own `load_prompt()`. The four **compiler** stages keep
  their deterministic, fixed op→prompt mapping — this is unchanged and is
  what §4.4's cost-bounded, non-agentic compilation guarantee depends on.
- **R5 — query-agent skill invocation.** The **query agent only** gains a
  genuine skill-invocation capability: instead of always loading
  `answer_query.md`, it is given the discovered skill set and picks (and can
  chain) among them per question, via a real tool-use round trip with the
  model. `Settings.agent_skills_dir` (`config.py`, default `./skills`) is
  already declared for this — reserved, but **not yet read by any code
  path**.

When R4/R5 land, this document's §3.2/§3.3 workflow diagrams and §5.3's "how
to add an op" instructions will need a corresponding update — check
`docs/HISTORY.md` for the entry before relying on this section as current.

---

## 6. API Reference

### 6.1 Python core layer (`llmwiki.tools`)

This is the layer other Python code — a notebook, a script, another service
(design v1.4 names `FUND-financial-Research` as an intended cross-repo
consumer, see `implement-plan-v1.4.md` §11) — should import directly, rather
than going through HTTP. `pip install -e ".[dev]"` and:

```python
from llmwiki import tools
from llmwiki.config import load_settings

cfg = load_settings()             # or pass cfg=None to use the process-wide default
result = tools.search_wiki("retrieval augmented generation", k=5, cfg=cfg)
```

Every function takes an optional `cfg: Settings | None = None` — omit it to
use the module-level default `Settings()` built from `.env`/environment at
import time, or pass an explicit `Settings(...)` to point at different
backends without touching the environment (this is exactly how the test
suite and the CLI's `--offline` flag work).

**The six canonical tools** (also the entire MCP surface, §6.3):

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `search_wiki` | `(query: str, k: int = 5, cfg=None)` | `list[SearchHit]` | Wiki-first; chunk fallback only if the top wiki hit is weak. |
| `get_page` | `(slug: str, cfg=None)` | `WikiPage` | Raises `tools.PageNotFound` (a `KeyError`) if absent. |
| `ingest_source` | `(url=None, file=None, filename=None, mime="", title="", cfg=None)` | `SourceRef` | Capture only — returns before extraction/compilation run. |
| `compile_update` | `(source_id: str, force: bool = False, cfg=None)` | `CompileResult` | Re-runs the 5-stage compiler for an already-captured source. |
| `list_concepts` | `(prefix: str \| None = None, cfg=None)` | `list[PageGist]` | One object read (`gists.json`); no LLM call regardless of wiki size. |
| `lint_wiki` | `(dry_run: bool = True, cfg=None)` | `LintReport` | The only function allowed to scan all of `wiki/`. Not on the ingest path. |

**Helper functions** (Python/REST/CLI, deliberately **not** MCP tools):

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `ingest_now` | `(url=None, file=None, filename=None, mime="", title="", cfg=None)` | `SourceStatus` | Capture **and** process, synchronously. Used by CLI and the smoke script. |
| `process_source` | `(source_id: str, cfg=None)` | `SourceStatus` | The expensive half of ingest; called by the API's background task. |
| `get_source_status` | `(source_id: str, cfg=None)` | `SourceStatus` | Poll pipeline state. |
| `answer` | `(query: str, k: int = 5, cfg=None)` | `Answer` | Full RAG answer with verified citations. |
| `source_exists` | `(source_id: str, cfg=None)` | `bool` | True iff a real object exists under `raw/{id}/`. |
| `cost_summary` | `(since: datetime \| None = None, cfg=None)` | `CostSummary` | Aggregates `wiki/_meta/cost.jsonl`. |
| `delete_source` | `(source_id: str, cfg=None)` | `int` | Removes `raw/` objects, status, and vectors for a source. Leaves compiled pages alone. |
| `health` | `(cfg=None)` | `dict` | No network calls — just reports configured backends/version. |

### 6.2 REST API (`llmwiki.api`, `api/routes.py`)

Base: whatever `llmwiki serve` binds (`API_HOST`/`API_PORT`, default
`0.0.0.0:8000`). Auth: a single static bearer token (`INGEST_API_TOKEN`),
checked with `secrets.compare_digest` — Phase 0 only; see design doc for the
Phase 1 auth plan. `Authorization: Bearer <token>` is required on the routes
marked 🔒 below.

| Method & path | Auth | Body / Query | Response model | Calls |
|---|:-:|---|---|---|
| `GET /healthz` | — | — | `dict` | `tools.health()` |
| `POST /ingest` | 🔒 | JSON `{url, title?}` | `SourceRef` | `tools.ingest_source()` + background `process_source` |
| `POST /upload` | 🔒 | multipart `file`, `title?` | `SourceRef` | same, for file uploads |
| `GET /sources/{source_id}` | — | — | `SourceStatus` | `tools.get_source_status()` |
| `GET /search` | — | `q`, `k=5` | `list[SearchHit]` | `tools.search_wiki()` |
| `GET /answer` | — | `q`, `k=5` | `Answer` | `tools.answer()` |
| `GET /concepts` | — | `prefix?` | `list[PageGist]` | `tools.list_concepts()` |
| `GET /page/{slug}` | — | — | raw markdown (`text/plain`) | `tools.get_page()` → `wiki.pages.render_page()`; 404 if absent |
| `POST /compile/{source_id}` | 🔒 | `force=false` | `CompileResult` | `tools.compile_update()` |
| `POST /lint` | 🔒 | `dry_run=true` | `LintReport` | `tools.lint_wiki()` |

OpenAPI/Swagger is auto-generated by FastAPI at `/docs` (interactive) and
`/openapi.json` while the service is running.

### 6.3 MCP tools (`llmwiki.mcp`, `mcp/server.py`)

Mounted inside the same FastAPI process at `/mcp` (`api/app.py` builds the
MCP ASGI app and mounts it — note the `path="/"` / mount-at-`/mcp` detail
recorded in `HISTORY.md`, easy to regress if you touch this file). Also
runnable standalone over stdio: `python -m llmwiki.mcp.server`.

Exactly six tools, deliberately no more (§5.7):

| MCP tool | Args | Wraps |
|---|---|---|
| `search_wiki` | `query: str, k: int = 5` | `tools.search_wiki` |
| `get_page` | `slug: str` | `tools.get_page` |
| `ingest_source` | `url: str, title: str = ""` | `tools.ingest_source` (and synchronously runs `process_source` before returning — MCP has no background-task concept here) |
| `compile_update` | `source_id: str, force: bool = False` | `tools.compile_update` |
| `list_concepts` | `prefix: str \| None = None` | `tools.list_concepts` |
| `lint_wiki` | `dry_run: bool = True` | `tools.lint_wiki` |

### 6.4 CLI (`llmwiki`, `cli.py`)

```
llmwiki [--offline] <command> [args]
```

| Command | Args | Calls |
|---|---|---|
| `ingest` | `--url URL \| --file PATH`, `--title` | `tools.ingest_now` |
| `search` | `query`, `-k N` | `tools.search_wiki` |
| `ask` | `query` | `tools.answer` |
| `page` | `slug` | `tools.get_page` (prints rendered markdown) |
| `concepts` | `--prefix` | `tools.list_concepts` |
| `compile` | `source_id`, `--force` | `tools.compile_update` |
| `lint` | `--fix` | `tools.lint_wiki` |
| `cost` | — | `tools.cost_summary` |
| `status` | — | `tools.health` |
| `source` | `source_id` | `tools.get_source_status` |
| `serve` | `--host`, `--port`, `--reload` | runs `uvicorn` against `llmwiki.api.app:app` |

`--offline` (must precede the subcommand) forces
`STORAGE_BACKEND=local VECTOR_BACKEND=memory EMBEDDING_BACKEND=fake
LLM_PROVIDER=fake` before `Settings` is built.

### 6.5 Configuration reference (`config.Settings`)

`Settings` (pydantic-settings, `env_file=".env"`, case-insensitive,
`extra="ignore"`) is the single source of runtime configuration. Full,
current variable list and defaults live in `.env.example` — treat that file,
not this table, as authoritative, since it is what `CLAUDE.md` mandates be
kept in sync with the code. In summary, grouped:

- **LLM (provider-generic contract):** `LLM_PROVIDER`, `LLM_API_KEY`,
  `LLM_MODEL`, `LLM_BASE_URL`, `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`.
- **Multi-provider routing (§5.6):** `LLMWIKI_PROVIDERS_CONFIG`,
  `LLMWIKI_OPS_CONFIG` (paths; default `./config/{providers,ops}.py`),
  `AGENT_SKILLS_DIR` (reserved for R5, unused today).
- **Deprecated LLM aliases** (still read, removed at a future milestone):
  `ANTHROPIC_API_KEY`, `LLM_DEFAULT_MODEL`, `LLM_BACKEND`.
- **Observability:** `LOG_LEVEL`; `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
  `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`.
- **Cloudflare:** `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT_URL`,
  `VECTORIZE_CHUNKS_INDEX`, `VECTORIZE_GISTS_INDEX`, `EMBEDDING_MODEL`,
  `EMBEDDING_DIM`.
- **Service:** `INGEST_API_TOKEN`, `API_HOST`, `API_PORT`.
- **Cost guardrails:** `COMPILE_MAX_PAGES`, `COMPILE_CANDIDATE_PAGES`,
  `INGEST_TOKEN_BUDGET`, `CHUNK_SIZE_CHARS`, `CHUNK_OVERLAP_CHARS`.
- **Backend selection:** `STORAGE_BACKEND` (`r2|local`), `VECTOR_BACKEND`
  (`vectorize|memory`), `EMBEDDING_BACKEND` (`workers_ai|fake`),
  `LOCAL_STORAGE_PATH`.

`Settings.require("field_a", "field_b")` is what adapters call in their
constructors to fail loudly, by name, on missing credentials — use it as the
template for any new adapter's precondition check (§5.5).

---

## 7. Object Storage Layout (`storage/layout.py`)

The **only** module that builds object keys — never construct a key by hand
elsewhere.

```
raw/{source_id}/original.{ext}     immutable, written once at capture
raw/{source_id}/meta.json          immutable, written once at capture
raw/{source_id}/extracted.md       the one rewritable object under raw/ (re-run of extraction)
status/{source_id}.json            pipeline progress, polled by GET /sources/{id}
wiki/concepts/{slug}.md            compiled concept pages
wiki/entities/{slug}.md            compiled entity pages
wiki/sources/{source_id}.md        one source note per captured source
wiki/index.md                      mechanically regenerated on every compile — no LLM call
wiki/_meta/gists.json              the manifest — one-line gist per page, read instead of bodies
wiki/_meta/cost.jsonl              append-only cost ledger, one CostRecord per LLM call
```

`source_id` is a 16-hex-char SHA-256 prefix (content address for files,
canonical-URL address for URLs — see `source_id_for_bytes`/`source_id_for_url`)
so re-capturing identical content or the same URL twice always resolves to
the same source and never duplicates. `slugify()` is the one path from an
arbitrary title to a filesystem/Obsidian-safe key; no caller-supplied string
reaches a key unsanitized.

---

## 8. Testing

```bash
pytest                                  # unit tests, ~270 tests, no network, ~5s
pytest -m integration                   # needs a populated .env; costs money
python scripts/smoke_flow.py --offline  # end-to-end, no keys, under 2s
ruff check . && mypy                    # the rest of the pre-commit gate
```

Four tests are **load-bearing** (`CLAUDE.md`) — never weaken them to make a
change pass; if a change genuinely requires touching one, that is a signal
to stop and reconsider the change, not the test:

| Test | Guards |
|---|---|
| `tests/unit/test_layering.py` | The L0–L5 import ladder (§2.1). |
| `tests/unit/test_compiler_no_full_scan.py` | Design §4.4's central cost constraint — no full-wiki scan on the ingest path. |
| `tests/unit/test_agent.py::test_every_citation_resolves_to_a_real_raw_object` | The answer-with-citations contract (§3.3). |
| `tests/unit/test_providers.py::test_importing_the_registry_imports_no_provider_sdk` | `pip install llmwiki` stays free of every LLM provider SDK. |

Other tests worth knowing about when extending a specific seam: the
**contract tests** — `test_store_contract.py`, `test_vector_contract.py` —
run one shared behavioural suite against every registered
backend/implementation (§5.5); add a new backend there rather than writing a
parallel file. `test_tools_and_mcp.py` asserts REST/MCP surface parity
(§5.7). `test_routing_config.py` and `test_router.py` cover the multi-provider
routing feature (§5.6) in isolation, including the `.env`-fallback and
autouse-isolation-fixture behaviour described there.

`tests/doubles.py` and `tests/factories.py` hold shared spies/fakes and
object-builders used across the unit suite — check there before writing a
new one, most scenarios (a spy store that counts reads, a scripted LLM with
canned per-op responses) already exist.

**Known current state:** `pytest` — 267 passed, 1 skipped (a provider extra
not installed), 6 integration tests deselected, and **one pre-existing,
unrelated failure**
(`tests/unit/test_extractors.py::test_fetch_video_title_reads_oembed`) — see
`docs/HISTORY.md` for its status before assuming a new change caused it.

---

## 9. Deployment

```bash
docker compose up --build                        # reads .env, real backends
docker compose --profile offline up api-offline   # no keys, fake adapters, :8001
docker compose --profile test run --rm smoke      # runs scripts/smoke_flow.py --offline in the image
docker compose --profile ops run --rm lint        # llmwiki lint — intended as a scheduled job, not on ingest
```

Single-process design (`docker-compose.yml`'s own comment: "there is no
separate worker service to keep in sync, and no queue broker to operate") —
FastAPI serves REST, mounts MCP at `/mcp`, and runs ingest processing in
FastAPI `BackgroundTasks`. `Dockerfile` is a two-stage build (build stage has
compilers/pip cache; runtime stage ships only the built venv, runs as a
non-root user, health-checks `/healthz`).

---

## 10. Known Gap Between This Document, the Design Doc, and the Plan

`docs/implement-plan-v1.4.md` describes an aspirational repository layout
with `packages/agentkit-storage/` and `packages/agentkit-llm/` as
independently-installable distributions (milestones N0–N8). **As of this
document, none of that extraction has happened** — `git status`/the tree
show no `packages/` directory, and `src/llmwiki/llm/`, `src/llmwiki/storage/`
still live where §2.2 describes them. Sections 1–8 of this document describe
the actual, current code; do not assume the `packages/` layout exists when
navigating the repository. If N0–N8 land later, this document (particularly
§2.2's directory table and §5's extension paths) will need a corresponding
rewrite — check `docs/HISTORY.md` for a milestone entry before trusting this
section.

---

*This document is maintained as living Markdown alongside the code. Update it
when a module moves, a layer rule changes, a tool is added or removed from
the MCP/REST/CLI surface, or when R4/R5 (§5.8) land.*
