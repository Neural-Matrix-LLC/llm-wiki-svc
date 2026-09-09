# HISTORY

Every code, configuration, or architectural change to this repository, in
reverse-chronological order. See `CLAUDE.md` for the rule this file follows.

---

## 2026-09-09 — Make LangChain + every provider SDK a core dependency

**Goal:** `uv sync` (per README's Quickstart) was installing only the base
`[project.dependencies]` group — none of `langchain-core` or the five
provider integrations (`langchain-openai`, `langchain-google-genai`,
`langchain-nvidia-ai-endpoints`, `langchain-deepseek`, `langchain-openrouter`)
were present, since they lived behind `optional-dependencies` extras. The user
asked for these to stop being optional: a plain `uv sync` should install all
of them, with no per-provider extra to remember.

**Root cause (why the gap was hit in the first place):** `pyproject.toml` put
`langchain-core` and every provider integration behind `optional-dependencies`
extras (`langchain`, `openai`, `google`, `nvidia`, `deepseek`, `openrouter`,
`all-providers`), on the design rationale that non-Anthropic providers should
"cost nothing until you ask for it." README.md's Quickstart told new
contributors to run bare `uv sync`, which — by design — skips every optional
extra, so nobody following the README ever got a LangChain package (or, since
`dev` is also an extra, `pytest`/`mypy`/`ruff` either).

**Implementation detail:**
- `pyproject.toml`: moved `langchain-core>=0.3`, `langchain-openai`,
  `langchain-google-genai`, `langchain-nvidia-ai-endpoints`,
  `langchain-deepseek`, and `langchain-openrouter` into
  `[project.dependencies]`. Deleted the now-empty `langchain`, `openai`,
  `google`, `nvidia`, `deepseek`, `openrouter`, and `all-providers` extras.
  `langsmith` (optional tracing) is untouched — it is independent of provider
  choice and stays opt-in. Dropped the redundant `langchain-core>=0.3` entry
  from the `dev` extra.
- `.env.example`: provider table's `install` column now reads `(built in)`
  for every provider; removed the stale `pip install "llmwiki[all-providers]"`
  line.
- `src/llmwiki/llm/providers.py`: corrected the module docstring, which
  claimed importing it "pulls in no provider SDK at all: `pip install
  llmwiki` stays free of them" — no longer true now that every SDK is a core
  dependency. The accurate framing: the per-call lazy import inside
  `build()`/`load_class()` keeps *importing this module* cheap, not the
  install itself. Removed the now-meaningless `ProviderSpec.extra` field and
  reworded `load_class`'s `RuntimeError` — an `ImportError` now means a
  broken/incomplete install (there is no separate extra to `pip install`
  anymore), so the message points at reinstalling llmwiki instead of naming a
  now-nonexistent extra.
- `CLAUDE.md`: updated "Current State" (dropped "each behind its own extra";
  noted the 2026-09-09 change and that `LLM_PROVIDER` switching needs no
  install step), removed the stale `uv pip install -e ".[openai]"` snippet
  under "Working in this repository", and reworded the
  `test_importing_the_registry_imports_no_provider_sdk` load-bearing-test
  description (it guards lazy *importing*, not "the extras" — those no longer
  exist).
- `README.md`: Quickstart now runs `uv sync --extra dev` instead of bare
  `uv sync`, so a new contributor also gets `pytest`/`mypy`/`ruff` (this extra
  gap predates today's change but was found and fixed in the same pass).

**Related files:** `pyproject.toml`, `uv.lock`, `.env.example`, `CLAUDE.md`,
`README.md`, `src/llmwiki/llm/providers.py`, `tests/unit/test_providers.py`.

**Test coverage:**
- No regressions: full `pytest` run — 289 passed, 1 skipped (unrelated
  `langsmith`-extra skip), 6 deselected (integration, opt-in). One pre-existing
  failure (`tests/unit/test_extractors.py::test_fetch_video_title_reads_oembed`)
  reproduces identically on a clean `git stash` checkout — confirmed unrelated
  to this change, left untouched.
- `ruff check .` and `mypy` both clean.
- `python scripts/smoke_flow.py --offline` — SMOKE PASS.
- Renamed and reworded two tests in `tests/unit/test_providers.py` to match
  the new (no-extras) failure message, rather than adding new tests — the
  registry's shape and lazy-import contract didn't change, only what happens
  when an import fails:
  - `test_a_missing_extra_names_the_pip_command` →
    `test_a_broken_install_names_the_distribution_and_suggests_reinstalling`
    (asserts the message now says "reinstall llmwiki" instead of naming a
    per-provider extra).
  - `test_the_factory_reports_a_missing_extra_rather_than_failing_at_the_socket`
    → `test_the_factory_reports_a_broken_dependency_rather_than_failing_at_the_socket`
    (same assertions, renamed for accuracy).
  - No tests removed: `test_registry_keywords_match_the_installed_class`'s
    `pytest.importorskip` simply stops skipping now that every provider
    module is always present — a strict improvement in coverage, not a
    behavior change.
  - The load-bearing `test_importing_the_registry_imports_no_provider_sdk`
    (`tests/unit/test_providers.py`) is untouched and still passes: it guards
    lazy per-call imports, which is orthogonal to whether the SDKs are
    optional or core dependencies.
