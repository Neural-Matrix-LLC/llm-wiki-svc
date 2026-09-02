# HISTORY

Every code, configuration and architectural change is logged here before it is
considered complete, per `CLAUDE.md`.

---

## 2026-09-01 — Development environment pinned to Python 3.11

**Goal.** Make the checked-in configuration true before the first commit. Three
things disagreed: `[tool.mypy] python_version = "3.11"`, a `.venv` that had been
rebuilt on 3.13.6, and `CLAUDE.md`'s claim that the venv was 3.11. Nothing
recorded which interpreter the project actually wanted.

**Root cause.** Two independent faults that both surfaced as "the documented
gate command does not work":

1. *mypy.* On 3.13 the resolver installs numpy 2.5.2, whose stubs use PEP 695
   `type` statements — 3.12+ syntax. Under `python_version = "3.11"` mypy fails
   parsing `numpy/__init__.pyi` and stops before reaching a single project file.
   On 3.11 the resolver picks numpy 2.4.6, whose stubs parse cleanly, so the
   pin and the interpreter agreeing is what fixes it, not a mypy setting.
2. *pytest.* `tests/` has no `__init__.py` and `[tool.pytest.ini_options]` set
   no `pythonpath`, so `tests.doubles` resolved only under `python -m pytest`
   (which puts the cwd on `sys.path`) and never under a bare `pytest`. This was
   the outstanding half of milestone N0 in `implement-plan-v1.4.md`.

**Implementation detail.**

* `.python-version` (new, committed) pins the development interpreter to
  3.11.14 — a full patch version, so `uv venv` resolves identically on a
  machine that has to download it. `requires-python` stays `>=3.11`: the
  library supports newer, only the dev environment is pinned. Constraining it
  to `<3.12` would have been a lie about the package and would hurt the §15
  repo split, where FUND consumes these distributions.
* `[tool.pytest.ini_options] pythonpath = ["."]` — one line, chosen over adding
  `tests/__init__.py` because it does not make the test tree an importable
  package or change how pytest resolves the modules under test.
* `.venv` deleted and rebuilt: `uv venv && uv pip install -e ".[dev]"`.
* `.gitignore` gained `.data/`, the local object store's runtime directory
  (`LOCAL_STORAGE_PATH`), which was untracked and would otherwise have been
  swept into the first commit.
* `uv.lock` regenerated (`uv lock`) for the new provider extras, the venv
  installed from it (`uv sync --extra dev`) rather than resolved independently,
  and `requirements.txt` regenerated from the result — so lock, venv and
  requirements now agree. `requirements.txt` was the stale artifact: it recorded
  `fastmcp==3.4.7` / `mcp==1.29.1` / `anthropic==1.2.0` while `uv.lock` already
  pinned `fastmcp 4.0.0` and `anthropic 1.3.0`. The full gate passes on the
  synced environment, fastmcp 4.x included.

**Related files.** `.python-version` (new), `pyproject.toml`, `.gitignore`,
`requirements.txt`, `uv.lock`, `CLAUDE.md`, `docs/implement-plan-v1.4.md`
(§1.1, §12 N0, §17).

**Test coverage.**

1. *No regressions.* 207 passed, 5 skipped, 6 deselected — identical to the
   pre-rebuild result, now under **bare `pytest`** as `CLAUDE.md` documents,
   and with plain `mypy` (no `--python-version` override) reporting success on
   all 53 source files. `ruff` clean, `smoke_flow.py --offline` passes.
2. *Obsolete tests removed.* None — this is configuration only.
3. *New tests.* None. The two faults are both "the documented command fails",
   which the gate itself now demonstrates; a test asserting that pytest can
   import its own conftest would only restate the run that already proves it.
4. *Documented* here and in `CLAUDE.md`, which now states the pin, why
   `[tool.mypy] python_version` must equal it, and how to rebuild the venv.

**`.env` was not modified** (per `CLAUDE.md`). Verified read-only against
`.env.example`: key-for-key in sync, no drift in either direction, and
`LLM_PROVIDER=anthropic` still validates against the widened `Provider` literal.
Six credentials remain unfilled, which is expected — see the note in §17 of the
plan about `LLM_API_KEY` holding its placeholder verbatim.

---

## 2026-09-01 — Multi-provider LLM support via LangChain

**Goal.** `LLM_PROVIDER` accepted `openai` and `google` as values but had no
adapter behind either: anything but `anthropic` or `fake` raised "arrives at
milestone N7". Model the LangChain approach so the six providers the project
actually cares about — OpenAI, Anthropic, Google, NVIDIA, DeepSeek, OpenRouter —
are reachable by changing one environment variable.

**Implementation detail.** This is the "`get_client()` may be implemented over
`get_llm()`, never the reverse" half of plan-v1.4 §7.5, pulled forward from
milestone N7. Three new modules in `src/llmwiki/llm/`:

* `providers.py` — a registry of `ProviderSpec(module, cls, extra, distribution)`
  for the five LangChain-backed providers. Every provider import happens inside
  `build()`, so importing the registry (or `factory`) pulls in no provider SDK;
  a missing extra raises a `RuntimeError` naming the exact `pip install`
  command rather than a bare `ModuleNotFoundError`.
* `langchain_client.py` — `LangChainLLM`, which implements the existing
  `LLMClient` protocol over any `BaseChatModel`. `complete()`'s signature is
  unchanged, so `wiki/compiler.py` and `agent/query.py` were not touched.
  Structured output uses a forced tool call, exactly as the Anthropic adapter
  does, so the compiler's JSON schemas pass through unmodified.
* `pricing.py` — the USD rate table, lifted out of `anthropic_client.py` and
  now shared by both adapters.

Anthropic deliberately keeps its native adapter and is **not** in the registry:
prompt caching, the `cache_control` breakpoint and per-model pricing all live
there, and that is where the project's cost guarantees come from. The
degradation on the LangChain path is documented in plan §7.5 — no prompt
caching, and `cost_usd = 0.0` for any model absent from `pricing.RATES`. Token
counts are always real; a zero cost means "not priced here", never "free". No
price was invented for a provider this repository has never billed against.

Two details were verified against the installed integration classes rather than
assumed, in a throwaway venv:

1. All five accept the same `model` / `api_key` / `base_url` keywords, which is
   why the registry carries no per-provider keyword spellings. `ChatOpenAI` is
   the one exception, on the token cap: it deprecated `max_tokens` in favour of
   `max_completion_tokens`, so `ProviderSpec.max_tokens_arg` overrides it there.
2. LangChain reports `input_tokens` as the *total* prompt size with cached
   tokens included, while `pricing.price()` bills cache reads and writes at
   their own multipliers. `LangChainLLM._usage` subtracts them, which is what
   stops a cache hit being charged twice.

`local` was dropped from the `Provider` literal rather than implemented: vLLM,
Ollama, LM Studio and llama.cpp all expose OpenAI-compatible routes, so
`LLM_PROVIDER=openai` with `LLM_BASE_URL` covers them with one code path fewer.

**Related files.** `src/llmwiki/llm/providers.py` (new),
`src/llmwiki/llm/langchain_client.py` (new), `src/llmwiki/llm/pricing.py` (new),
`src/llmwiki/llm/anthropic_client.py`, `src/llmwiki/factory.py`,
`src/llmwiki/config.py`, `pyproject.toml`, `.env.example`,
`docs/implement-plan-v1.4.md` (§7.4, §7.5, §7.6, §12, §13.2, §13.3, §14, §18),
`CLAUDE.md`.

**Test coverage.**

1. *No regressions.* All 189 pre-existing tests pass unmodified. `complete()`'s
   signature is unchanged, which is what made that achievable; the suite is now
   209 selected tests (207 passed, 5 skipped, 6 integration deselected). The
   three new source modules also added three cases to the parametrized
   `test_layering.py`, and pass it.
2. *Obsolete tests removed.*
   `test_config.py::test_unimplemented_provider_names_the_milestone_that_adds_it`
   asserted that `LLM_PROVIDER=openai` fails with a message naming N7. `openai`
   now works, so it documented a gap that no longer exists. Replaced in place by
   `test_every_provider_the_config_accepts_can_actually_be_built`, which asserts
   the stronger property: the `Provider` literal and `providers.REGISTRY` cannot
   drift apart.
3. *New tests.* `tests/unit/test_providers.py` (11) — the registry imports no
   provider SDK, checked in a fresh interpreter; the five providers are
   registered and Anthropic is not; every registered provider is an accepted
   config value; a missing extra names its `pip install` command; and, for
   whichever integrations are installed, the class really accepts the four
   keywords the registry passes (skipped when absent, so it costs nothing here
   and becomes a real check in an environment with the extras).
   `tests/unit/test_langchain_client.py` (9) — text and forced-tool paths
   against a scripted `BaseChatModel` returning real `AIMessage` objects; prose
   despite `tool_choice` still parsed; usage carries op/model/version; cached
   tokens subtracted; an unpriced model records real tokens and `cost_usd == 0.0`;
   a priced model matches `RATES`; a per-call `model` builds its own chat model
   (the D5 escalation path); chat models cached per (model, token cap).
   `langchain-core` was added to the `[dev]` extra so both run offline with no
   provider SDK and no network, per the unit-test rule.
4. *Documented* here, in `CLAUDE.md`, and in `docs/implement-plan-v1.4.md`
   §13.2 and §13.3.

**Note on the environment, not this change.** The dev tooling was missing from
`.venv` when this work started (`pytest`, `ruff`, `mypy` all absent) and was
reinstalled with `uv pip install -e ".[dev]"`. That venv was Python 3.13.6 while
`[tool.mypy] python_version = "3.11"`, so `mypy` had to be run with
`--python-version 3.13` to get past numpy's stubs. Resolved by the next entry,
which rebuilds the venv on 3.11; the plain `mypy` invocation is clean again.

---

## 2026-09-01 — LLM environment contract made provider-generic

**Goal.** `.env.example` named the LLM credential `ANTHROPIC_API_KEY` and the
model `LLM_DEFAULT_MODEL`. A provider-specific credential variable cannot be the
credential of the provider-agnostic layer design v1.4 §4.7 describes, and the
storage layer's cross-repo adoption is cheap precisely *because* its variable
names already agree with `FUND-financial-Research` (plan-v1.4 §11.1). This change
buys the same property for the LLM layer, while this repository is still the only
consumer and renaming is free.

**Implementation detail.** Four generic names are now the canonical spelling,
chosen to match what `FUND-financial-Research/fund_models/agent_base.py`
(`AgentConfig`) already reads, so the eventual shared package costs FUND no
environment migration:

| Canonical | Was | FUND `AgentConfig` |
|---|---|---|
| `LLM_PROVIDER` | `LLM_BACKEND` | `LLM_PROVIDER` |
| `LLM_API_KEY` | `ANTHROPIC_API_KEY` | `LLM_API_KEY` |
| `LLM_MODEL` | `LLM_DEFAULT_MODEL` | `LLM_MODEL` |
| `LLM_BASE_URL` | — | `LLM_BASE_URL` |

`LLM_BASE_URL` is new and genuinely wired: `AnthropicLLM` now takes `base_url`
and passes it to the SDK only when set, which covers gateways, proxies and
OpenAI-compatible endpoints.

`LLM_PROVIDER` accepts `openai`, `google` and `local` as *values* even though no
adapter exists for them, so the env contract is generic ahead of the adapters. A
misconfiguration is caught in `factory._build_llm_client` with a message naming
the provider and the milestone that adds it (N7), rather than an opaque pydantic
validation error or a failure at the socket.

Two variables were deliberately left out of the shared contract:
`COMPILE_EXECUTOR_MODEL`, which is wiki compilation policy (plan-1.1 D5), and
`EMBEDDING_*`, because design v1.4 places Vector Ops in the Core Wiki Package
rather than the LLM layer.

**Backward compatibility, and why it is shaped this way.** `ANTHROPIC_API_KEY`,
`LLM_DEFAULT_MODEL` and `LLM_BACKEND` are kept as **real settings fields**, not
properties, resolved onto the generic names by a `model_validator(mode="after")`
that then mirrors the resolved values back. Properties would have been tidier and
wrong: `Settings` sets `extra="ignore"`, so a field removed outright but still
passed as a constructor kwarg is silently swallowed — and `tests/conftest.py`
plus four test modules pass `llm_backend="fake"` exactly that way. The silent
failure mode would have been a test fixture asking for the fake backend and
quietly building a real Anthropic client against a live key. An explicitly-set
generic name wins over its alias; anything a settings source supplied counts as
explicitly set, so `.env` and kwargs behave identically.

This is why no call site outside `config.py` and `factory.py` had to change:
`wiki/compiler.py` (×3) and `agent/query.py` still read `settings.llm_default_model`
and get the truth. `tools.py` and `scripts/smoke_flow.py` were switched to the
canonical names for clarity, not necessity, and `cli.py --offline` now sets
`LLM_PROVIDER=fake`.

The aliases, the mirroring validator and their tests are removed at milestone N4,
when `agentkit-llm` is extracted.

**Related files.** `.env.example`, `src/llmwiki/config.py`,
`src/llmwiki/factory.py`, `src/llmwiki/llm/anthropic_client.py`,
`src/llmwiki/tools.py`, `src/llmwiki/cli.py`, `scripts/smoke_flow.py`,
`tests/unit/test_config.py`, `implement-plan-v1.4.md` (new §7.6; §11.3, §13.1,
§13.3 and §14 updated).

**Test coverage.**

*No regressions.* All 182 pre-existing tests pass **unmodified** — no fixture,
no assertion, no conftest change. That was the design constraint on the alias
mechanism, and it is the evidence that the rename changed naming rather than
behaviour. `ruff` clean, `mypy` clean across 50 source files,
`scripts/smoke_flow.py --offline` passes. Suite is now 189 unit tests, 6
integration deselected.

*Obsolete tests removed.* None. `test_defaults_are_the_cloud_backends` was
**extended** (not replaced) to assert `llm_provider` and `llm_model` alongside
the existing `llm_default_model` assertion, so the alias and the canonical name
are both pinned.

*New tests added* — 7, all in `tests/unit/test_config.py`:

- `test_generic_names_are_the_canonical_spelling` — the four new fields load.
- `test_deprecated_aliases_still_configure_the_generic_fields` — a pre-rename
  `.env` or fixture still selects the right provider, model and key.
- `test_both_spellings_read_the_same_value_after_resolution` — guards the
  mirroring; without it a call site on the old name could read a stale default.
- `test_the_generic_name_wins_over_its_deprecated_alias` — precedence.
- `test_generic_api_key_does_not_appear_in_repr` — `LLM_API_KEY` gets the same
  `SecretStr` protection `ANTHROPIC_API_KEY` had.
- `test_unimplemented_provider_names_the_milestone_that_adds_it` — asserts the
  error names both `openai` and `N7`.
- `test_missing_llm_key_is_reported_by_its_generic_name` — the `require()` failure
  says `LLM_API_KEY`, not `ANTHROPIC_API_KEY`.

Verified separately, outside pytest, that both spellings resolve identically when
supplied as real environment variables rather than constructor kwargs, since
pydantic-settings treats the two paths differently.

*Documented in.* `implement-plan-v1.4.md` §7.6 (the contract, the two exclusions,
and the alias-removal schedule) and §13.3 (the seven tests, listed under a
`pre-N0` row).

---

## 2026-09-01 — Phase 0.5 plan authored: `implement-plan-v1.4.md`

**Goal.** Design doc v1.4 (`llmwiki-KB-design_v1.4.md`) redrew the §2 architecture
diagram to separate shareable packages (Core Wiki Package §4.6, Shareable LLM
Integration Layer §4.7) from platform-specific services, and raised the
possibility that the LLM layer moves to its own repository, with the Storage
layer and Core Wiki Package reusable by `FUND-financial-Research`. This change
produces the corresponding implementation plan. **No code changed.**

**Implementation detail.** Added `implement-plan-v1.4.md` — a Phase 0.5
*restructuring* plan whose governing rule is no behaviour change: every
milestone ends with the same 182 unit tests green and `smoke_flow.py --offline`
passing. `implement-plan.md` v1.1 remains authoritative for Phase 0 behaviour;
the new plan covers packaging only.

Its content was derived from the repository as it actually is, not from the
diagram:

- `src/llmwiki/storage/**` imports nothing from `llmwiki` except its own
  `base.py`; `src/llmwiki/llm/**` imports only `llm/base.py` plus
  `models.plan.CostRecord`. The extraction seams design v1.4 asks for already
  exist — what is missing is packaging metadata and a guard that stops them
  closing again.
- The repository is **not under git** and bare `pytest` fails
  (`ModuleNotFoundError: tests.doubles`; `python -m pytest` passes, 182 tests in
  2.14 s). Both are fixed in the plan's first milestone, N0, before any file moves.
- `FUND-financial-Research` was inspected as the named consumer: it publishes
  `fund-models` as a real package with langchain/langgraph dependencies, has
  `fund_models/agent_base.py` (the `AgentBase`/`AgentConfig` pattern design v1.4
  §4.7 cites), and carries **two** parallel storage implementations
  (`utils/storage.py`, `fund_models/storage.py`) sharing six of seven `R2_*`
  environment variable names with this repository.

Nine locked packaging decisions (P1–P9), the load-bearing ones being: a monorepo
of independently-buildable distributions now with a defined graduation path to
separate repos (P1); shareable packages may never import `llmwiki`, enforced by
an AST test (P3); the LLM package's stable core stays the narrow `complete()`
protocol with LangChain `get_llm()` added as an optional extra rather than a
replacement, because `complete()` carries the prompt-caching, forced-tool
structured output and measured cost accounting that plan-1.1 §8.2's cost
guarantees depend on (P5); and `pip install llmwiki` pulling no web framework,
cloud SDK or extractor engine (P6).

Milestones N0–N8 order storage extraction before LLM extraction (storage has
zero coupling to untangle, so a failure there means the mechanism is wrong rather
than the extraction was hard), and gate the broad §4.7 surface — skills, memory,
context, `AgentBase`, eval helpers — behind a real cross-repo adoption at N5.
`llmwiki` needs none of that surface; building it before a consumer has exercised
the packaging is how a shared package acquires seven abstractions and one user.

**Deviation from design v1.4, recorded here as required.** The v1.4 Layer
Responsibilities table classifies Storage as "Infra" and not shareable. The plan
treats it as a third shareable unit (§2.1), on the evidence that it is the least
coupled code in the repository and has a concrete waiting consumer. The
distinction the table is reaching for is preserved: the provisioned bucket and
Vectorize indexes remain infra; the client code that talks to them is ordinary
library code. `storage/layout.py` stays in `llmwiki` — key construction is wiki
domain knowledge, not a generic storage concern.

**Related files.** `implement-plan-v1.4.md` (new, 851 lines). Read but not
modified: `llmwiki-KB-design_v1.4.md`, `implement-plan.md`, `pyproject.toml`,
`.env.example`, `src/llmwiki/{storage,llm,models,config,factory,tools}`,
`tests/unit/test_layering.py`, and in `../FUND-financial-Research`:
`fund_models/{agent_base,storage}.py`, `utils/storage.py`,
`agents/storage/src/main.py`, `pyproject.toml`.

**Test coverage.** No code changed, so no test changed: 182 unit tests pass, 6
integration deselected. The plan's §13 carries the mandated four-point test
section for the work it schedules — §13.1 no regressions (the 182 are the pass
condition of every milestone, and the three `CLAUDE.md` load-bearing tests are
individually accounted for); §13.2 obsolete tests announced, namely the
`storage` and `llm` rows of `test_layering.py`'s `FORBIDDEN` map, which match
nothing once those directories leave the distribution, plus the relocation (not
removal) of `test_local_store.py` and `test_store_contract.py` into the storage
package's own suite; §13.3 new tests per milestone, including the first direct
test of the LLM layer, which today has none; §13.4 where each is documented.

---

## 2026-08-30 — Phase 0 implementation (M0–M8), plus Docker packaging

**Goal.** Execute `implement-plan.md` v1.1: turn the pre-implementation
repository into a working Phase 0 vertical slice — capture, extraction,
chunking and embedding, incremental compilation, wiki-first retrieval with RAG
fallback, and the hybrid FastAPI + MCP interface over one core package. Docker
packaging was added at the user's request; it is not in the plan (see
*Deviations* below).

**Implementation detail.**

*Package and layering (plan §4).* `src/` layout installed editable, so nothing
anywhere manipulates `sys.path`. `pyproject.toml` declares abstract dependencies
and `requirements.txt` is the pinned lockfile from `pip freeze --exclude-editable`.
The L0–L5 layer ladder is enforced mechanically by `tests/unit/test_layering.py`,
which walks each module's AST and fails on a forbidden import edge.

*Adapters (plan §4.3).* Each of the four network-touching concerns — object
storage, embeddings, vector store, LLM — has a `typing.Protocol`, a real adapter
and a fake, selected at runtime by `factory.py`. Concrete imports are
function-local, so an offline run never imports `boto3` or `anthropic`. Domain
classes take protocols and never call the factory, which is what makes the
compiler testable with a spy store.

*The compiler (plan §8).* Five stages: summarize (one cheap call), locate
(semantic lookup against the **gist** index — one-line gists only, never page
bodies), plan (one cheap call capped at `COMPILE_MAX_PAGES`), execute (loads only
the page bodies the plan names, writes under an optimistic version check), record
(manifest, index, source note, cost ledger — no LLM calls). Global lint is a
separate scheduled entry point and never runs on the ingest path.

*Interface (plan §10).* Six canonical tools defined once in `tools.py`, exposed
over REST and MCP in one process. `tests/unit/test_tools_and_mcp.py` asserts both
transports reach the same function objects, so they cannot drift.

**Deviations from the plan, and why.**

1. **Docker was added** (`Dockerfile`, `docker-compose.yml`, `.dockerignore`).
   Not in `implement-plan.md` v1.1; requested during implementation. Multi-stage
   build, non-root user, health check against `/healthz`. Compose profiles cover
   the real service, an offline demo needing no keys, the smoke check, and the
   scheduled lint. Recorded as plan §6.10.
2. **`pipeline/chunker.py` added** — the plan's §3 layout named no home for
   chunking, but §12.3 requires `test_chunker.py`. It is a pure function over an
   `ExtractedDoc`, so it sits in L3 beside the pipeline that calls it.
3. **`extractors/text.py` added** — the plan named pdf/web/youtube extractors;
   plain text and markdown had no path, and uploading a `.md` file is an obvious
   Phase 0 case.
4. **`@lru_cache` in the factory replaced with an explicit keyed cache.** The
   plan's §4.3 snippet caches on the `Settings` argument, but pydantic `Settings`
   is mutable and therefore unhashable, so `lru_cache` raises `TypeError` on
   every call. The cache is now keyed on the fields that actually determine which
   client to build.
5. **`VectorizeStore.delete_by_source` resolves ids via a filtered query.**
   Vectorize has no delete-by-filter, so the protocol method is implemented as
   query-then-`delete_by_ids`, looping because `topK` is capped at 100.
6. **Source notes are registered in `gists.json`.** Found by the smoke script's
   lint step: a source note is a page, so a page in storage that the manifest
   does not know about is exactly what lint calls an orphan. They carry no gist
   vector, so they never become patch candidates.

**Two bugs the tests caught, both fixed in the code rather than the test.**

- `test_compiler_no_full_scan` failed on its first run: 6 page reads against a
  cap of 5. The cause is that `write_page`'s optimistic version check re-reads a
  page the compiler had already read. The check is worth keeping — it is what
  makes concurrent writes safe — and reading one page twice is not a scan, so the
  guard now counts **distinct** page bodies. Growth with corpus size, which is
  what the constraint is actually about, is asserted separately and exactly:
  `test_page_reads_do_not_grow_with_wiki_size` requires the count at 10 pages and
  at 200 to be *identical*.
- `tools.get_page` raised `ValueError` instead of returning 404 for an unknown
  slug: it fell back to the `source` page type, whose keys must be 16 hex
  characters. It now only tries that folder for a slug that looks like a source id.

**Related files.** `pyproject.toml`, `requirements.txt`, `.env.example`,
`.gitignore`, `.dockerignore`, `Dockerfile`, `docker-compose.yml`, `README.md`,
`src/llmwiki/**` (39 modules), `tests/**`, `scripts/{smoke_flow,bootstrap_indexes,backfill}.py`.

**Test coverage.**

*Added — unit (181 tests, no network, default `pytest` run):*

| File | Covers |
|---|---|
| `tests/unit/test_config.py` | settings load, `SecretStr` never in `repr`, missing vars named, `changeme` rejected |
| `tests/unit/test_layering.py` | **permanent** — L0–L5 import boundaries, transport-only-calls-tools, no module-level I/O |
| `tests/unit/test_layout.py` | content addressing, URL canonicalization, path-traversal rejection |
| `tests/unit/test_local_store.py` | root escape, atomic writes, Obsidian-readable vault |
| `tests/unit/test_store_contract.py` | one contract over local + R2 (stub S3 client) |
| `tests/unit/test_extractors.py` | pdf/web/youtube golden fixtures, modality detection, corrupt input → `ExtractionError` |
| `tests/unit/test_chunker.py` | headings before length, overlap, offset round-trip, degenerate inputs |
| `tests/unit/test_vector_contract.py` | one contract over memory + Vectorize (stub transport, asserts ndjson) |
| `tests/unit/test_pages_and_gists.py` | front-matter round trip, version conflict, index renders without reading bodies |
| `tests/unit/test_compiler_no_full_scan.py` | **permanent** — plan §8.3 all three assertions, plus flat page reads across a 20× wiki |
| `tests/unit/test_compiler_behaviour.py` | source notes, cost ledger, budget abort, create→patch downgrade, source preservation |
| `tests/unit/test_ingest.py` | dedup, status transitions, `raw/` append-only, corrupt source marked failed |
| `tests/unit/test_agent.py` | wiki-first ordering, RAG fallback, **every citation resolves**, empty KB says so |
| `tests/unit/test_routes.py` | each endpoint, 401 without token, 422 on bad body, 404 on missing page |
| `tests/unit/test_tools_and_mcp.py` | MCP exposes exactly the six canonical tools; REST and MCP share one implementation |
| `tests/unit/test_lint_and_cost.py` | orphan/stale/dangling/missing-gist findings, safe repair, cost aggregation |

*Added — integration (`@pytest.mark.integration`, opt-in, needs a live `.env`):*
`test_cloudflare.py` (R2 round trip; **embedding dimension equals `EMBEDDING_DIM`**;
Vectorize round trip polled for eventual consistency), `test_anthropic.py`
(structured output; **prompt caching asserted via `cache_read_input_tokens > 0`**),
`test_end_to_end.py` (capture → compile → cited answer → non-zero cost).

*Removed:* none. There was no prior test suite.

**Results.** `pytest` — 181 passed. `scripts/smoke_flow.py --offline` — SMOKE
PASS, 7 steps, under 2 seconds. Integration tests are unrun: they need Cloudflare
and Anthropic credentials, which do not exist yet (plan §15, open item 1). The
plan's §14 success criteria are therefore **not** yet demonstrated — they require
M1 against live Cloudflare and a 50-source corpus.

### Follow-up within the same change: MCP mount path

**Root cause.** FastMCP's `http_app()` serves the endpoint at `/mcp` *within its
own app*. Mounting that at `/mcp` put the real endpoint at `/mcp/mcp`, so every
MCP client got a 404 from `/mcp/`. The mounted app also owns a session manager
that must be started with the process; mounting alone never started it.

**Fix.** `mcp/server.py` builds the ASGI app with `http_app(path="/")`, and
`api/app.py` constructs `FastAPI(lifespan=mcp_app.lifespan)` so the session
manager's lifecycle is tied to the process.

**Test coverage.** Added `test_mcp_is_reachable_at_slash_mcp` — a real MCP
`initialize` handshake through `TestClient` against the mounted app. Caught only
by exercising the running container; the earlier MCP tests asserted the tool
registry, which was correct all along, not the mount path.

### Follow-up within the same change: the rest of the pre-commit gate

`ruff` and `mypy` were run after the tests passed and both had findings.

- `ruff` — 25 findings: 15 auto-fixed (import ordering), 9 line-length fixed by
  hand, and `B008` given a scoped per-file ignore for `api/routes.py`, since
  `Depends()`/`File()`/`Form()` in argument defaults is how FastAPI declares
  dependencies and the rule is a false positive on every FastAPI route module.
- `mypy --disallow-untyped-defs` — 7 errors, both clusters real: iterating a
  PyMuPDF `Document` gave `page` no type (now indexed by page number), and
  `PageFrontMatter(**meta)` splatted untyped YAML into a typed model (now
  `model_validate`, which validates at the storage boundary where it belongs).

Gate now clean: `ruff` passes, `mypy` reports no issues across 50 source files,
182 unit tests pass, `smoke_flow.py --offline` passes.
