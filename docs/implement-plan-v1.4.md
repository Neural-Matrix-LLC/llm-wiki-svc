# Phase 0.5 Implementation Plan — Layer Separation & Shareable Packages

**Version:** 1.3
**Date:** 2026-09-18
**Derived from:** `llmwiki-KB-design_v1.4.md` (§2 layered architecture, §4.6 Core Wiki Package, §4.7 Shareable LLM Integration Layer, §4.8 Application-Specific LLM Routing, §4.10 Phase 2)
**Supersedes:** nothing. `implement-plan.md` v1.1 remains authoritative for Phase 0 *behaviour*. §1–§18 (v1.0) change only *packaging*, still with no behaviour change. §19 (new in 1.1), §20 (new in 1.2, Phase 1-D) and §21 (new in 1.3, Phase 2) are the exceptions: deliberate, scoped *behaviour* changes, independent of and not gated by the N0–N8 packaging milestones.
**Status:** §19, §20 and §21 landed. Every Phase 2 milestone (P2-1 … P2-Z) shipped on 2026-09-19; §21.9's table records each.

> **Reference convention:** "design v1.4 §X" points at `llmwiki-KB-design_v1.4.md`.
> "plan-1.1 §X" points at `implement-plan.md`. A bare "§X" points at a section of *this* plan.

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
| | | **21** | **Phase 2: Domains · Hybrid Retrieval · Cost · Multimodal (new, v1.3)** |

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
├── implement-plan.md                    # Phase 0 — behaviour. Still authoritative
├── implement-plan-v1.4.md               # this file — packaging
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

## 21. Phase 2: Domains · Hybrid Retrieval · Cost · Multimodal (new, v1.3)

> Design decision: design v1.4 §4.10 (v1.7). This section is the implementation design, approved
> 2026-09-18: locked decisions, data model and key layout, module layout, algorithms, surfaces,
> environment, milestones, tests and risks. Milestones land one change set at a time; `HISTORY.md`
> gets an entry per milestone.

### 21.1 Scope and relationship to §1–§20

Phase 2 of KB design §5, four workstreams: **A** domain partitioning + automated routing (+ scheduled
per-domain synthesis), **B** hybrid search + reranking, **C** usage dashboards + cost alerts (+ the
per-domain ingest worker), **D** selective multimodal. The custom mobile app is deferred. Like §19
and §20 this is *behaviour*, not packaging: nothing moves under `packages/`, N0–N8 are untouched.
`LLMClient.complete()` (P5) is byte-identical — vision is a *separate optional protocol* (D1). The
compiler stays non-agentic and never lists a wiki prefix (§4.4); the citation contract is untouched.

The design is organised around the five places in the Phase 1 code whose cost grows with corpus
size (design v1.4 §4.10's opening table): the whole-wiki gist manifest (`wiki/gists.py:19`, loaded by
`compiler.py:139`, `agent/query.py:250`, `tools.py:78,131`), the whole-index rewrite per compile
(`gists.py:97`), the single growing cost ledger (`compiler.py:481-517`), unserialized concurrent
ingests (`api/routes.py:92,115` — `BackgroundTasks` on anyio's pool), and dense-only retrieval
(`agent/graph.py:110`). Capability gaps: no image branch in `extractors/base.py:57-73`; scans raise at
`extractors/pdf.py:33`; no query-side cost anywhere.

**Organising principle for A:** `general` is not a new domain — it is the Phase 0/1 layout under a
name. Every key, index name and manifest of `general` is exactly what exists today; other domains
nest beside it. Zero data migration; the five load-bearing tests stay literally untouched
(`test_compiler_no_full_scan.py` reads `wiki/concepts/`; `test_agent.py` asserts
`settings.vectorize_gists_index` is queried first); "general-only reproduces today" is a property of
the layout.

### 21.2 Locked decisions

**A — Domains**

| # | Decision | Consequence |
|---|---|---|
| A1 | `general` keeps the Phase-1 keys and index names verbatim; domain `d` lives under `wiki/domains/{d}/…` with its own `_meta/gists.json`, `index.md`, and Vectorize/lexical indexes `{base}-{d}` | no migration; load-bearing tests untouched; the asymmetry lives in `storage/layout.py` only |
| A2 | Partition vectors **by index** (the existing `index` seam), not by a `domain` metadata filter | no re-upsert of existing vectors (Vectorize only filters vectors inserted after a metadata index exists); general queries carry no filter. `VectorStore` gains additive `ensure_index(index)` (memory: no-op; Vectorize: create + `FILTERABLE` metadata indexes + readiness poll, moved out of `scripts/bootstrap_indexes.py`). Cost: moving a source = re-embed + recompile |
| A3 | Registry `wiki/_meta/domains.json` is admin-write-only (CLI/REST); compiler, router, query only read it. `general` implied, never listed, never removable. Absent file ⇒ `{general}` | no concurrent registry writers; the root index's `## Domains` renders from the registry alone |
| A4 | Routing once per source in `IngestPipeline.process()` between extract and embed: explicit `domain=` (immutable `SourceMeta.domain`) → no call; registry `{general}` → no call; else one `route_domain` call over title + head of the extracted text; persisted to `raw/{id}/routing.json` (derived, rewritable); recompiles never re-route | chunk and lexical writes know their domain; zero added cost for a single-domain wiki. Routing on the compiler's summary was rejected: it would move stage 1 out of the compiler and re-plumb its cost accounting |
| A5 | Poor fit → `general` + `suggested_domain` on `routing.json`/`SourceStatus`; never auto-created; `llmwiki domains suggestions` aggregates (admin `raw/` listing, never on ingest) | accepting = `domains add` + `domains reassign` |
| A6 | Query scope: explicit `domain=` wins; else `QUERY_DOMAIN_POLICY` = `all` (default, fan-out, no LLM) / `routed` (one `route_domain` call, ≤ `QUERY_MAX_DOMAINS`) / `general` | one domain ⇒ today's exact call sequence under all three; graph tools take `domain`; `agent_step.md` lists domains |
| A7 | Slugs unique *within* a domain; `SearchHit.domain`, `Citation.domain`, `PageFrontMatter.domain` (rendered only when ≠ general) | `get_page(slug, domain="general")`; Obsidian cross-domain `[[slug]]` ambiguity accepted (§21.11) |
| A8 | "Higher-quality synthesis" = scheduled per-domain job, op `synthesize_domain`, ≤ `SYNTHESIS_MAX_PAGES` page reads, writes `overview.md` (new `PageType` `overview`, gets a gist vector); never on ingest | compose `synthesize` service beside `lint` |

**B — Hybrid retrieval + rerank**

| # | Decision | Consequence |
|---|---|---|
| B1 | `LexicalIndex` protocol (L1 `lexical/`) with `VectorStore`'s shape: `upsert(index, ids, texts, metadata)`, `query(index, text, k)`, `delete_by_source(index, source_id)`; backends `sqlite` (FTS5, default), `memory`, `none` | one index *name* addresses both halves of a scope; `none` is the exact pre-Phase-2 path |
| B2 | One SQLite file per index name at `{LEXICAL_DB_PATH}/{index-name}.sqlite`; derived, rebuildable from `raw/*/extracted.md` + manifests (`llmwiki lexical rebuild`); missing DB ⇒ `[]` + WARNING; startup asserts FTS5 is compiled in | no new service or credential; not shared across replicas (none exist) |
| B3 | RRF (`K=60`) over dense + lexical per scope, pool `HYBRID_POOL_K=20` per list; rerank after fusion, inside each layer, input ≤ `RERANK_MAX_CANDIDATES=40`, text = gist/chunk metadata text (no page reads) | wiki-first remains code; per-question cost bounded by config |
| B4 | `SearchHit.score` = last stage's score; new `dense_score` keeps the cosine; **the `WIKI_CONFIDENCE` gate reads `dense_score`** (falls back to `score` when `None`); `none`/`none` ⇒ byte-identical pass-through | `test_wiki_is_searched_before_the_chunk_index` / `test_chunk_index_is_untouched_when_the_wiki_answers` keep their meaning |
| B5 | `Reranker` protocol (L1 `rerank/`): `workers_ai` (`@cf/baai/bge-reranker-base`, same `CF_*` creds), `fake`, `none`; error ⇒ fused order + WARNING | never a hard failure on the query path |

**C — Cost, usage, worker**

| # | Decision | Consequence |
|---|---|---|
| C1 | Ledger keys `wiki/_meta/cost/{YYYY-MM}/{DD}-{writer}.jsonl`, `writer` ∈ `api, cli, mcp, backfill, eval, synth`; legacy `cost.jsonl` read-through until `llmwiki usage --migrate` | processes never share a key ⇒ RMW safe; a read lists one month prefix |
| C2 | `CostRecord += domain, kind` (`compile\|ingest\|query\|eval\|synthesis\|route`); query cost via `MeteredLLM` (L1 decorator around whatever `factory.llm_client` builds) + a `contextvars` collector opened in `QueryAgent.answer()`; `tools.answer` appends, sets `Answer.cost_usd` | closes §20.1's gap with zero call-site changes; fallback if LangGraph context propagation changes: `usage` list in `QueryState` with an `add` reducer |
| C3 | `pipeline/worker.py`: bounded pool (`WORKER_THREADS`) + one `threading.Lock` per domain around embed + compile; `WORKER_MODE=threads\|inline`; pending marker `status/_pending/{id}` at capture, removed at done/failed, resubmitted on startup | replaces `BackgroundTasks` in REST and channels; MCP/CLI stay synchronous but take the same lock; no broker |
| C4 | Alerts on every ledger append from running totals (seeded by one month read; refreshed on rollover / 60 s); dedup `wiki/_meta/cost/alerts.json`; `Notifier` (L1 `notify/`): `telegram` (reuses `TELEGRAM_BOT_TOKEN`, needs `ALERT_TELEGRAM_CHAT_ID`), `log`, `fake` | WARNING always; one Telegram message per threshold per period |
| C5 | Monthly hard cap pauses post-capture processing (`SourceState` += `paused`); capture, search, answer keep working; worker re-checks every 60 s, drains at rollover or when raised; query spend counts, never blocks | alert text says paused sources are not yet searchable |
| C6 | `GET /usage` bearer; `GET /dashboard` bearer header **or** `?token=`; server-rendered HTML, inline SVG, no JS, no new dependency | user decision 2026-09-18 |

**D — Multimodal**

| # | Decision | Consequence |
|---|---|---|
| D1 | Separate optional protocol `VisionLLMClient.describe(*, op, system, prompt, images: list[ImageInput], model=None, max_tokens=None, temperature=None) -> LLMResponse`; **not** an `images=` kwarg on `complete()` | P5 byte-identical; `RoutingLLMClient` validates at startup that `describe_image`'s adapter implements `describe`, fails by name otherwise |
| D2 | Extractors stay LLM-free: `PdfExtractor`/`ImageExtractor` put rendered PNG pages in `ExtractedDoc.extra["vision_pages"]` with `<!-- vision:p{n} -->` placeholders; `pipeline/describe.py` fills them via `op="describe_image"` before `extracted.md` is written; `vision_pages` bytes stripped afterwards | text-first; text embeddings only; chunking, lexical, compiler, backfill see described text |
| D3 | Descriptions cached at `raw/{id}/vision.json` (derived, rewritable) | recompile/backfill never re-pay |
| D4 | `VISION_MODE=off\|auto\|always` (default `off` = today incl. the scan `ExtractionError`), `VISION_MAX_PAGES_PER_SOURCE=8`; selection by text density / image area (§21.6.5); vision usage joins the per-source records `_check_budget` sees | per-source vision spend is configuration |

**Cross-cutting**

| # | Decision | Consequence |
|---|---|---|
| X1 | Three new ops — `route_domain`, `describe_image`, `synthesize_domain` → `KNOWN_OPS` = 10; prompts `route_domain_source.md`, `route_domain_query.md`, `describe_image.md`, `synthesize_domain.md` (SKILL.md format) | `config/ops.py` rows; the drift guard scans `wiki/router.py`, `wiki/synthesis.py`, `pipeline/describe.py` and matches `.describe(op=…)` as well as `.complete(op=…)` |
| X2 | Defaults follow "defaults are production" (`test_defaults_are_the_cloud_backends`): `LEXICAL_BACKEND=sqlite`, `RERANKER_BACKEND=workers_ai`, `WORKER_MODE=threads`; "off until asked": `VISION_MODE=off`, alerts `0`, `QUERY_DOMAIN_POLICY=all`; `tests/conftest.py` pins `memory`/`none`/`inline`/`off`; `--offline` forces the same | an upgraded deployment gets hybrid + rerank on; one variable each to turn off |
| X3 | MCP = six canonical tools **+ `list_domains`** (read-only); registry mutation is CLI/REST only | user decision 2026-09-18 |

### 21.3 Data model (L0, all additive with defaults)

| File | Change |
|---|---|
| `models/source.py` | `SourceMeta.domain: str \| None = None`; `SourceState += "paused"`; `SourceStatus += domain, suggested_domain, vision_calls`; new `ImageInput(media_type, data: bytes)`, `DomainAssignment(domain, confidence, suggested_domain, reason, explicit, routed_at)` |
| `models/page.py` | `PageType += "overview"`; `PageFrontMatter.domain: str = "general"`; new `Domain(name, description, created)`, `DomainRegistry(domains)` (`general` implied); `PageGist` unchanged |
| `models/chunk.py` | `SearchHit += domain="general", dense_score, lexical_score`; `ChunkMetadata` unchanged |
| `models/plan.py` | `CostRecord += domain="general", kind="compile"`; `CostSummary += by_op, by_domain, by_day, by_kind, top_sources, window`; `Answer += cost_usd, domains`; `Citation += domain`; `CompileResult += domain`; new `SynthesisResult`, `WorkerStatus(paused, reason, queued, in_flight)`, `AlertState` |

### 21.4 Key layout (`storage/layout.py` — still the only key builder)

```
GENERAL = "general";  _DOMAIN_RE = ^[a-z0-9][a-z0-9-]{0,31}$     # ≤32 so "{base}-{d}" fits Vectorize's 64-char name cap
reserved: general, domains, _meta, index, concepts, entities, sources, overview

domain_prefix(d)            ""         if general else f"wiki/domains/{d}/"
gists_key(d)                GISTS_KEY  if general else f"wiki/domains/{d}/_meta/gists.json"
index_key(d)                INDEX_KEY  if general else f"wiki/domains/{d}/index.md"
overview_key(d)             f"{domain_prefix(d)}overview.md"
wiki_page(slug, type, domain=GENERAL)  today's path for general; f"wiki/domains/{d}/{folder}/{safe}.md" otherwise
domain_index_name(base, d)  base       if general else f"{base}-{d}"          # Vectorize + lexical
DOMAINS_KEY                 "wiki/_meta/domains.json"
raw_routing(id)             f"raw/{id}/routing.json"     raw_vision(id)  f"raw/{id}/vision.json"    # derived, rewritable
pending_key(id)             f"status/_pending/{id}"
COST_PREFIX "wiki/_meta/cost/";  cost_key(day, writer)  f"wiki/_meta/cost/{YYYY-MM}/{DD}-{writer}.jsonl"
ALERTS_KEY                  "wiki/_meta/cost/alerts.json"
lexical_db_path(root, index_name)  root / "lexical" / f"{index_name}.sqlite"
```

`raw/` invariants hold: `original.*`/`meta.json` write-once; `routing.json`/`vision.json` join
`extracted.md` as derived objects. The compiler still never lists `wiki/`.

- Registry: `{"version": 1, "domains": {"ml-systems": {"name", "description", "created"}}}`.
- `wiki/index.md` = today's `render_index(general manifest)` plus, only when other domains exist,
  `## Domains` — `[[domains/{d}/index|{d}]] - {description}` per domain — from the registry only.
- `wiki/domains/{d}/index.md` = the same mechanical index over that domain's manifest, titled
  `Index - {d}`, with a `[[index|All domains]]` back-link.
- Pre-Phase-2 data: nothing moves. No `routing.json` ⇒ `general`; no `domains.json` ⇒ general only;
  lexical DBs built once (`llmwiki lexical rebuild`) or serve `[]`; legacy `cost.jsonl` read-through.
  `scripts/migrate_phase2.py --check|--apply` bundles lexical rebuild + ledger migration +
  `ensure_index` for registered domains.

### 21.5 Module layout by layer (⊕ new, △ changed)

```
L0  models/source.py △ page.py △ chunk.py △ plan.py △

L1  storage/layout.py △        domain/ledger/vision/pending keys; check_domain()
    vector/base.py △           + ensure_index(index)   memory.py △ no-op   vectorize.py △ create + metadata indexes + readiness poll
    lexical/ ⊕                 base.py LexicalIndex · sqlite.py (FTS5, porter, query sanitiser) · memory.py (tiny BM25)
    rerank/ ⊕                  base.py Reranker · workers_ai.py · fake.py
    notify/ ⊕                  base.py Notifier · telegram.py (httpx POST sendMessage) · log.py · fake.py
    llm/base.py △              + VisionLLMClient protocol; complete() UNCHANGED
    llm/metering.py ⊕          MeteredLLM(inner) + collect_usage() contextvar; forwards describe() when inner has it
    llm/anthropic_client.py △  describe(): image blocks (base64) before the text block; system/caching unchanged
    llm/langchain_client.py △  describe(): HumanMessage content list with image_url data: URIs
    llm/router.py △            describe() dispatch by op; startup validation that describe_image's adapter has describe
    llm/fake.py △              describe(); _synthesize rows for route_domain, synthesize_domain
    llm/routing_config.py △    KNOWN_OPS += 3
    extractors/base.py △       get_extractor(modality, *, vision_mode="off", max_pages=8); "image" branch
    extractors/pdf.py △        page selection heuristic, extra["vision_pages"], placeholders (off ⇒ today, byte-identical)
    extractors/image.py ⊕      ImageExtractor: original bytes → one vision page (JPEG/PNG/WebP; GIF first frame)

L2  wiki/domains.py ⊕          load/save registry · DomainScope(name) → gists_key/index_key/page_key/index names · resolve_scopes()
    wiki/router.py ⊕           DomainRouter(llm, registry): route_source(doc) / route_query(question) — op="route_domain"
    wiki/gists.py △            load_gists/save_gists/write_index(store, manifest, domain=GENERAL, registry=None)
    wiki/pages.py △            read_page/write_page(..., domain=GENERAL)
    wiki/compiler.py △         compile_source(doc, force=False, domain=GENERAL, prior_costs=()); lexical kwarg; _sync_gist writes lexical row; _append_costs → CostLedger
    wiki/ledger.py ⊕           CostLedger(store, writer): append(records, *, domain, kind, source_id) · read(since, until, domain) · summarize() · running totals · read_cost_ledger() compat
    wiki/alerts.py ⊕           CostAlerts(ledger, notifier, settings): evaluate() · capped() · dedup at ALERTS_KEY
    wiki/synthesis.py ⊕        synthesize_domain(...) — bounded reads, op="synthesize_domain"
    wiki/lint.py △             lint_wiki(store, dry_run, domain=None); per-domain attribution; `unknown_domain` finding
    agent/retrieval.py ⊕       retrieve_layer(kind, query, vector, scopes, k, *, vectors, lexical, reranker, settings) — RRF + rerank
    agent/query.py △           search/answer(query, k, domain=None); collect_usage() around graph.invoke; _build_context per-domain manifests (memoised)
    agent/graph.py △           QueryState += scopes, domains; retrieve via retrieve_layer; gate on dense_score
    agent/toolkit.py △         search_wiki/search_chunks(query, k, domain=None); get_page(slug, domain="general")
    chains/prompts/            route_domain_source.md ⊕ route_domain_query.md ⊕ describe_image.md ⊕ synthesize_domain.md ⊕ agent_step.md △

L3  pipeline/ingest.py △       capture(..., domain=None) → SourceMeta.domain + pending marker; process(): extract → describe → route → embed(domain, +lexical) → compile(domain) under the domain lock; paused check; prior_costs
    pipeline/describe.py ⊕     describe_pending_pages(doc, llm, settings, cache) → (doc, usage records)
    pipeline/worker.py ⊕       CompileWorker: submit/run_now/recover/status; WORKER_MODE threads|inline; domain locks; pause/drain
    pipeline/lexical_rebuild.py ⊕ rebuild(store, lexical, settings, domain=None)

L4  tools.py △                 domain= on search_wiki/get_page/ingest_source/compile_update/list_concepts/lint_wiki/answer · list_domains/upsert_domain/remove_domain/reassign_source/domain_suggestions · enqueue_source · worker_status · usage_summary · rebuild_lexical · synthesize
    eval/run.py △              flattens cost_usd + domains; evaluators unchanged

L5  api/routes.py △            domain params; GET /domains, PUT/DELETE /domains/{name} (bearer; DELETE 409 unless empty or ?force); GET /usage (bearer); GET /worker; background → tools.enqueue_source
    api/dashboard.py ⊕         GET /dashboard (bearer header or ?token=) — f-string template, inline SVG, no JS
    api/app.py △               lifespan: worker.recover() on start, drain-stop on shutdown
    mcp/server.py △            + list_domains; optional domain on search_wiki/get_page/list_concepts/ingest_source
    cli.py △                   domains {list,add,update,remove,suggestions,reassign} · usage [--month --domain --migrate --json] · lexical rebuild · synthesize --domain · worker · --domain on ingest/ask/search/concepts
    channels/telegram.py △     leading "#<domain>" in caption/text = explicit domain when registered; enqueue_source
    channels/email.py △        "[domain]" subject prefix likewise; enqueue_source

factory.py △    lexical_index() · reranker() · notifier() · compile_worker() · ledger(writer) · llm_client wraps MeteredLLM (all via _cached)
config.py △     §21.8        config/ops.py △ + 3 rows        docker-compose.yml △ + synthesize (ops profile)
scripts/        bootstrap_indexes.py △ (thin caller of ensure_index) · migrate_phase2.py ⊕ · probe_domain_routing.py ⊕ · backfill.py △ (--domain) · smoke_flow.py △ · eval_answer.py △ (prints gate/score distributions)
```

`test_layering.py`: add L1 rows `lexical`, `rerank`, `notify` (same banned set as `websearch`) and
add each name to every other layer's banned set exactly as `websearch` was; `wiki` and `agent` may
import the `lexical`/`rerank` protocols (as they import `vector` today); `pipeline` receives
protocols and builds nothing. Transports still import only `tools/models/config/wiki/factory`.

### 21.6 Algorithms

**21.6.1 Ingest** (`pipeline/ingest.py:process` after A4/C3/C5/D2):

```
capture(url|file|text, domain?)  → raw/original + meta(.domain) + status queued + status/_pending/{id}
worker lane (or inline):
  [cap]     if alerts.capped(): status paused (reason names COST_HARD_CAP_MONTHLY_USD); park; retry each 60 s
  extract   get_extractor(modality, vision_mode, max_pages).extract() → text (+ placeholders + extra.vision_pages)
  describe  vision.json cache → describe_image for missing pages (≤ cap) → stitched text → extracted.md
  route     explicit? registry=={general}? else DomainRouter.route_source(doc) → routing.json (+ suggested_domain)
  embed     chunks → vectors.upsert(domain_index_name(chunks, d)); lexical.upsert(same name)
  compile   with domain_lock(d): Compiler.compile_source(doc, domain=d, prior_costs=describe+route usage)
  record    ledger.append(...) → alerts.evaluate() → notifier; status done/failed; delete pending marker
```

`compile_update` reads `routing.json` for the domain; `_already_compiled` is checked against that
domain's manifest before any spend, as today.

**21.6.2 Retrieval** (`agent/retrieval.py`):

```
resolve_scopes(explicit, policy, registry, router):
    explicit → [explicit] (unknown → 404/ValueError)     registry == {general} → [general]
    policy general → [general]   policy all → all registered (general first)
    policy routed → router.route_query(q) ⊆ registry, ≤ QUERY_MAX_DOMAINS; empty → all

retrieve_layer(kind ∈ {gists, chunks}, query, vector, scopes, k):
    for scope in scopes:
        dense   = vectors.query(scope.index(kind), vector, k=HYBRID_POOL_K)      # today's call shape
        lexical = lexical.query(scope.index(kind), query, k=HYBRID_POOL_K) or []
        tag hits with scope.name; dense hits get dense_score = score
    if no lexical and no reranker and len(scopes) == 1: return dense[:k]          # byte-identical pass-through
    fused = RRF(all lists, K=60), dedup by id, keep dense_score / lexical_score
    pool  = fused[:RERANK_MAX_CANDIDATES]
    if reranker: try order = reranker.rerank(query, [gist text | chunk text[:1000]], top_n=k); score = rerank score
                 except: WARNING, keep fused order
    return top k

wiki-first (graph.retrieve and QueryAgent.search, same shape as today):
    W = retrieve_layer(gists); strong = [h for h in W if (h.dense_score ?? h.score) >= WIKI_CONFIDENCE]
    if not strong: C = retrieve_layer(chunks); used_rag_fallback = True
```

`QueryAgent.search()` today checks only `hits[0].score`; align it to the graph's `any(...)`.
`_build_context` loads one manifest per distinct hit domain (memoised per call) and reads pages with
`domain=hit.domain`; headers add `(domain: d)` for non-general only.

**21.6.3 Domain routing** (`wiki/router.py`): `route_source(doc)` — prompt = registry
(`name — description`, `general` last as "anything else") + title + first ~1 500 chars; forced schema
`{domain: enum[registry ∪ general], confidence: 0–1, suggested_domain: str, reason: str}`;
`confidence < DOMAIN_ROUTE_MIN_CONFIDENCE (0.6)` ⇒ `general`, suggestion kept. `route_query(q)` —
same op, `route_domain_query.md`, ≤ `QUERY_MAX_DOMAINS`. `scripts/probe_domain_routing.py
--offline|--live` prints the decision per fixture/real source for calibration.

**21.6.4 Worker** (`pipeline/worker.py`): `CompileWorker(settings, process, capped)`;
`ThreadPoolExecutor(WORKER_THREADS)`; `submit(id)` → pending marker → future; each job acquires
`locks[domain]` (created on demand) around embed + compile only, so extraction/description/routing
overlap across sources while one domain compiles serially. `run_now(id)` (CLI, MCP,
`compile_update`) runs inline in the caller but takes the same lock. `recover()` at lifespan start
lists `status/_pending/` (bounded by in-flight work) and resubmits. Paused jobs sit in a `deque`
drained by a 60 s timer. `health()["worker"]` and `GET /worker` return `WorkerStatus`.
`WORKER_MODE=inline` ⇒ `submit == run_now` (tests, CLI); routes keep
`background.add_task(tools.enqueue_source, id)` so `TestClient` semantics stay deterministic.

**21.6.5 Vision** (`extractors/pdf.py`, `extractors/image.py`, `pipeline/describe.py`): per PDF
page `chars = len(text)`, `img_area` = Σ image rect area / page area (`page.get_images(full=True)` +
`get_image_rects`), `vec = len(page.get_cdrawings())` only when `chars < 1500`. Candidate if
`chars < VISION_MIN_CHARS_PER_PAGE (200)` or `img_area ≥ VISION_IMAGE_AREA_RATIO (0.25)` or
`vec ≥ 200`. If ≥ 80 % of pages are candidates by the chars rule ⇒ "scanned document": first
`VISION_MAX_PAGES_PER_SOURCE` pages; else candidates by `img_area` desc up to the cap. Render
`get_pixmap(dpi=110)`, longest side ≤ 1 568 px, PNG. Text keeps `### Page n` + its text and appends
`<!-- vision:p{n} -->`; over-cap candidates get `> [page n: figure-heavy, not described —
VISION_MAX_PAGES_PER_SOURCE reached]`. `describe_pending_pages` replaces each placeholder with
`#### Described content (page n)\n\n{markdown}`, writes `vision.json`, sets
`extra.vision_calls/vision_skipped`, strips `vision_pages`. Still-empty text ⇒
`ExtractionError("no text and no describable pages")`. `describe_image.md`: transcribe visible text
verbatim, tables as markdown tables, figures as `### Figure` blocks with axes/trends/numbers, no
speculation.

**21.6.6 Cost, usage, alerts** (`wiki/ledger.py`, `wiki/alerts.py`): compiler `_record` →
`kind=compile`; `process()` → route + describe usage `kind=ingest`; `tools.answer` → collector
records `kind=query` (+ `Answer.cost_usd`); `tools.judge_answer` → `eval`; synthesis → `synthesis`.
`MeteredLLM` forwards `response.usage` only when a collector is open, so compile-side calls are never
double-counted. `usage_summary(since, until, domain)` → `CostSummary` with `by_model` (existing),
`by_op`, `by_domain`, `by_day`, `by_kind`, `top_sources` (10). Alerts: after each append compare
`day_total`/`month_total` with the three thresholds (0 = off); dedup
`{"daily_warned": "2026-09-18", "monthly_warned": "2026-09", "capped": "2026-09"}`; a hard-cap event
also says "processing paused until <1st of next month> or cap raised; N sources queued". Dashboard:
month/today totals vs thresholds, by-day SVG bars, tables by domain/kind/op/model, top sources,
worker + alert state.

**21.6.7 Synthesis** (`wiki/synthesis.py`): manifest → concept/entity gists ranked by
`len(sources)` desc then `updated` desc → read ≤ `SYNTHESIS_MAX_PAGES` bodies → one
`synthesize_domain` call (manifest one-liners + bodies) → `{title, gist, body}` →
`write_page(overview, expected_version)` + gist row + gist vector + lexical row.
`tools.synthesize(domain)`, `POST /synthesize/{domain}` (bearer), `llmwiki synthesize
--domain|--all`, compose `synthesize` (ops profile, weekly cron next to `lint`).

### 21.7 Surfaces

| Surface | Additions |
|---|---|
| `tools.py` | `domain=` on `search_wiki`, `get_page`, `ingest_source`, `compile_update`, `list_concepts`, `lint_wiki`, `answer`; `list_domains`, `upsert_domain`, `remove_domain`, `reassign_source`, `domain_suggestions`; `enqueue_source` (replaces `process_source` for async callers; `process_source` stays); `worker_status`; `usage_summary` (`cost_summary` kept as a month-to-date alias); `rebuild_lexical`; `synthesize` |
| MCP | **`list_domains`** (seventh tool); optional `domain` on `search_wiki`, `get_page`, `list_concepts`, `ingest_source` |
| REST | `domain` on `POST /ingest` body, `/upload` form, `/search`, `/answer`, `/concepts`, `/page/{slug}`, `/lint`; `GET /domains`; `PUT /domains/{name}`, `DELETE /domains/{name}` (bearer); `GET /sources/{id}` shows domain/suggestion/vision_calls; `GET /usage?month=&domain=` (bearer); `GET /dashboard?month=` (bearer or `?token=`); `GET /worker`; `POST /synthesize/{domain}` (bearer); `/healthz` += `domains`, `worker`, `budget`, `lexical`/`reranker`/`vision` backends |
| CLI | `ingest --domain`, `search/ask/concepts --domain`, `domains list\|add\|update\|remove\|suggestions\|reassign`, `usage [--month --domain --migrate --json]`, `lexical rebuild [--domain]`, `synthesize --domain\|--all`, `worker`, `lint [--domain]` |
| Channels | Telegram: leading `#<domain>`; email: `[domain]` subject prefix — explicit domain when registered, else routed |
| Scripts | `migrate_phase2.py --check\|--apply`, `probe_domain_routing.py`, `bootstrap_indexes.py` (thin), `backfill.py --domain` |
| Compose | `synthesize` service (ops profile); lexical DBs live under the already-mounted `./.data` |

### 21.8 Environment variables and ops rows

| Variable | Default | Read by |
|---|---|---|
| `DOMAIN_ROUTING` | `auto` (`off` = everything general, never call the router) | `pipeline/ingest.py` |
| `DOMAIN_ROUTE_MIN_CONFIDENCE` | `0.6` | `wiki/router.py` |
| `QUERY_DOMAIN_POLICY` / `QUERY_MAX_DOMAINS` | `all` (`routed`/`general`) / `2` | `wiki/domains.resolve_scopes` |
| `SYNTHESIS_MAX_PAGES` | `10` | `wiki/synthesis.py` |
| `LEXICAL_BACKEND` / `LEXICAL_DB_PATH` | `sqlite` (`memory`/`none`) / `{LOCAL_STORAGE_PATH}/lexical` | `factory.lexical_index`, `lexical/sqlite.py` |
| `RERANKER_BACKEND` / `RERANKER_MODEL` | `workers_ai` (`fake`/`none`) / `@cf/baai/bge-reranker-base` | `factory.reranker`, `rerank/workers_ai.py` |
| `HYBRID_POOL_K` / `RERANK_MAX_CANDIDATES` | `20` / `40` | `agent/retrieval.py` |
| `WORKER_MODE` / `WORKER_THREADS` | `threads` (`inline`) / `4` | `pipeline/worker.py` |
| `COST_ALERT_DAILY_USD` / `COST_ALERT_MONTHLY_USD` / `COST_HARD_CAP_MONTHLY_USD` | `0` (off) | `wiki/alerts.py` |
| `NOTIFY_BACKEND` / `ALERT_TELEGRAM_CHAT_ID` | `log` (`telegram`/`fake`) / empty | `factory.notifier`, `notify/telegram.py` (reuses `TELEGRAM_BOT_TOKEN`) |
| `VISION_MODE` / `VISION_MAX_PAGES_PER_SOURCE` / `VISION_MIN_CHARS_PER_PAGE` / `VISION_IMAGE_AREA_RATIO` | `off` (`auto`/`always`) / `8` / `200` / `0.25` | extractors, `pipeline/describe.py` |

Code constants: `RRF_K=60`, render DPI 110, 1 568 px bound. Ops rows (`config/ops.py`):
`route_domain` (cheapest, temp 0.0, 512 tok); `describe_image` (`openrouter` /
`google/gemini-2.5-flash-lite` — the current default `z-ai/glm-5.3-flash` is text-only; verify the
exact OpenRouter id at P2-D1 — temp 0.2, 2048 tok); `synthesize_domain` (the `create_page` model,
temp 0.7, 8192 tok). Every variable goes to `.env.example` with the milestone that reads it.

### 21.9 Milestones

Each: `pytest` green, `ruff check . && mypy` clean, `scripts/smoke_flow.py --offline` and
`scripts/eval_answer.py --offline` green, `HISTORY.md` entry, `.env.example` in sync.

| # | Milestone | Exit criterion |
|---|---|---|
| **P2-0** | Docs: design v1.7 §4.10 (+§4.2/§4.4/§4.6/§5), this §21, `TODOS.md`, `CLAUDE.md`, `HISTORY.md` | reviewed text — **landed 2026-09-18** |
| **P2-1** | Foundations: models, `Settings` + `.env.example`, 3 ops rows + 4 prompts, `FakeLLM` rows, layering rows, `wiki/ledger.py` (day×writer keys, legacy read-through), `MeteredLLM` + factory wrap, `CostRecord.domain/kind`, `Answer.cost_usd`. (`KNOWN_OPS` grows per call-site milestone, not here - the drift guard equates it with what the code calls) | no behaviour change; `read_cost_ledger` compat test; `Answer.cost_usd` populated offline — **landed 2026-09-19** (507 tests) |
| **P2-A1** | Domain-aware `layout.py`, `wiki/domains.py`, registry CRUD (tools/REST/CLI), `domain=` through `pages`/`gists`/`compiler`/`lint`/`get_page`/`list_concepts` (default `general`), root index `## Domains`, `ensure_index`, `list_domains` MCP tool; also explicit `domain=` on every capture surface + `routing.json` (pulled forward from A2 so a second domain is testable end to end) | **byte-identity test**: general-only output identical to a Phase-1 fixture; `domains add` creates indexes; MCP = 7 tools — **landed 2026-09-19** (560 tests) |
| **P2-A2** | `wiki/router.py`, `routing.json`, `process()` reorder, `SourceMeta.domain`, `domain=` on every capture transport (+ channel prefixes), `backfill.py --domain`, probe script | zero `route_domain` calls with a general-only registry; explicit domain never routes — **landed 2026-09-19** (579 tests; `KNOWN_OPS`=8) |
| **P2-C1** | `pipeline/worker.py`, pending markers, recovery, `enqueue_source`, routes/channels switched, `WORKER_MODE`, `paused` plumbing, `/worker` | per-domain serialization + cross-domain overlap test; recovery test; route outcomes unchanged — **landed 2026-09-19** (591 tests) |
| **P2-A3** | Query-side scopes (`resolve_scopes`, policy), `agent/retrieval.py` (dense-only, multi-scope merge), toolkit + `_build_context` domain awareness, `SearchHit.domain`, `Citation.domain`, `agent_step.md` domain hint | wiki-first tests unchanged; fan-out bounded test — **landed 2026-09-19** (606 tests) |
| **P2-B1** | `lexical/` (protocol, sqlite, memory), writes in `_embed`/`_sync_gist`/`delete_source`, `lexical rebuild`, RRF fusion, `dense_score` gate | pass-through identity when `none`; rebuild-from-raw equivalence; FTS injection test — **landed 2026-09-19** (644 tests; conftest pins `LEXICAL_BACKEND=none`, not `memory`) |
| **P2-B2** | `rerank/` (protocol, workers_ai, fake), rerank after fusion, error fallback, eval prints score distributions, golden set gains exact-term rows | eval offline exit 0; candidate-cap test — **landed 2026-09-19** (657 tests; score-distribution printing deferred to P2-Z) |
| **P2-C2** | `usage_summary` dims, `GET /usage`, `llmwiki usage`, `/dashboard`, `notify/`, `wiki/alerts.py`, hard cap → worker pause, `usage --migrate` | alert dedup; pause-not-capture; ledger window-bounded — **landed 2026-09-19** (677 tests) |
| **P2-A4** | `wiki/synthesis.py`, `overview` page type, CLI/REST/compose | bounded reads; never called from `process()` — **landed 2026-09-19** (686 tests; `KNOWN_OPS`=9) |
| **P2-D1** | `VisionLLMClient` on adapters/router/fake/metered, `describe_image`, `pipeline/describe.py`, `vision.json`, `ImageExtractor`, `get_extractor` branch | Telegram photo → `done` end-to-end offline; cache prevents a second call — **landed 2026-09-19** (703 tests; `KNOWN_OPS`=10) |
| **P2-D2** | PDF page selection + caps + stitching; `SourceStatus.vision_calls`; budget accounting | scanned fixture: `off` still raises (existing test kept), `auto` describes ≤ cap; figure-heavy fixture selects by area — **landed 2026-09-19** (713 tests) |
| **P2-Z** | `migrate_phase2.py`; technical document (§2.2, §6, §7, §8, new **§12** — the Phase 2 code-path map, in place of the planned §3.8/§5/§10 edits), `README.md`, `scripts/README.md`, `CLAUDE.md`, `docs/phase2-testing-guide.md` (manual test plan); `smoke_flow --offline` exercises a second domain + hybrid + fake reranker + fake vision | docs match code — **landed 2026-09-19** (714 tests) |

Ordering: A1/A2 give every later piece its `domain`; C1 (worker) precedes B1 so lexical writes are
already serialized; A3 precedes B so scopes exist before fusion; C2 lands once all spend sources
exist; D last because it is independent and the only piece touching adapters. Each milestone is
shippable and behaviour-compatible under its default switch.

### 21.10 Testing plan

**No regressions.** The five load-bearing tests are untouched: `test_layering.py` gains rows only;
`test_compiler_no_full_scan.py` unchanged (general layout byte-identical, `Compiler(...)` positional
signature preserved, new deps keyword-optional); `test_every_citation_resolves_to_a_real_raw_object`,
`test_importing_the_registry_imports_no_provider_sdk` (`sqlite3` is stdlib) and
`test_tool_loop_is_bounded_by_agent_max_tool_calls` unchanged.
`test_every_transport_can_ingest_all_five_source_kinds` gains a `domain` case.

**Isolation fixtures.** `tests/conftest.py`'s `settings` fixture gains `lexical_backend="memory",
reranker_backend="none", worker_mode="inline", vision_mode="off", notify_backend="fake"`; a fourth
autouse fixture pins `WORKER_MODE=inline` for any default `Settings()`.

**Changed-shape (announced, not weakened):**
`test_tools_and_mcp.py::test_mcp_exposes_exactly_the_six_canonical_tools` → six + `list_domains`;
`test_channels.py` and route tests patching `tools.process_source` → `tools.enqueue_source`;
`test_config.py::test_defaults_are_the_cloud_backends` → new defaults;
`test_lint_and_cost.py::test_cost_summary_aggregates_by_model` keeps writing legacy `COST_KEY`
(proves read-through) plus a sibling for partitioned keys; `test_extractors.py`'s scanned-PDF-raises
test kept as the `VISION_MODE=off` case; `test_agent.py` `QueryAgent.search` gate alignment (`any`
vs `[0]`). No test file is deleted.

**New tests per code path:** `test_layout_domains.py` (general identity:
`wiki_page(s,t,"general")==wiki_page(s,t)`, `gists_key("general")==GISTS_KEY`,
`domain_index_name(b,"general")==b`; name validation; nesting; `cost_key`); `test_domains.py`
(registry, implied `general`, `resolve_scopes` matrix, root `## Domains` only with ≥ 1 other domain,
byte-identical general index/page vs a Phase-1 fixture); `test_router.py`; `test_worker.py`
(interleaving with events, inline, recovery, pause/drain, `run_now` lock); `test_lexical.py`
(contract over sqlite+memory like `test_vector_contract.py`; FTS injection `"foo" OR bar)(`;
stemming; absent DB); `test_lexical_rebuild.py`; `test_retrieval.py` (pass-through identity, RRF,
`dense_score` gate, rerank order/fallback/cap, multi-scope tagging); `test_rerank.py` +
`test_notify.py` (payload shapes via `httpx.MockTransport`); `test_ledger.py` (keys, window,
read-through, migrate, threaded appends, aggregations); `test_alerts.py`; `test_metering.py`
(collector only inside `collect_usage()`; real graph + `FakeLLM`); `test_vision.py` (adapter message
shapes, router validation, cache hit/miss, cap, empty-after-describe); `test_extractors.py` additions
(image extractor; three synthetic PDFs: text-only / scanned / figure-heavy); `test_synthesis.py`;
`test_routes.py` / CLI / MCP additions (domain params, `/domains` CRUD + auth + 409, `/usage`,
`/dashboard` token paths, `/worker`).

**New permanent scale guards — `tests/unit/test_phase2_scale_guards.py`** (added to `CLAUDE.md`'s
load-bearing list when P2-A1 lands):

- compiling into domain `d` never `get`s `wiki/_meta/gists.json` nor any `wiki/domains/{other}/`
  key, and never lists `wiki/`;
- compiling into `general` reads at most one extra object (`domains.json`);
- `CostLedger.read(month)` lists exactly one prefix and gets only that month's keys (three months
  seeded);
- query fan-out: `routed` ≤ `QUERY_MAX_DOMAINS` vector queries per layer; `all` exactly one per
  domain per layer;
- rerank input ≤ `RERANK_MAX_CANDIDATES` regardless of scope count;
- vision calls per source ≤ `VISION_MAX_PAGES_PER_SOURCE` for a 40-page scan;
- hard cap: capture writes `raw/`, `answer` returns, `process` parks as `paused`;
- `route_domain` never called with a general-only registry; `synthesize_domain` never called from
  `process()`.

**Verification, end to end.** After each milestone: `pytest`; `ruff check . && mypy`;
`python scripts/smoke_flow.py --offline`; `python scripts/eval_answer.py --offline`;
`python scripts/probe_query_graph.py --offline --matrix`; `docker compose --profile test run --rm
pytest` (also proves FTS5 in the image). Live (`docs/phase2-testing-guide.md`, written at P2-Z):
`domains add` ×2 → `bootstrap_indexes.py --check` shows the new indexes; one source per routing path
(explicit, routed, general + suggestion); `lexical rebuild`; an exact-term question dense misses and
hybrid finds (score distributions printed by `eval_answer.py`); a scanned PDF with
`VISION_MODE=auto`; `usage` + `/dashboard`; a deliberately low `COST_HARD_CAP_MONTHLY_USD` → `paused`
→ Telegram alert → raise cap → drained.

### 21.11 Risks and open items

| Risk / item | Mitigation / status |
|---|---|
| Per-domain indexes make reassignment expensive (A2) | `domains reassign` = `delete_by_source` in the old index, re-embed + recompile in the new; old pages keep the source in `sources` until lint flags it (same posture as `delete_source`). Curated registries reassign rarely |
| Local FTS5 state on R2 deployments (B2) | fresh box: `lexical rebuild` (minutes at 100k chunks) or dense-only until then; `health()` reports per-index row counts + last rebuild; it is the one admin op that lists `raw/` |
| Reranker on the query path (B5) | second Cloudflare call, ~100–300 ms/layer; error → fused order. **Open:** whether a strong rerank score should also *raise* wiki confidence — decide from the printed distributions on the real corpus |
| `contextvars` across LangGraph's executor (C2) | verified by `test_metering.py` against the real graph; fallback (state reducer) recorded in C2 |
| Hard cap semantics (C5) | paused sources are not searchable until resumed — stated in the alert and `/worker` |
| Suggestions accumulate silently (A5) | `domains suggestions` lists them; a name repeating ≥ N times is the human cue |
| Obsidian `[[slug]]` ambiguity across domains (A7) | deferred until observed; the fix would be qualified links for non-general pages in `append_sources_section`/prompts |
| `WORKER_THREADS` also bounds concurrent LLM calls | providers with tight limits want `2`; documented in `.env.example` |
| Vectorize readiness for a brand-new domain | `ensure_index` polls ≤ 30 s; the first compile still has the manifest's exact-slug path, as today |
| `describe_image` model id | `google/gemini-2.5-flash-lite` on OpenRouter — verify the exact id at P2-D1; `VISION_MODE=off` until the operator flips it |
| Scope (13 milestones) | each independently shippable and behaviour-compatible under its defaults (`general`-only, `VISION_MODE=off`, alerts `0`); `LEXICAL_BACKEND`/`RERANKER_BACKEND` default on per the repo's "defaults are production" convention and are one variable each to turn off |

**Deferred (recorded in `TODOS.md`):** custom mobile app; compile-correctness golden set as the
measurement for A8; lexical in the compiler's `_locate`; Qdrant as an alternative hybrid store;
qualified cross-domain wikilinks; `general` renaming; ledger retention; sharing one Telegram client
between `notify/` and `channels/telegram.py`.

---

*End of Phase 0.5 Implementation Plan*
