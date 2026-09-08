# HISTORY

Every code, configuration and architectural change is logged here before it is
considered complete, per `CLAUDE.md`.

---

## 2026-09-07 — SKILL.md-format prompts and query-agent skill invocation (R4-R5)

**Goal.** Execute the two milestones `implement-plan-v1.4.md` §19.6 held back
from the 2026-09-05 R1-R3 change: give the five `chains/prompts/*.md` files
real Agent Skill frontmatter (R4), and give the query agent - only the query
agent, not the compiler - genuine runtime skill selection over that discovered
skill set (R5). Design in `llmwiki-KB-design_v1.4.md` §4.8.2.

**Implementation detail.**

*R4 - frontmatter.* All five prompt files
(`summarize_source.md`/`plan_compile.md`/`create_page.md`/`patch_page.md`/
`answer_query.md`) gained a `---` YAML block naming `name` (kebab-case,
matching the filename) and one-line `description`. `chains/prompts_loader.py`
now parses each file with `frontmatter.loads()` (already a hard dependency,
used identically by `wiki/pages.py`) and returns `.content.strip()` - the
`@cache`d body every existing caller already expected. A file with no
frontmatter still loads (`frontmatter.loads` on plain text returns empty
metadata and the whole text as content), which is what keeps this a
non-breaking, back-compatible change to a loader four call sites depend on.

*R5 - query-agent skill invocation.* New `agent/skills.py`:
`discover_skills(skills_dir)` scans `*.md` under `Settings.agent_skills_dir`
(`AGENT_SKILLS_DIR`, default `./skills`, added inert in R1-R3) and returns a
`dict[str, Skill]` keyed by frontmatter `name`. Unlike `routing_config.py`'s
startup-fatal validation, a malformed or unnamed file is logged and skipped,
not fatal - the query agent must keep answering even if one skill file is
broken. `agent/query.py:QueryAgent.answer()` calls it after retrieval and
context-building (both unchanged): no skills discovered runs the pre-R5 single
fixed-prompt call (`_answer_with_fixed_prompt`), byte-identical to before;
skills discovered calls `_answer_with_skills`, which:

1. Asks the model to choose a skill or ordered chain of up to `MAX_SKILL_CHAIN`
   (3) skills, via one `answer_query`-op call forced by a `schema` whose
   `skills` property enumerates the discovered names - the same forced-tool-
   call mechanism the compiler already uses for structured output
   (`SUMMARY_SCHEMA`/`PLAN_SCHEMA`/`PAGE_SCHEMA` in `wiki/compiler.py`), so
   this needed no change to `LLMClient`'s protocol.
2. Validates the response against the discovered set; a choice naming nothing
   valid is retried once (`SKILL_SELECTION_ATTEMPTS = 2`), then falls back to
   the fixed skill (design v1.4 §4.8.2, plan §19.9 item 2's defined failure
   mode) - a malformed tool call or a hallucinated skill name must not be a
   hard failure on a user-facing query.
3. Runs the chosen skill(s) in order, each as one more `answer_query`-op call
   whose `system` is that skill's frontmatter body; a step after the first
   also receives the previous step's output appended to its prompt. The final
   step's text is the answer.

Citation resolution (`source_exists`, building `citations` from the retrieved
wiki/chunk hits) sits **above** all of this in `answer()`, unchanged - it does
not know or care which skill, or how many calls, produced the text.

**Related files.** `src/llmwiki/chains/prompts/*.md` (all five),
`src/llmwiki/chains/prompts_loader.py`, `src/llmwiki/agent/skills.py` (new),
`src/llmwiki/agent/query.py`, `src/llmwiki/llm/fake.py` (`_synthesize` gained a
branch recognizing the skill-selection `schema` shape, so offline runs -
`scripts/smoke_flow.py --offline` included - exercise the real control flow),
`skills/answer_query.md` (new), `skills/compare_concepts.md` (new),
`tests/conftest.py`, `tests/unit/test_config.py`, `.env.example` (no variable
change; `AGENT_SKILLS_DIR`'s comment updated - it is now read),
`docs/implement-plan-v1.4.md` (§19.6/§19.7.2/§19.7.3 status updated to landed),
`docs/llm-wiki-technical-document.md` (§5.8 rewritten from roadmap to current
state), `CLAUDE.md` (test count).

**Three deliberate deviations from the letter of the plan**, each recorded in
`implement-plan-v1.4.md` §19.6 as well:

1. Skill selection and per-step generation reuse the `answer_query` op rather
   than a new op. §19.5 item 2 explicitly allows either "the `answer_query`
   call (or a preceding call)"; reusing it keeps `KNOWN_OPS`, `config/ops.py`
   and the AST drift guard (`test_routing_config.py::
   test_known_ops_matches_every_real_call_site`) untouched. Trade-off: every
   skill-related call shares one cost-ledger op label instead of each getting
   its own - acceptable for now, and exactly the kind of granularity §19.9
   item 3 already left open for LangSmith.
2. A real `skills/` directory ships, populated with two skills
   (`answer-query` - the same rules as `chains/prompts/answer_query.md` - and
   `compare-concepts`, for comparison questions), rather than landing inert.
   §19.9 item 1 settled the *location*, not a promise to leave it empty; §4.8.2
   frames R5 as a real behaviour change to the query agent, the same posture
   R3 already took removing `COMPILE_EXECUTOR_MODEL`.
3. `tests/conftest.py` gained `_isolate_agent_skills_dir` (mirrors R1's
   `_isolate_llm_routing_config`): an autouse fixture pointing
   `AGENT_SKILLS_DIR` at a guaranteed-absent path so every test in the suite
   *except* the new `test_agent_skill_invocation.py` (which opts back in
   explicitly) keeps exercising the pre-R5 fixed-prompt path. This is what
   keeps `test_agent.py` - including the load-bearing
   `test_every_citation_resolves_to_a_real_raw_object` - passing completely
   unmodified, per the R5 exit criteria.

**Test coverage.** Full suite: 289 passed, 1 skipped, 6 deselected
(integration, opt-in), plus the one pre-existing unrelated failure already
noted in the 2026-09-07 technical-document entry below
(`test_extractors.py::test_fetch_video_title_reads_oembed` - present before
this change, untouched by it). `ruff check .` and `mypy` both clean.
`python scripts/smoke_flow.py --offline` passes end to end, now actually
exercising skill discovery/selection against the shipped `skills/` catalog.

- **Rewritten, not obsoleted:**
  `tests/unit/test_config.py::test_agent_skills_dir_defaults_outside_src` now
  `monkeypatch.delenv("AGENT_SKILLS_DIR")` before building `Settings`, bypassing
  the new isolation fixture on purpose (same reasoning as
  `test_routing_config_paths_default_under_a_config_directory`), and its
  docstring no longer claims the setting is unread.
- **New:** `tests/unit/test_prompts_loader.py` (5 tests) - every real prompt
  file's frontmatter round-trips (`name`/`description`), `load_prompt()`
  strips it and returns exactly `frontmatter.loads(...).content.strip()`, a
  file with no frontmatter still loads, a missing prompt still names the file.
  `tests/unit/test_agent_skill_invocation.py` (11 tests) - `discover_skills()`
  finds every frontmatter'd file / returns empty for an absent directory /
  skips an unnamed file / skips a duplicate name; `QueryAgent.answer()` with
  no discoverable skills makes exactly one fixed-prompt call; the model's
  skill choice is honored (asserted on the recorded `system` prompt of the
  generation call); a two-skill chain carries the first step's output into the
  second step's prompt; an invalid choice retries once then falls back to the
  fixed skill; and the citation-resolution contract re-run specifically
  against a skill-invoked answer.

---

## 2026-09-07 — New developer-support technical document

**Goal.** Give a developer joining this repository (to support, extend, bug
fix, or test it) a single map document, distinct from the design doc
(why-shaped-this-way) and the implementation plan (packaging roadmap):
module-to-module workflows named by class/file, an extension guide per seam
(new LLM provider, new op, new extractor, new storage/vector backend, the
multi-provider routing config), and the full API reference (Python
`tools.py` layer, REST, MCP, CLI, config), plus an explicit note on the R4/R5
skill-invocation work that is planned but not yet implemented.

**Implementation detail.** New `docs/llm-wiki-technical-document.md`, written
by reading the actual current source tree (not the aspirational
`packages/agentkit-*` layout in `implement-plan-v1.4.md`, which has not been
executed - §10 of the new document states this gap explicitly so a reader
does not conflate the plan with the code). Content was derived from: the
L0-L5 layer ladder and `ALLOWED_EXCEPTIONS` in
`tests/unit/test_layering.py`; the five-stage compiler
(`wiki/compiler.py`), the wiki-first/RAG-fallback query agent
(`agent/query.py`), and the ingest pipeline (`pipeline/ingest.py`); the
single-provider vs. R1-R3 multi-provider LLM routing paths in `factory.py`,
`llm/router.py` and `llm/routing_config.py`; the six-tool `tools.py` surface
and its REST (`api/routes.py`), MCP (`mcp/server.py`) and CLI (`cli.py`)
transports; `storage/layout.py`'s key scheme; and `.env.example`/
`config/providers.py.example`/`config/ops.py.example` for the configuration
reference.

**Related files.** `docs/llm-wiki-technical-document.md` (new). Read but not
modified: `docs/llmwiki-KB-design_v1.4.md`, `docs/implement-plan-v1.4.md`,
`README.md`, `pyproject.toml`, `.env.example`, `config/*.example`,
`src/llmwiki/**` (all modules), `tests/unit/test_layering.py`.

**Test coverage.** Documentation-only change; no code, config, or test
behaviour changed. No existing test is obsoleted or newly required. Verified
the document's factual claims against a live `pytest` run at the time of
writing: 267 passed, 1 skipped, 6 deselected, plus the one pre-existing,
unrelated failure (`test_extractors.py::test_fetch_video_title_reads_oembed`)
- recorded in the new document's §8 so a reader does not mistake it for a
regression they caused.

---

## 2026-09-06 — Routing config couldn't read `.env`, its own env vars were wired wrong, and `--offline` wasn't

**Goal.** Fix four bugs found while actually activating the R1-R3 multi-provider
routing feature (below) for real, with OpenRouter: (1) a provider's
`api_key_env` set only in `.env` (not the real shell) resolved to empty; (2)
the documented override variables `LLMWIKI_PROVIDERS_CONFIG`/
`LLMWIKI_OPS_CONFIG` did nothing at all; (3) this repository's own test suite
had no isolation from a real `config/providers.py`/`config/ops.py` sitting in
the checkout, unlike its `_env_file=None` isolation from a real `.env`; (4)
`scripts/smoke_flow.py --offline` no longer guaranteed no network calls once a
real routing config existed - and, in the course of investigating, actually
made five real calls to OpenRouter (`z-ai/glm-5.3-flash`, small token counts,
recorded in `.data/wiki/_meta/cost.jsonl` with real timestamps) despite the
flag, before the fix landed.

**Root cause.**
1. `llm/routing_config.py` resolved `api_key_env`/`base_url_env` with a bare
   `os.environ.get(...)`. Nothing in this codebase ever calls
   `dotenv.load_dotenv()` - `Settings` (config.py) reads `.env` through
   pydantic-settings' own internal parser, which populates only `Settings`'
   fixed model fields and never exports anything into the real process
   environment. A variable that exists only in `.env` (the documented,
   intended way to hold it) was therefore invisible to `routing_config.py`.
2. `Settings.llm_providers_config`/`llm_ops_config` had no `validation_alias`.
   pydantic-settings auto-derives an env var name from the field name alone
   (`LLM_PROVIDERS_CONFIG`, no "WIKI") - the deliberately-prefixed names
   written into `.env.example` and `implement-plan-v1.4.md` §19.8 were a
   different string entirely and were silently accepted-and-ignored
   (`extra="ignore"`), with the hardcoded default used instead. Caught by
   directly testing the documented env var against a real `Settings()` - unit
   tests never had, because every one of them passed the path as a Python
   kwarg, never as an env var.
3. `tests/conftest.py` had `_env_file=None` sprinkled through the suite for
   isolation from a real `.env`, but nothing equivalent for
   `llm_providers_config`/`llm_ops_config`: those are plain `Path` fields whose
   *default* is checked for existence on disk by
   `routing_config.load_routing_config`, a check with no `_env_file` gate at
   all. Once real `config/providers.py`/`config/ops.py` existed in this
   checkout (for actual OpenRouter use), every test building a default
   `Settings()` silently flipped into routed mode.
4. `scripts/smoke_flow.py --offline` set `LLM_PROVIDER=fake` but never touched
   `llm_providers_config`/`llm_ops_config`. `factory._build_llm_client` checks
   routing config *before* `cfg.llm_provider` at all (by design, §19.2) - so a
   real routing config outranked `--offline`'s intent completely, silently.

**Implementation detail.**
- `llm/routing_config.py`: new `_dotenv_fallback()` (via `dotenv_values()`,
  which - unlike `load_dotenv()` - never mutates `os.environ` and returns
  `{}` for a missing file) and `_env()`, used everywhere `api_key_env`/
  `base_url_env` are resolved. Precedence: real `os.environ` wins over `.env`,
  matching pydantic-settings' own source order. Also removed a debug line
  (added ad hoc while diagnosing this) that logged a resolved secret value
  verbatim at DEBUG level, and a stray `logging.basicConfig()` call that would
  have fought with `config.configure_logging`'s single point of control.
- `config.py`: `llm_providers_config`/`llm_ops_config` gained
  `validation_alias=AliasChoices("LLMWIKI_..._CONFIG", "llm_..._config")` -
  the documented env var name now actually works, and the plain field name
  (used by every constructor call in the test suite) still does too.
- `tests/conftest.py`: new autouse `_isolate_llm_routing_config` fixture -
  points both env vars at a guaranteed-nonexistent path under `tmp_path` for
  every test, unconditionally. A test that explicitly passes
  `llm_providers_config=`/`llm_ops_config=` is unaffected (an init kwarg
  always outranks an env var in pydantic-settings' source order).
- `scripts/smoke_flow.py`: `--offline` now also sets both env vars to a
  guaranteed-nonexistent path, for the same reason.
- `config/providers.py.example` (and the real, already-copied
  `config/providers.py`): two comment lines exceeding ruff's line length -
  invisible while the file was `.example` (ruff only checks `.py`), surfaced
  the moment it was copied to a real `.py` file - rewrapped.

**Related files.** `src/llmwiki/llm/routing_config.py`, `src/llmwiki/config.py`,
`tests/conftest.py`, `scripts/smoke_flow.py`, `config/providers.py.example`,
`config/providers.py` (the user's real copy).

**Test coverage.** Full suite: 267 passed, 1 skipped, 6 deselected, plus the
same one pre-existing unrelated failure. `ruff check .` and `mypy` clean.
`scripts/smoke_flow.py --offline` re-run after the fix and confirmed back to
the deterministic `FakeLLM` output (no new `cost.jsonl` entries with a real
model id).
- New: `tests/unit/test_routing_config.py::test_credentials_are_resolved_from_dotenv_when_not_a_real_env_var`,
  `::test_a_real_env_var_wins_over_dotenv` (root cause 1);
  `tests/unit/test_config.py::test_documented_env_var_names_actually_configure_the_routing_paths`
  (root cause 2).
- Rewritten: `tests/unit/test_config.py::test_routing_config_paths_default_outside_src_and_absent`
  → `test_routing_config_paths_default_under_a_config_directory`: now
  explicitly clears the two env vars and `chdir`s into an empty `tmp_path`
  before asserting the default, rather than asserting against this
  checkout's real state (which the new autouse fixture masks anyway, and
  which was the false assumption that made the original version of this test
  pass by accident rather than by design).

**Operational note.** Five real OpenRouter calls happened during this session
before the `--offline` fix landed, against whatever credit/quota
`OPENROUTER_API_KEY` draws on - check the OpenRouter dashboard if that
matters. `.data/wiki/_meta/cost.jsonl` (local smoke-flow scratch state, not
committed) now has five real-model entries mixed in with fake-mode ones from
before and after; harmless, but flagged rather than silently cleaned, since
it's a historical ledger by design and deleting entries from it wasn't asked
for.

## 2026-09-05 — Application-specific multi-provider/per-op LLM routing (R1-R3)

**Goal.** Let a deployment configure several LLM providers at once and route
each pipeline op (`summarize_source`, `plan_compile`, `create_page`,
`patch_page`, `answer_query`) to its own provider/model/temperature/max_tokens
- without that shape becoming part of `agentkit-llm`'s shareable, semver-
protected `LLMConfig` contract (implement-plan-v1.4.md §7.3). Design in
`llmwiki-KB-design_v1.4.md` §4.8 (v1.5); implementation plan in
`implement-plan-v1.4.md` §19 (v1.1), milestones R1-R3. **R4 (SKILL.md
frontmatter + `load_prompt` parsing) and R5 (query-agent skill invocation) are
deliberately not part of this change**, per explicit instruction -
`chains/prompts/*.md` and `chains/prompts_loader.py` are untouched; they land
together later. The one exception is `Settings.agent_skills_dir`
(`AGENT_SKILLS_DIR`, R5's skill directory), added now, inert, so its location
outside `src/llmwiki` is locked in ahead of R4/R5 rather than improvised then.

**Implementation detail.**
- New `config/providers.py` + `config/ops.py` (repo root, outside
  `src/llmwiki`, not part of the installed package) - a committed, secret-free
  manifest of providers (naming which env var carries each one's credentials)
  and a per-op routing table. **Absence of `config/providers.py` is the
  fallback switch**: nothing changes for a deployment that never creates one,
  which is why the real repository still has no `config/` directory and the
  existing test suite needed no behaviour-changing edits.
- New `llm/routing_config.py`: loads and validates both files
  (`load_routing_config`). A provider whose named credential env var is unset
  is dropped from the active set, silently (not an error). A missing/duplicate
  known op, or an op naming an inactive/unknown provider, fails loudly by name
  at startup - same posture as `Settings.require`. `KNOWN_OPS` is the
  frozenset of the five ops the codebase actually calls; a dedicated AST-scan
  test breaks if a new call site adds an op with no matching row.
- New `llm/router.py` (`RoutingLLMClient`): implements the existing
  `LLMClient` protocol, dispatching `complete(op=...)` to the provider adapter
  the matching `config/ops.py` row names, with that row's model/temperature/
  max_tokens - an explicit call-site value still overrides it.
- `llm/base.py`: `LLMClient.complete()`'s `model`/`max_tokens`/`temperature`
  are now `Optional`, defaulting to `None` rather than a hardcoded `2048`/
  `1.0`. Each concrete adapter (`AnthropicLLM`, `LangChainLLM`) gained
  `default_max_tokens`/`default_temperature` constructor params (alongside the
  existing `default_model`) and resolves `None` to them. This is what let call
  sites drop `max_tokens=`/`temperature=` entirely without silently ignoring
  `Settings.llm_max_tokens`/`llm_temperature` in the fallback path - the
  constructors default to `2048`/`1.0` (matching the old hardcoded values), so
  every existing adapter unit test needed no change.
- `factory.py`: `_build_llm_client` now checks `routing_config.
  load_routing_config()` first; `None` falls through to the existing
  single-provider logic, otherwise `_build_routed_llm_client` builds one
  concrete adapter per **provider actually referenced by an op row** (not
  every active provider) and wraps them in a `RoutingLLMClient`. Extracted
  `_construct_provider_client` (fake/anthropic/LangChain construction, no
  logging, no env reads) so both paths share it. `llm_client()`'s cache key
  extended with the two config paths.
- **`COMPILE_EXECUTOR_MODEL` / `Settings.compile_executor_model` removed.** Its
  one job - a stronger model for `create_page`/`patch_page` - is now just
  those ops' own `config/ops.py` rows. A deployment relying on the old
  variable must define `config/ops.py`; there is no equivalent left in the
  zero-config fallback. `tools.py::health()`'s `models.compile_executor` key
  is removed with it (nothing else read it).
- `config.py`: added `llm_providers_config` (default `./config/providers.py`),
  `llm_ops_config` (default `./config/ops.py`), and `agent_skills_dir`
  (default `./skills`, **not yet read by any code path** - the location is
  locked in now, ahead of R5, so it isn't improvised later; configurable via
  `AGENT_SKILLS_DIR` per the confirmed design decision that it lives outside
  `src/llmwiki` like the two paths above).
- Call sites simplified: `agent/query.py`'s and `wiki/compiler.py`'s five
  `llm.complete(...)` calls all drop `model=`/`max_tokens=`/`temperature=`,
  passing only `op=`/`system=`/`prompt=`/(`schema=`) - "config fully owns it."
  Debug log lines that named the resolved model were adjusted since the model
  is no longer known at the call site (routed mode may choose per-op).
- `.env.example`: `COMPILE_EXECUTOR_MODEL` line removed; added
  `LLMWIKI_PROVIDERS_CONFIG`, `LLMWIKI_OPS_CONFIG`, `AGENT_SKILLS_DIR` with an
  explanation of the fallback switch and where secrets actually live.

**Related files.** `src/llmwiki/llm/routing_config.py` (new),
`src/llmwiki/llm/router.py` (new), `src/llmwiki/llm/base.py`,
`src/llmwiki/llm/anthropic_client.py`, `src/llmwiki/llm/langchain_client.py`,
`src/llmwiki/llm/fake.py`, `src/llmwiki/factory.py`, `src/llmwiki/config.py`,
`src/llmwiki/agent/query.py`, `src/llmwiki/wiki/compiler.py`,
`src/llmwiki/tools.py`, `.env.example`, `tests/doubles.py`,
`docs/llmwiki-KB-design_v1.4.md` (→ v1.5), `docs/implement-plan-v1.4.md`
(→ v1.1, new §19).

**Test coverage.** Full suite: 264 passed, 1 skipped, 6 deselected (integration,
opt-in), plus the one pre-existing unrelated failure already noted in the entry
below (`test_extractors.py::test_fetch_video_title_reads_oembed` - present in
the working tree before this change, untouched). `ruff check .` and `mypy` both
clean. `python scripts/smoke_flow.py --offline` passes end to end. No test
referenced `compile_executor_model` at all, so its removal needed no test
deletions.

- **Obsolete, rewritten in place** (their premise - that the domain layer reads
  `Settings.llm_max_tokens`/`llm_temperature` and forwards them itself - is
  exactly what R3 removed):
  - `tests/unit/test_agent.py::test_answer_passes_settings_max_tokens_and_temperature`
    → `test_answer_leaves_model_and_sampling_params_to_the_configured_adapter`:
    now asserts the recorded call's `model`/`max_tokens`/`temperature` are all
    `None` (nothing forwarded), instead of asserting they equal a
    `Settings.model_copy`-injected value.
  - `tests/unit/test_compiler_behaviour.py::test_llm_max_tokens_and_temperature_reach_every_stage`
    → `test_compiler_leaves_model_and_sampling_params_to_the_configured_adapter`:
    same change, across all four compiler-stage calls, and now also doubles as
    the `COMPILE_EXECUTOR_MODEL` regression check (`create_page`/`patch_page`
    pass no `model=` either).
  - `FakeLLM.complete()` (`llm/fake.py`) and `ScriptedLLM.complete()`
    (`tests/doubles.py`) now record `max_tokens`/`temperature` **as passed**
    (`None` included) instead of pre-resolving to `2048`/`1.0` before
    recording - required for the two rewrites above to actually observe
    "nothing was passed," and itself covered by asserting on `.calls[...]`
    in both rewritten tests.
- **New:** `tests/unit/test_routing_config.py` (loader/validation - absent
  config, half-present config, active-set resolution, credential resolution
  from the named env var, missing/duplicate/inactive-provider/unknown-
  provider-kind errors, malformed module errors, and the
  `KNOWN_OPS`-vs-real-call-sites drift guard); `tests/unit/test_router.py`
  (`RoutingLLMClient` dispatch, op-row values, explicit-override precedence,
  schema passthrough).
- **Extended:** `tests/unit/test_factory.py` (routed-mode end-to-end build, an
  active-but-unused provider is never constructed, absent routing config
  leaves the fallback path untouched); `tests/unit/test_anthropic_client.py`
  and `tests/unit/test_langchain_client.py` (omitted `max_tokens`/
  `temperature` resolve to the adapter's configured defaults, not a
  hardcoded value; an explicit call-site value still overrides them);
  `tests/unit/test_config.py` (the two new config paths default outside
  `src/llmwiki` and are absent in this repo; `agent_skills_dir` default;
  `compile_executor_model` no longer exists).

## 2026-09-05 — `requirements.txt` missing the `openrouter` extra's packages

**Goal.** `llmwiki ingest` failed with `ModuleNotFoundError: No module named
'langchain_core'` even though `LLM_PROVIDER=openrouter` was configured in
`.env`. Root-caused and fixed so both the local dev venv and the Docker image
(built from `requirements.txt`, per `Dockerfile`) actually carry what
`LLM_PROVIDER=openrouter` needs.

**Root cause.** Two stacked problems:
1. The project's own `.venv` had only the bare package installed (no `dev` or
   provider extra) - `uv pip install -e ".[dev,openrouter]"` had never been
   run in it, so `langchain_core`/`langchain_openrouter`/`openrouter` were
   genuinely absent from the interpreter `llmwiki` runs under. A `pip list |
   grep lang` run in the same terminal appeared to show them installed, but
   that was a different Python environment on `PATH` (this `.venv` is
   uv-managed and has no `pip` binary at all), which is what made the failure
   look inconsistent.
2. `requirements.txt` - the lockfile `Dockerfile` builds from
   (`COPY requirements.txt ./` + `pip install -r requirements.txt`, before the
   app is even installed) - was frozen from a `.[dev]`-only environment. It
   carries `langchain-core` (a `dev` extra dependency, for the adapter tests)
   but none of the `openrouter` extra's packages, so a container built to run
   with `LLM_PROVIDER=openrouter` would hit the identical
   `ModuleNotFoundError` at first ingest/compile call. `uv.lock` (present,
   untracked) already had the correct resolution for every extra including
   `openrouter`; `requirements.txt` had just never been regenerated to match.

**Implementation detail.**
- Added the three packages the `openrouter` extra needs, at the versions
  `uv.lock` resolves, in the file's existing alphabetical order:
  `jsonpath-python==1.1.6` (dep of the `openrouter` SDK), `openrouter==0.10.8`
  (the SDK `langchain-openrouter` wraps), `langchain-openrouter==0.2.8`. Their
  own transitive deps (`httpx`, `httpcore`, `pydantic`, `jsonpath-python`
  itself) were already present at compatible pins - no other line changed.
- Ran `uv pip install -e ".[dev,openrouter]"` in the project's `.venv` so the
  local environment matches what `requirements.txt` now documents.

**Related files.** `requirements.txt`.

**Test coverage.**
- No regressions: `pytest -q` → 238 passed, 1 skipped, 6 deselected, plus one
  pre-existing unrelated failure
  (`test_extractors.py::test_fetch_video_title_reads_oembed`, already present
  in the working tree before this change - not touched).
- No new tests added: this is a dependency/lockfile fix, no new code path.
- Verified the actual failure mode directly: `langchain_core`,
  `langchain_openrouter`, and `openrouter` all import cleanly from `.venv`
  post-install (they raised `ModuleNotFoundError` beforehand). Did not force a
  full fresh ingest through the real `openrouter` API, since the source's raw
  object already existed and re-ingesting would either dedup-skip (no
  exercise of the code path) or spend real API budget - out of scope for
  verifying a dependency pin.

---

## 2026-09-05 — `LOG_LEVEL` and stdlib `logging`: which adapter/model backs ingest and compile

**Goal.** No way to see, during development, which LLM client/model a given
ingest or compile actually used, or where in the pipeline time was going -
Python's logging defaults meant nothing below WARNING was ever visible and
only one module (`api/app.py`) even had a logger. Add `LOG_LEVEL` and
`debug`/`info`/`warning`/`error` logging at the locations that answer that,
starting with (per explicit request) "which LLMClient backs wiki ingest,
compile, etc."

**Root cause.** N/A — new capability, not a bug fix.

**Implementation detail.**

- `Settings.log_level: str = "INFO"` (`config.py`).
- `configure_logging(level)` lives in `config.py` itself, not its own module:
  the transport layers (`api/`, `cli.py`) may only import
  `tools`/`models`/`config`/`wiki`/`factory` per
  `test_layering.py::test_transport_layer_only_calls_tools` (one of the four
  load-bearing tests - see `CLAUDE.md`), so a standalone `logging_config.py`
  module (its own layer, per that test's AST walk) would have failed it. It
  attaches one `StreamHandler` to the root logger with a format that includes
  module + line number (`%(name)s:%(lineno)d`) so a log line names its own
  location, and is idempotent - a second call only adjusts the level, never
  stacks a second handler.
- Both entry points call it once: `cli.main()` right after `load_settings()`,
  and `api/app.py` at module import (before its existing
  `logger = logging.getLogger(__name__)`).
- Every other touched module follows the one existing convention
  (`api/app.py`'s `logger = logging.getLogger(__name__)`):
  - `factory.py` - INFO once per adapter actually built: object store
    backend, vector store backend, embedder backend+model, and (the direct
    answer to the request) the LLM client's `provider=... model=...
    tracing=...`, logged at the exact chokepoint (`_build_llm_client`) every
    call path shares.
  - `wiki/compiler.py` - INFO at `compile_source` start/end (source id,
    title, created/patched/skipped/aborted counts); DEBUG at each of the 4
    `.complete()` call sites naming the model actually used
    (`llm_default_model` for summarize/plan, `compile_executor_model` for
    create/patch page); WARNING on a token-budget abort and on a version
    conflict (previously recorded only in `result.reason`, never logged).
  - `pipeline/ingest.py` - INFO at capture (source id, modality, duplicate)
    and at each `process()` state transition (extracting/embedding/
    compiling/done/failed) with elapsed time; the unexpected-exception catch
    now also logs at ERROR with a traceback instead of failing silently.
  - `agent/query.py` - DEBUG in `answer()`: model used, whether the RAG
    fallback engaged.
- `.env.example` documents `LOG_LEVEL` under a new "stdlib logging" section.

**Related files.** `src/llmwiki/config.py`, `src/llmwiki/cli.py`,
`src/llmwiki/api/app.py`, `src/llmwiki/factory.py`,
`src/llmwiki/wiki/compiler.py`, `src/llmwiki/pipeline/ingest.py`,
`src/llmwiki/agent/query.py`, `.env.example`.

**Test coverage.**

- No regressions: `pytest -q` → 234 passed, 5 skipped, 6 deselected, plus the
  same one pre-existing failure noted below. Also fixed, in passing, a real
  test-isolation bug this work exposed: `test_config.py::test_configure_langsmith_exports_the_env_vars`
  wrote `LANGSMITH_TRACING=true` etc. straight into `os.environ` (real
  application behavior, not `monkeypatch.setenv`) and never cleaned it up, so
  it leaked into every later test in the session - harmless until a test
  actually built a real `AnthropicLLM(tracing=True)` after it, which then hit
  an installed-package version mismatch (`langsmith`'s `wrap_anthropic`
  expects a legacy `.completions` attribute this `anthropic` SDK version no
  longer has). Fixed with a `try/finally` that `monkeypatch.delenv`s the four
  vars: that fix, plus keeping tests below explicit about
  `langsmith_tracing=False`, means the underlying `anthropic`/`langsmith`
  version mismatch is now dormant but not itself resolved - it would resurface
  if `LANGSMITH_TRACING=true` is set for real against this `anthropic` pin.
- New tests:
  - `tests/unit/test_logging_config.py` (new file) - `configure_logging` sets
    the root level, rejects an unrecognized `LOG_LEVEL`, is idempotent (no
    handler stacking on a second call), and is case-insensitive.
  - `tests/unit/test_config.py::test_defaults_are_the_cloud_backends` -
    extended to assert `log_level == "INFO"`.
  - `tests/unit/test_factory.py` (new file) - `caplog`-based:
    `test_llm_client_build_logs_the_provider_and_model` (the `fake` path) and
    `test_anthropic_client_build_logs_its_model` (the native path) both
    assert the provider/model actually land in a log record.
  - `tests/unit/test_compiler_behaviour.py::test_budget_abort_marks_the_source_rather_than_running_away` -
    extended with a `caplog` assertion that the abort is logged at WARNING.
- No obsolete tests.
- `ruff check .` and `mypy` both clean; `python scripts/smoke_flow.py
  --offline` → SMOKE PASS (the smoke script calls `tools.py` directly, not
  `cli.main()`, so it does not itself call `configure_logging` - out of scope
  for this change). Manually verified end-to-end with the real CLI
  (`LOG_LEVEL=DEBUG llmwiki --offline ingest --file ...`): factory logs each
  backend and the LLM provider/model at INFO, and DEBUG shows
  `summarize_source`/`plan_compile` using `llm_default_model` while
  `create_page`/`patch_page` use `compile_executor_model` - the exact
  visibility asked for.
- The one pre-existing failure, `tests/unit/test_extractors.py::test_fetch_video_title_reads_oembed`,
  predates and is unrelated to this change (see the LangSmith-tracing entry
  below for how that was verified).

---

## 2026-09-04 — `LLM_MAX_TOKENS` / `LLM_TEMPERATURE`, applied uniformly across adapters

**Goal.** `max_tokens` and sampling temperature were not configurable: the
Anthropic adapter and every LangChain provider silently used a hardcoded
`max_tokens=2048` default baked into the `LLMClient.complete()` protocol
signature, and temperature was never set at all (each provider's own default
applied, unexamined). Add `LLM_MAX_TOKENS`/`LLM_TEMPERATURE` as two more
provider-generic knobs, alongside `LLM_MODEL`.

**Root cause.** N/A — new capability, not a bug fix.

**Implementation detail.**

- `Settings` gains `llm_max_tokens: int = 2048` and `llm_temperature: float =
  1.0` (`config.py`) — `1.0` matches Anthropic's own default, so leaving the
  var unset changes nothing observable.
- Both are passed as **explicit kwargs at every `.complete()` call site**
  (`self.settings.llm_max_tokens` / `self.settings.llm_temperature`), the same
  idiom already used for `model=self.settings.llm_default_model` /
  `model=self.settings.compile_executor_model` — no hidden per-adapter
  default, five call sites touched: `wiki/compiler.py` (`_summarize`, `_plan`,
  `_create_page`, `_patch_page`) and `agent/query.py` (`answer`).
- `LLMClient.complete()` (`llm/base.py`) gains a `temperature: float = 1.0`
  parameter next to the existing `max_tokens: int = 2048`.
- `AnthropicLLM.complete()` puts `temperature` straight into the Messages API
  request dict alongside `max_tokens` — both are genuinely per-call fields on
  that API.
- `LangChainLLM`/`providers.build()`: temperature, like `max_tokens`, must be
  a **constructor** argument for LangChain chat models (invoke-time keywords
  differ across integrations, per the existing `max_tokens_arg` comment in
  `providers.py`) — `TEMPERATURE_ARG = "temperature"` is added as a single
  constant (no per-provider override needed, unlike `max_tokens_arg`), and
  `LangChainLLM`'s model cache key grows from `(model, max_tokens)` to
  `(model, max_tokens, temperature)`.
- `factory.py`'s LangChain `build(model, max_tokens)` closure becomes
  `build(model, max_tokens, temperature)`; the Anthropic branch needed no
  change (temperature there is per-call, not constructed).
- `FakeLLM` and the `ScriptedLLM` test double (`tests/doubles.py`) both now
  record `max_tokens`/`temperature` in `self.calls`, so propagation is
  assertable from tests instead of just accepted-and-ignored.
- `.env.example` documents both under the LLM section, explicitly noting they
  sit outside the four-name agentkit-llm contract (plan-v1.4 §7.6) but are
  still applied uniformly by every adapter.

**Related files.** `src/llmwiki/config.py`, `src/llmwiki/llm/base.py`,
`src/llmwiki/llm/anthropic_client.py`, `src/llmwiki/llm/langchain_client.py`,
`src/llmwiki/llm/providers.py`, `src/llmwiki/llm/fake.py`,
`src/llmwiki/factory.py`, `src/llmwiki/wiki/compiler.py`,
`src/llmwiki/agent/query.py`, `.env.example`, `tests/doubles.py`.

**Test coverage.**

- No regressions: `pytest -q` → 228 passed, 5 skipped, 6 deselected, plus the
  same one pre-existing failure noted below.
- New tests:
  - `tests/unit/test_config.py::test_defaults_are_the_cloud_backends` —
    extended to assert `llm_max_tokens == 2048`, `llm_temperature == 1.0`.
  - `tests/unit/test_anthropic_client.py::test_max_tokens_and_temperature_reach_the_request`
    and `::test_temperature_defaults_to_one` — a stubbed `messages.create`
    proves both values land in the request dict.
  - `tests/unit/test_providers.py::test_registry_keywords_match_the_installed_class`
    — extended to also require `TEMPERATURE_ARG` be accepted by every
    installed LangChain chat model class.
  - `tests/unit/test_providers.py::test_build_forwards_temperature_to_the_constructor`
    — new, confirms `build()` forwards `temperature` to the constructor.
  - `tests/unit/test_langchain_client.py::test_a_temperature_change_builds_its_own_chat_model`
    — new, mirrors the existing `max_tokens`-triggers-a-rebuild test.
  - `tests/unit/test_compiler_behaviour.py::test_llm_max_tokens_and_temperature_reach_every_stage`
    — new, proves every compiler-stage `.complete()` call carries
    `Settings.llm_max_tokens`/`llm_temperature`, not the protocol default.
  - `tests/unit/test_agent.py::test_answer_passes_settings_max_tokens_and_temperature`
    — new, same proof for the query agent's `answer_query` call.
- No obsolete tests.
- `ruff check .` and `mypy` both clean; `python scripts/smoke_flow.py
  --offline` → SMOKE PASS.
- The one pre-existing failure, `tests/unit/test_extractors.py::test_fetch_video_title_reads_oembed`,
  predates and is unrelated to this change (part of the user's own
  in-progress title-capture work; verified via `git stash` in the prior
  LangSmith-tracing entry above).

---

## 2026-09-04 — Optional LangSmith tracing, independent of `LLM_PROVIDER`

**Goal.** The project's only LLM observability was the measured cost ledger
(`CostRecord` → `wiki/_meta/cost.jsonl`) — token counts and USD, no visibility
into prompts, responses, or run structure. Add LangSmith tracing as an opt-in
layer on top, without weakening the "no provider SDK imported until asked for"
discipline the extras already enforce.

**Root cause.** N/A — new capability, not a bug fix.

**Implementation detail.**

* Four new `Settings` fields (`langsmith_tracing`, `langsmith_api_key`,
  `langsmith_project`, `langsmith_endpoint`), all optional, tracing off by
  default. `langsmith_api_key` is `SecretStr`, same as every other credential.
* `factory._configure_langsmith(cfg)` runs at the top of `_build_llm_client`,
  the one chokepoint every entry path (CLI, FastAPI, MCP) shares. It is a
  no-op unless `LANGSMITH_TRACING=true`; otherwise it calls
  `cfg.require("langsmith_api_key")` and exports `LANGSMITH_TRACING` /
  `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_ENDPOINT` to
  `os.environ`, which is what LangChain's own auto-instrumentation reads for
  every LangChain-routed provider (`openai`/`google`/`nvidia`/`deepseek`/
  `openrouter`).
* The default provider, `anthropic`, bypasses LangChain entirely by design
  (native adapter, for caching and measured cost) — so LangChain's
  auto-instrumentation would never see it. `AnthropicLLM` gained a
  `tracing: bool = False` constructor arg; when true it wraps the client with
  `langsmith.wrappers.wrap_anthropic`, imported only in that branch. A missing
  `langsmith` package with tracing requested raises a `RuntimeError` naming
  the extra to install, the same pattern `cfg.require` uses for a missing key.
* New `langsmith` extra in `pyproject.toml` (`pip install "llmwiki[langsmith]"`),
  independent of the provider extras. Added to `dev` too, since it was already
  a transitive dependency of `langchain-core` there — the tracing-wrap test
  needs it importable to monkeypatch.
* `.env.example` documents the four new variables and states explicitly that
  tracing is complementary to, not a replacement for, the cost ledger.

**Related files.** `src/llmwiki/config.py`, `src/llmwiki/factory.py`,
`src/llmwiki/llm/anthropic_client.py`, `pyproject.toml`, `requirements.txt`,
`.env.example`.

**Test coverage.**

1. *No regressions.* Full suite passes (the one failing test in
   `test_extractors.py::test_fetch_video_title_reads_oembed` predates this
   change — confirmed via `git stash` — and belongs to unrelated in-progress
   work on YouTube/web title capture; not touched here).
   `test_providers.py::test_importing_the_registry_imports_no_provider_sdk`
   still passes: `langsmith` stays a function-local import.
2. *Obsolete tests removed.* None.
3. *New tests.* `test_langsmith_tracing_defaults_off`,
   `test_langsmith_api_key_does_not_appear_in_repr`,
   `test_configure_langsmith_is_a_noop_when_tracing_is_off`,
   `test_configure_langsmith_requires_the_api_key`,
   `test_configure_langsmith_exports_the_env_vars` (`test_config.py`);
   `test_tracing_off_never_imports_langsmith`,
   `test_tracing_on_wraps_the_client`,
   `test_tracing_on_without_langsmith_installed_raises_a_clear_error`
   (new `tests/unit/test_anthropic_client.py`).
4. *Documented* here.

`.env` was not modified — only `.env.example`; `ruff` and `mypy` both clean.

---

## 2026-09-04 — Capture titles and titled source rows in index.md

**Goal.** YouTube and blog `raw/*/meta.json` were stored with an empty `title`
because URL ingest never passed `--title`. `wiki/index.md` then listed those
sources as `[[source_id]]`, which is the storage key, not something a person
reads.

**Root cause.** Capture wrote `SourceMeta.title` from the caller only.
Extractors already recovered a blog title from HTML into `ExtractedDoc`, but
`raw/` is append-only so that never reached `meta.json`. YouTube extraction
had no title source at all — the transcript JSON has no video title.

**Implementation detail.**

* Capture fills an empty title before the first `meta.json` write: HTML via
  `title_from_html` (trafilatura metadata, then `<title>`), YouTube via the
  public oEmbed endpoint. A caller-supplied title still wins. Failed lookups
  leave the field empty rather than failing the ingest.
* `wiki/index.md` source rows use an Obsidian alias: `[[source_id|title]]
  (`source_id`)`. Concept and entity rows are unchanged. The source gist no
  longer repeats the title.

**Related files.** `src/llmwiki/pipeline/ingest.py`,
`src/llmwiki/extractors/web.py`, `src/llmwiki/extractors/youtube.py`,
`src/llmwiki/wiki/gists.py`, `src/llmwiki/wiki/compiler.py`.

**Test coverage.**

1. *No regressions.* Existing extractor, ingest, and index tests still pass.
2. *Obsolete tests removed.* None.
3. *New tests.* `test_web_title_comes_from_the_html_when_meta_has_none`,
   `test_youtube_extract_uses_the_captured_title`,
   `test_fetch_video_title_reads_oembed`,
   `test_fetch_video_title_is_empty_when_oembed_fails`,
   `test_capture_fills_web_title_from_html`,
   `test_capture_fills_youtube_title_from_oembed`,
   `test_caller_title_wins_over_inferred_html_title`,
   `test_index_sources_display_title_and_keep_source_id`. Web extraction now
   also asserts the fixture `<title>`.
4. *Documented* here.

**`.env` was not modified.**

---

## 2026-09-04 — README Quickstart: YouTube and blog URL ingest

**Goal.** The Quickstart only showed a local PDF ingest. Add the CLI commands
for the two URL modalities the extractors already support (YouTube transcript
and web/blog HTML).

**Root cause.** Docs gap, not a code gap. `llmwiki ingest --url` was already
the capture path (`cli.py` → `tools.ingest_now` → `IngestPipeline.capture`).

**Implementation detail.** `README.md` Quickstart now has a second command
block: one YouTube URL and one blog URL, still under `--offline` so compile
stays on fake adapters. A short note states that `--url` still fetches over
the network. Stale "181 unit tests" count in that block was dropped in favour
of "unit tests" so the README does not drift from the gate again.

**Related files.** `README.md`.

**Test coverage.**

1. *No regressions.* Docs only; no code path changed.
2. *Obsolete tests removed.* None.
3. *New tests.* None — CLI ingest of `--url` is already covered by extractor
   and pipeline unit tests.
4. *Documented* here.

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
