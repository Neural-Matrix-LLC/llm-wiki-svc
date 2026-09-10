# HISTORY

Every code, configuration, or architectural change to this repository, in
reverse-chronological order. See `CLAUDE.md` for the rule this file follows.

---

## 2026-09-09 — Plan + diagnostic script for standing up real Cloudflare Vectorize

**Goal:** the service still runs entirely offline (`STORAGE_BACKEND=local`,
`VECTOR_BACKEND=memory`, `EMBEDDING_BACKEND=fake` in `.env`), even though R2
credentials had already been filled in in an earlier session — `CF_ACCOUNT_ID`
and `CF_API_TOKEN` (Vectorize + Workers AI) were still `changeme`. Asked to
plan the move to a real Cloudflare Vectorize backend, plus a script to check
the setup (structure, credentials, permissions) before trusting it with real
ingests.

**Implementation detail:**
- `docs/cloudflare-vectorize-setup-plan.md` — sequences the existing runbook
  (`docs/implement-plan.md` §6.2–6.4: account ID, scoped API token, index
  creation) against the gap actually present in this `.env`, and adds the new
  diagnostic script as an explicit preflight step before flipping the three
  backend switches.
- `scripts/check_cloudflare_setup.py` (new, executable) — six independently
  reported checks: config presence, R2 put/get/delete round trip, Cloudflare
  API token validity (`GET /user/tokens/verify`), a live Workers AI embedding
  call asserting the dimension matches `EMBEDDING_DIM`, Vectorize index/
  metadata-index structure, and (skippable with `--quick`) a live Vectorize
  upsert → poll-until-queryable → delete round trip — the only one of the six
  that actually proves the token's *Edit* scope works, since token-verify and
  index-describe both succeed on a read-only token. Each check prints
  `[OK]`/`[FAIL]`/`[SKIP]` with a remediation hint; the process exits non-zero
  if anything failed. Deliberately a superset of, not a replacement for,
  `scripts/bootstrap_indexes.py` (structure only, and it creates things) and
  `tests/integration/test_cloudflare.py` (pytest-gated, the real regression
  net) — this is the fast, human-readable, run-by-hand preflight between them.
- No `.env`/`.env.example`/`src/llmwiki` changes — the required Cloudflare
  variables were already documented; only their values are still placeholders,
  which is exactly what the new script's first check flags.

**Related files:** `docs/cloudflare-vectorize-setup-plan.md`,
`scripts/check_cloudflare_setup.py`.

**Test coverage:**
- No regressions: `pytest` — 319 passed, 1 skipped, 6 deselected (opt-in
  integration, unaffected by this change); `ruff check .` and `mypy` both
  clean on the new file.
- No obsolete tests — nothing existing changed behavior.
- No new unit test added for the new script itself, matching the existing
  convention: `scripts/bootstrap_indexes.py`, the closest analog, has none
  either — every behavior worth asserting needs a live Cloudflare account and
  is already covered, for the underlying adapters, by
  `tests/integration/test_cloudflare.py`. Noted explicitly in the plan doc
  rather than silently skipped.
- Ran `scripts/check_cloudflare_setup.py --quick` against the current (still
  placeholder) `.env`: failed at check 1 as expected, reporting
  `CF_ACCOUNT_ID, CF_API_TOKEN` missing and making no network calls — the
  intended behavior before real credentials exist. The script has not yet been
  run against a live Cloudflare account; that only happens once the plan's
  steps 1–2 are carried out.

### Follow-up within the same change: Account API Token, not User API Token

**Root cause.** The plan and `docs/implement-plan.md` §6.3 originally pointed
at Dashboard → My Profile → API Tokens (a *User* API Token). The user asked
about Cloudflare's own recommendation to use an *Account* API Token instead —
correct: a user token is tied to whoever created it and stops working if that
person loses account access; an account token is a durable service principal,
which is what a credential baked into a server's `.env` should be. Confirmed
against current Cloudflare docs (verified live, not from memory): account
tokens live under Manage Account → Account API Tokens, require Super
Administrator to create, and — the concrete consequence for this codebase —
verify against a *different* endpoint than user tokens:
`GET /accounts/{account_id}/tokens/verify`, not `GET /user/tokens/verify`.

**Fix.** `docs/implement-plan.md` §6.3 and `docs/cloudflare-vectorize-setup-plan.md`
now both specify an Account API Token with the correct dashboard path and the
account-scoped verify curl example. `scripts/check_cloudflare_setup.py` check
3 now hits `/accounts/{account_id}/tokens/verify` instead of
`/user/tokens/verify`, with a comment explaining why.

**Related files:** `docs/implement-plan.md`, `docs/cloudflare-vectorize-setup-plan.md`,
`scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` and `mypy` clean on the changed script;
re-ran `scripts/check_cloudflare_setup.py --quick` against the (still
placeholder) `.env` — same expected check-1 failure, no network calls. No
`pytest` impact — none of the three changed files are imported by the test
suite.

### Follow-up within the same change: finding CF_ACCOUNT_ID

**Root cause.** `docs/cloudflare-vectorize-setup-plan.md` step 1 said "Dashboard →
right sidebar" for the account ID; the user couldn't find it there — Cloudflare
moved it. Confirmed live against current Cloudflare docs: it's now `Cmd/Ctrl+K` →
"Copy account ID", Workers & Pages → Account details, or a domain's Overview →
API section. Also noted the faster path specific to this repo: the account ID is
already the subdomain of the `R2_ENDPOINT_URL` this `.env` has from the earlier R2
setup, since R2 and Vectorize/Workers AI share one Cloudflare account.

**Fix.** `docs/cloudflare-vectorize-setup-plan.md` step 1 rewritten with the
`.env`-shortcut first and the three current dashboard locations.
`docs/implement-plan.md` §6.2 already said "it is the R2 endpoint hostname" and
needed no change.

**Related files:** `docs/cloudflare-vectorize-setup-plan.md`.

**Test coverage:** none applicable — a documentation-only correction, not a
covered code path.

### Follow-up within the same change: the permission is "Vectorize Write", not "Vectorize Edit"

**Root cause.** Every doc in this repo (inherited from the original
`implement-plan.md`, predating this session) said `Account → Vectorize →
Edit`. The user couldn't find a permission called "Edit" when creating an
Account API token. Checked Cloudflare's current API reference directly (the
per-operation docs for `vectorize-create-vectorize-index` and others): the
permission group is actually named **"Vectorize Write"** (paired with
"Vectorize Read"), matching the Read/Write convention Cloudflare uses for
newer products, not the older Read/Edit convention. Also worth stating
explicitly since the user's phrasing suggested otherwise: Workers AI → Read
does **not** cover Vectorize — they're two separate products needing two
separate permission grants on the same token.

**Fix.** Renamed every "Vectorize Edit" reference to "Vectorize Write" across
`docs/implement-plan.md` (§6.3 permission list and the `.env` comment),
`.env.example`, `docs/cloudflare-vectorize-setup-plan.md` (step 2, step 5, and
§3's check-6 description), and `scripts/check_cloudflare_setup.py` (check 6's
label and its failure-hint text). Also added a note in both plan docs that
Workers AI Read and Vectorize Write are independent grants, and that the
permission may need to be found via search in the token-creation picker.

**Related files:** `docs/implement-plan.md`, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md`, `scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` and `mypy` clean on the changed script;
re-ran `scripts/check_cloudflare_setup.py --quick` — same expected check-1
failure against the still-placeholder `.env`, no network calls. No `pytest`
impact — this was a rename of user-facing strings and doc text only, no
behavior change.

### Follow-up within the same change: Workers AI needs Read *and* Edit, not Read alone

**Root cause.** Asked whether "Workers AI Read, Workers AI Write, Vectorize
Write" was sufficient. Checked Cloudflare's own Workers AI REST API quickstart
directly: running a model (exactly what `embedding/workers_ai.py` does) needs
**both** `Workers AI - Read` and `Workers AI - Edit` granted together — this
repo's docs had only ever specified Read, which per Cloudflare's own docs is
not enough on its own. Also confirmed the two products name their permission
groups inconsistently: Vectorize is Read/**Write**, Workers AI is Read/**Edit**
— there is no "Workers AI Write".

**Fix.** `docs/implement-plan.md` §6.3 and its `.env` comment, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md` step 2, and the failure hint in
`scripts/check_cloudflare_setup.py` (`check_workers_ai`) now all list three
required grants: Vectorize Write, Workers AI Read, Workers AI Edit.

**Related files:** `docs/implement-plan.md`, `.env.example`,
`docs/cloudflare-vectorize-setup-plan.md`, `scripts/check_cloudflare_setup.py`.

**Test coverage:** `ruff check .` (one line-length fix needed and applied) and
`mypy` both clean; `pytest` — 319 passed, 1 skipped, 6 deselected, unaffected;
re-ran `scripts/check_cloudflare_setup.py --quick` — same expected check-1
failure, no network calls.

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
