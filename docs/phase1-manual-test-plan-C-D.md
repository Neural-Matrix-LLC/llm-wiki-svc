# Phase 1 manual test plan — workstreams C and D

**Scope.** Workstream **C** (local-LLM routing: vLLM primary / llama.cpp
fallback on the RTX 3090 host) and workstream **D** (LangGraph query graph,
external search, LangSmith eval and correction loop). Workstreams A/B (the
capture channels) were verified separately (commit `c37e03d`, guide §2–§3).

**Relationship to `docs/phase1-testing-guide.md`.** The guide is the
long-form *how to set it up* narrative (§4 for vLLM, §5 for the query graph).
This document is the *what to verify and how to tell* — numbered test cases
with preconditions, exact commands, expected results, the evidence to record,
and a results log. Setup procedures are repeated here in condensed form so a
tester can run the plan top to bottom; the guide is referenced where a step
needs its longer explanation.

**Written 2026-09-17** against `llmwiki 0.9.0`, 459 unit tests.

---

## 0. How to use this plan

### 0.1 Conventions

- Cases are `C-nn` / `D-nn`. Each has **Purpose**, **Preconditions**,
  **Steps**, **Expected**, **Evidence** (what to paste into the results log).
- A result is **PASS**, **FAIL**, **BLOCKED** (a precondition could not be met —
  say which) or **N/A** (the deployment does not use that feature).
- Cases marked **(O)** are *observational*: they depend on what the model
  chooses to do, so the expected result is a bound, not a specific value. A
  model that never calls a tool does not fail an (O) case; a bound being
  exceeded does.
- Run the automated baseline (§1, S-0.1) **before** anything else and again at
  the end (C-14, D-21). A manual PASS on top of a red unit suite is not a PASS.

### 0.2 Tooling this plan relies on

| Tool | Workstream | Status | What it gives you |
|---|---|---|---|
| `scripts/check_local_llm.py` | C | **new (2026-09-17)** | six pass/fail checks between `.env` and a working local compile: config → reachability → routed model is served → plain completion → forced-tool-call completion → the real routed op through `factory.llm_client` |
| `scripts/probe_query_graph.py` | D | **new (2026-09-17)** | one question under one or a matrix of `(AGENT_MAX_TOOL_CALLS, AGENT_WEB_SEARCH_POLICY)` settings, applied in-process (no `.env` edit, no restart); prints steps + the model's per-step *reasons*; checks the code-enforced invariants; `--verify-trace` checks the LangSmith run tree |
| `scripts/eval_answer.py` | D | existing | golden-set scoring, `--push`, `--langsmith`, `--judge`, `--export-failures`, `--promote-feedback` |
| `scripts/smoke_flow.py --offline`, `scripts/eval_answer.py --offline` | both | existing | the no-keys gate |
| `llmwiki` CLI (`status`, `ingest`, `compile`, `source`, `cost`, `ask`, `feedback`, `lint`) | both | existing | everything the REST surface does, without a server |
| `GET /healthz` | both | existing | `.config.routes` (op → provider/model in force), `.config.query_graph` (the bounds in force) — what the *running process* has, not what `.env` says |
| `wiki/_meta/cost.jsonl` / `llmwiki cost` | C | existing | the measured ledger: op, model, tokens, USD per compile call |
| LangSmith UI | D | external | run trees, feedback, datasets, experiments |
| `tests/unit/test_phase1_scripts.py` | both | **new (2026-09-17)** | pins the two scripts' judgement (invariants, trace checks, provider/model resolution) and one offline probe run |

Both new scripts print `[OK]`/`[FAIL]`/`[SKIP]`/`[WARN]` lines with a `->`
remediation hint under every failure, and exit 1 on any failure — the same
posture as `check_cloudflare_setup.py` and `smoke_flow.py`.

### 0.3 Cost and time expectations

| Block | Real tokens? | Approximate |
|---|---|---|
| §1 baseline, C-01, D-01 | no | 1 min |
| C setup + C-02…C-07 | a few local-model completions; no cloud spend | 30–90 min the first time (model download dominates) |
| C-08…C-13 | one PDF compile per case: two local stages + two cloud `create_page`/`patch_page` calls — cents | 15 min |
| D-02…D-12 | 1–3 cheap `agent_step` calls + 1–2 `answer_query` calls per probe run; a `--matrix` run is 9 questions' worth | cents per run |
| D-13…D-20 | as above plus LangSmith/Tavily quota; `--judge` adds one `judge_answer` call per example | cents |

### 0.4 Environments

| Name | What | Used by |
|---|---|---|
| **dev** | this checkout, `.venv`, `.env` with the real cloud keys (OpenRouter today), `STORAGE_BACKEND=local` or R2 | every case |
| **ML3090** | the RTX 3090 host; runs `vllm serve` (port 8100) and `llama-server` (port 8080) | C setup, C-02…C-13 |
| **container** (optional) | `docker compose --profile dev up dev` / `--profile ops run eval` | C-13, D-20 |

---

## 1. Common setup (both workstreams)

### S-0.1 Automated baseline

```bash
cd ~/projects/llm-wiki-svc
source .venv/bin/activate                      # python 3.11.14
pytest -q                                      # expect: 459 passed, 1 skipped, 7 deselected
ruff check . && mypy                           # expect: clean
python scripts/smoke_flow.py --offline         # expect: SMOKE PASS
python scripts/eval_answer.py --offline        # expect: EVAL PASS
python scripts/probe_query_graph.py --offline --matrix --max-tool-calls 4   # expect: 9 run(s), 0 failed / PROBE PASS
```

If `smoke_flow.py --offline` fails with `PermissionError … .data/raw/…`, the
checkout's `.data/` is owned by the container uid (`init-data`, 2026-09-13);
run it with `LOCAL_STORAGE_PATH=/tmp/llmwiki-smoke` or `chown` the directory —
that is an environment issue, not a test failure.

### S-0.2 `.env` sanity

```bash
llmwiki status | jq '.config'
```

Expect `llm_routing: "per-op table"`, `routes` listing all seven ops on the
cloud provider (today `openrouter/z-ai/glm-5.3-flash`), `providers_config`
and `ops_config` `present: true`, `skills_dir.skill_files >= 2`, and
`query_graph` echoing `AGENT_MAX_TOOL_CALLS` / `AGENT_WEB_SEARCH_POLICY` /
`WEB_SEARCH_BACKEND` / `LANGSMITH_TRACING` from `.env`. Record the routes —
C-06 compares against them.

### S-0.3 A small real corpus

Both workstreams need sources that were compiled by the real pipeline. Ingest
three of the checked-in test files (each is one compile; cents):

```bash
llmwiki ingest --file "tests/testingfiles/ORB_Day_Trading_SSRN-id4416622.pdf"
llmwiki ingest --file "tests/testingfiles/a-practical-guide-to-building-agents.pdf"
llmwiki ingest --file "tests/testingfiles/How to Build Your Own Custom LLM Memory Layer from Scratch.md"
llmwiki concepts | jq -r '.[].slug'          # the wiki pages that resulted
```

Record each `source_id` (`{sha256[:16]}-{slug}`) — D's golden set (S-D.5)
and C-08 refer to them. Skip this if the deployment already has a corpus;
then pick three existing ids from `llmwiki concepts` / `GET /sources/{id}`.

### S-0.4 Dev server

```bash
llmwiki serve                                  # http://localhost:8011
#   or: docker compose --profile dev up dev    # same port, tree bind-mounted
curl -s localhost:8011/healthz | jq .config
```

The CLI cases below work without the server; the `curl` variants need it.
`INGEST_API_TOKEN` from `.env` is the bearer for `/ingest`, `/feedback`,
`/compile`, `/lint`.

---

## 2. Workstream C — local-LLM routing

### 2.1 What is being verified

`config/providers.py` gives `vllm` and `llamacpp` their own
`VLLM_API_KEY`/`VLLM_BASE_URL` and `LLAMACPP_API_KEY`/`LLAMACPP_BASE_URL`
pairs (2026-09-14). `config/ops.py` carries a commented example routing the
two cheap compile stages (`summarize_source`, `plan_compile`) to them. The
claim under test: **once an endpoint is reachable, uncommenting those rows
moves exactly those two stages to the local server, everything else stays on
the cloud provider, cost is recorded as $0 with real token counts, and every
misconfiguration fails loudly at startup rather than silently routing
elsewhere.**

Two facts about the code shape the cases:

1. Every compile-stage call is a **forced tool call** (`LangChainLLM` binds
   one tool named `emit` and sets `tool_choice` to it — `summarize_source`
   and `plan_compile` both pass a schema). A local server that answers in
   prose instead of a tool call compiles nothing. `check_local_llm.py` check
   5 tests exactly this shape before any real compile is attempted.
2. `RoutingLLMClient` has **no automatic failover**. "vLLM primary /
   llama.cpp fallback" is a *configuration* choice (which op names which
   provider), not a runtime retry. C-11 verifies that a down server fails the
   compile loudly and does **not** silently fall back to the cloud.

### 2.2 Setup procedure

#### S-C.1 vLLM on ML3090 (guide §4 steps 1–5, condensed)

```bash
# on ML3090
nvidia-smi                                          # free VRAM after llama.cpp; note it
python3 -m venv ~/vllm-venv && source ~/vllm-venv/bin/activate
pip install --upgrade pip && pip install vllm
export VLLM_SECRET=$(openssl rand -hex 20); echo "$VLLM_SECRET"   # -> VLLM_API_KEY
vllm serve Qwen/Qwen2.5-14B-Instruct-AWQ \
  --served-model-name qwen2.5-14b \
  --host 0.0.0.0 --port 8100 \
  --api-key "$VLLM_SECRET" \
  --gpu-memory-utilization 0.5 --max-model-len 8192 \
  --enable-auto-tool-choice --tool-call-parser hermes
```

`--served-model-name` must equal the `"model"` in `config/ops.py`'s row
**exactly**. The two tool-call flags are not strictly required for the
*named* `tool_choice` llmwiki sends (vLLM handles that through guided
decoding by default) but are harmless and make `tool_choice: auto` work too;
check 5 of the script is what tells you whether your version needs them.
Keep it running in `tmux` or as the systemd unit in guide §4 step 5.

> Sourcing note (as in the guide): model names, VRAM figures and CLI flags
> are supplementary guidance, not from this repo — cross-check `vllm serve
> --help` for the version installed.

#### S-C.2 llama.cpp server on ML3090 (the fallback provider)

llama.cpp is already running on ML3090 per the guide; what C needs is its
**OpenAI-compatible route with tool calling on**:

```bash
export LLAMACPP_SECRET=$(openssl rand -hex 20); echo "$LLAMACPP_SECRET"   # -> LLAMACPP_API_KEY
llama-server -m /path/to/Qwen2.5-14B-Instruct-Q4_K_M.gguf \
  --alias qwen2.5-14b-gguf \
  --host 0.0.0.0 --port 8080 \
  --api-key "$LLAMACPP_SECRET" \
  --jinja -c 8192 -ngl 99
```

`--alias` is what `/v1/models` reports and what `config/ops.py`'s
`"model": "qwen2.5-14b-gguf"` must match; `--jinja` enables the chat
template's tool-call support (without it the server answers a forced tool
call in prose — check 5 will say so). If the existing llama.cpp instance was
started without `--alias`/`--jinja`/`--api-key`, restart it with them.

#### S-C.3 Reachability from **dev**

```bash
# from the dev machine; <ml3090> = LAN IP or tunnel hostname
curl -s http://<ml3090>:8100/v1/models -H "Authorization: Bearer $VLLM_SECRET" | jq '.data[].id'
curl -s http://<ml3090>:8080/v1/models -H "Authorization: Bearer $LLAMACPP_SECRET" | jq '.data[].id'
```

Expect `"qwen2.5-14b"` and `"qwen2.5-14b-gguf"`. Firewall both ports to the
trusted subnet (guide §4 step 7) — the API key is the only auth these servers
have.

#### S-C.4 Wire `.env` on **dev**

```
VLLM_API_KEY=<VLLM_SECRET>
VLLM_BASE_URL=http://<ml3090>:8100/v1
LLAMACPP_API_KEY=<LLAMACPP_SECRET>
LLAMACPP_BASE_URL=http://<ml3090>:8080/v1
```

Nothing changes in `config/providers.py` — its `vllm`/`llamacpp` rows already
name these variables. Use the LAN IP, not `localhost`, if the service will
ever run in a container (C-13).

#### S-C.5 Pre-flip check

```bash
python scripts/check_local_llm.py --quick      # checks 1-3: no tokens
python scripts/check_local_llm.py              # + checks 4-5: two tiny completions per server
```

Expect every line `[OK]` except a `[WARN] no op is routed to a local
provider yet` — that is the state before S-C.6. Do not proceed to the flip
until checks 4 **and 5** pass for each server you intend to route to.

#### S-C.6 Flip `config/ops.py`

Replace the two cheap-stage rows so the file has exactly one row per op:

```python
{"op": "summarize_source", "provider": "vllm", "model": "qwen2.5-14b",
 "temperature": 1.0, "max_tokens": 2048},
{"op": "plan_compile", "provider": "llamacpp", "model": "qwen2.5-14b-gguf",
 "temperature": 1.0, "max_tokens": 2048},
```

(delete or comment out the `openrouter` rows for those two ops — a duplicate
op fails at startup, which C-04 exercises on purpose). Leave `create_page`,
`patch_page`, `answer_query`, `agent_step`, `judge_answer` on the cloud
provider. If you have only vLLM running, point both rows at `vllm`.

`config/` is **baked into the Docker image** (2026-09-10): a container sees
this edit only after `docker compose build`, or with the
`./config:/app/config:ro` mount uncommented in `docker-compose.yml`
(lines ~125 / ~271). The CLI and `llmwiki serve` read the file directly.

#### S-C.7 Post-flip check and restart

```bash
python scripts/check_local_llm.py --op summarize_source --op plan_compile
llmwiki status | jq '.config.routes'
# then restart whatever serves the API (llmwiki serve / compose up -d --force-recreate)
```

### 2.3 Test cases

#### C-01 — Baseline: local providers inactive, nothing routed locally

- **Purpose:** the shipped state is inert — no local var set, no op local, and the diagnostic says so instead of passing vacuously.
- **Preconditions:** S-0.1; `VLLM_*`/`LLAMACPP_*` **unset** in `.env`; `config/ops.py` unmodified.
- **Steps:** `python scripts/check_local_llm.py; echo exit=$?` and `llmwiki status | jq '.config.routes | to_entries[] | select(.value | test("vllm|llamacpp"))'`.
- **Expected:** `[SKIP] vllm: inactive`, `[SKIP] llamacpp: inactive`, `[FAIL] no local provider is active` with the `.env` hint, `[WARN] no op is routed…`, `exit=1`. The `jq` prints nothing.
- **Evidence:** the script output.

#### C-02 — Endpoint reachable, key accepted, served model listed

- **Preconditions:** S-C.1–S-C.4.
- **Steps:** `python scripts/check_local_llm.py --quick`.
- **Expected:** per active server: `[OK] <name>: active, base_url=…`, `[OK] <name>: /models in N ms - served: [...]`, `[SKIP] <name>: no config/ops.py row routes to it`. Exit 0 (the WARN about no op routed does not fail the run).
- **Negative sub-steps (each must FAIL with the matching hint):**
  - wrong key: `VLLM_API_KEY=wrong python scripts/check_local_llm.py --quick --provider vllm` → `-> 401` + "must equal the server's --api-key exactly".
  - wrong port: `VLLM_BASE_URL=http://<ml3090>:8101/v1 …` → `ConnectError … Connection refused` + "listening on 0.0.0.0…".
  - URL without `/v1`: `VLLM_BASE_URL=http://<ml3090>:8100 …` → `[WARN] … does not end in /v1` and then a non-200 on `/models`.
- **Evidence:** four outputs.

#### C-03 — Plain and forced-tool-call completions through the project's adapter

- **Purpose:** the exact class the router builds (`LangChainLLM` over `ChatOpenAI`) gets a text reply with real token counts, and a *forced named tool call* returns structured data — the shape every compile stage uses.
- **Preconditions:** C-02 passed.
- **Steps:** `python scripts/check_local_llm.py` (both servers) — optionally `--model <served-id>` if the routed name is not yet in `ops.py`.
- **Expected:** check 4 `[OK] … 'pong' (N ms, in=>0 out=>0 cost=$0.0000)`; check 5 `[OK] … data={'answer': 'pong', …}`. A `[FAIL]` on 5 with the vLLM/llama.cpp hint means the server flags in S-C.1/S-C.2 are missing — fix and re-run; **do not flip `ops.py` until 5 is green**.
- **Evidence:** the two check lines per server, with latency.

#### C-04 — Misconfiguration fails loudly at startup: inactive provider named by an op

- **Purpose:** `test_op_naming_an_inactive_provider_fails_loudly` in the real process, not a tmp fixture.
- **Preconditions:** S-C.6 done (rows flipped).
- **Steps:** `VLLM_API_KEY= llmwiki status; echo exit=$?` (empty var overrides `.env`), then `VLLM_API_KEY= python scripts/check_local_llm.py --quick`.
- **Expected:** `RuntimeError: … op 'summarize_source' routes to provider 'vllm', which is not active …` from the CLI, non-zero exit; the script shows `[FAIL] routing table does not load: RuntimeError: …` with "the same error the service raises at startup". Also try a duplicate row (keep both `summarize_source` rows for a moment) → `duplicate op entry 'summarize_source'`. Restore the file.
- **Evidence:** the error lines.

#### C-05 — Misconfiguration fails loudly: routed model is not served

- **Steps:** temporarily set the `summarize_source` row's model to `qwen2.5-14b-typo`; run `python scripts/check_local_llm.py --quick --provider vllm`; then `llmwiki compile <source_id> --force` on one S-0.3 id.
- **Expected:** `[FAIL] summarize_source -> vllm/qwen2.5-14b-typo but the server serves ['qwen2.5-14b']` with the `--served-model-name` hint; the compile fails with the server's 404/"model not found" error and `llmwiki source <id>` shows the failure (`state` not `done`, error text present). Restore the model name; `llmwiki compile <id> --force` succeeds.
- **Evidence:** script line, compile error, `llmwiki source` before/after.

#### C-06 — The flip is visible where it must be

- **Preconditions:** S-C.6, S-C.7.
- **Steps:** `llmwiki status | jq '.config.routes'`; with the server running, `curl -s localhost:8011/healthz | jq '.config.routes'`.
- **Expected:** `summarize_source: "vllm/qwen2.5-14b"`, `plan_compile: "llamacpp/qwen2.5-14b-gguf"`, the other five ops unchanged from S-0.2. Both surfaces agree. If `curl` still shows the old routes, the server process was not restarted (or the container image was not rebuilt — S-C.6 note).
- **Evidence:** both `routes` objects.

#### C-07 — The real routed op reaches the local server

- **Steps:** `python scripts/check_local_llm.py --op summarize_source --op plan_compile --op create_page`.
- **Expected:** check 6 `[OK] summarize_source -> vllm/qwen2.5-14b: data={…} (N ms, in=… out=… cost=$0.0000)`, same for `plan_compile -> llamacpp/…`; `[FAIL] create_page is not routed to a local provider` (correct — it must not be). The `CostRecord` must name the op and the local model (the script fails otherwise).
- **Evidence:** the three check-6 lines.

#### C-08 — A real compile uses the local stages; the ledger records $0 with real tokens

- **Purpose:** design §4.4's four stages, two local and two cloud, on one real source.
- **Preconditions:** C-06, C-07.
- **Steps:**
  ```bash
  llmwiki cost | jq '{call_count, total_usd, by_model}'                 # before
  LOG_LEVEL=DEBUG llmwiki ingest --file tests/testingfiles/cherry_pick_signals.pdf 2>&1 | tee /tmp/c08.log
  grep -E "summarize_source|plan_compile|create_page|patch_page" /tmp/c08.log | grep -iE "model|provider" | head
  llmwiki cost | jq '{call_count, total_usd, by_model}'                 # after
  # local storage only:
  tail -n 6 .data/wiki/_meta/cost.jsonl | jq -c '{op, model, input_tokens, output_tokens, cost_usd}'
  ```
- **Expected:** the source reaches `state: done` (`llmwiki source <id>`); the debug log names `qwen2.5-14b` for `summarize_source`, `qwen2.5-14b-gguf` for `plan_compile`, the cloud model for `create_page`/`patch_page`; the ledger has one `summarize_source` and one `plan_compile` line with `cost_usd: 0.0` and **nonzero** `input_tokens`/`output_tokens`, and `by_model` gains `qwen2.5-14b: 0.0` and `qwen2.5-14b-gguf: 0.0` while the cloud model's total grows only by the executor stages. A local line with zero tokens is a FAIL (the server is not returning `usage`).
- **Evidence:** the ledger lines and both `llmwiki cost` snapshots.

#### C-09 — Compiled output is usable (quality sanity) **(O)**

- **Steps:** `llmwiki concepts | jq -r '.[] | select(.slug | test("cherry|signal"))'`; `llmwiki page <slug>`; `llmwiki search "cherry picking signals" -k 3`; `llmwiki lint`.
- **Expected:** at least one page created or patched for the source, with a one-line gist, wikilinks that resolve, and the source cited; `llmwiki lint` reports no new broken links/orphans attributable to this source. The 14B local models are expected to produce plans and summaries comparable to the cloud flash model; a page that is empty, truncated (`finish_reason=length`) or in the wrong language is a FAIL — raise `max_tokens` on the row or pick a larger quant, and record what you changed.
- **Evidence:** page slug(s), lint summary.

#### C-10 — Mixed providers coexist: cloud + vLLM + llama.cpp active at once

- **Purpose:** the 2026-09-14 reason for splitting the providers — three active providers, none sharing a variable.
- **Steps:** with all three keys set, `llmwiki status | jq '.config.routes | group_by(.) | map({route: .[0], ops: length})'` and `python scripts/check_local_llm.py --quick`. If you also hold a real `OPENAI_API_KEY`, set it too and confirm `check_local_llm.py` still reports the vLLM base URL, not `OPENAI_BASE_URL`.
- **Expected:** three distinct provider prefixes in `routes`; both local servers `[OK]`; no cross-talk between `OPENAI_*` and `VLLM_*`.
- **Evidence:** the routes grouping.

#### C-11 — Local server down: the compile fails loudly and does **not** fall back to the cloud

- **Purpose:** pins the "no automatic failover" fact so nobody assumes otherwise later.
- **Steps:**
  ```bash
  # on ML3090: sudo systemctl stop vllm   (or Ctrl-C the tmux session)
  llmwiki cost | jq '{call_count, total_usd}'                       # before
  llmwiki compile <source_id from S-0.3> --force; echo exit=$?
  llmwiki source <source_id> | jq '{state, error}'
  llmwiki cost | jq '{call_count, total_usd}'                       # after
  # on ML3090: sudo systemctl start vllm
  llmwiki compile <source_id> --force && llmwiki source <source_id> | jq .state
  ```
- **Expected:** the compile fails with a connection error naming the vLLM host/port; exit non-zero; the source's status records the failure; `call_count`/`total_usd` are **unchanged** (the pipeline stops at `summarize_source`, the first stage — no cloud call was made in its place). After the restart the same compile succeeds and `state: done`.
- **Evidence:** the error text, both cost snapshots.

#### C-12 — Rollback: commenting the rows back out restores the cloud routes

- **Steps:** re-comment the two rows (restore the `openrouter` rows), `llmwiki status | jq '.config.routes'`, `python scripts/check_local_llm.py --quick`.
- **Expected:** routes identical to S-0.2; the script goes back to `[WARN] no op is routed…` while checks 1–2 still pass (the servers are still active, just unused). Then un-set `VLLM_*`/`LLAMACPP_*` in `.env` → C-01's output again.
- **Evidence:** the routes object.

#### C-13 — From a container (optional; N/A if the deployment is bare-metal)

- **Preconditions:** C-06 passed on dev; `VLLM_BASE_URL` uses the LAN IP.
- **Steps:** `docker compose build && docker compose up -d --force-recreate api` (or uncomment the `./config:/app/config:ro` mount); `curl -s localhost:8011/healthz | jq '.config.routes'`; `docker compose exec api python scripts/check_local_llm.py --quick`; one `POST /ingest` with a URL.
- **Expected:** routes show the local providers *inside* the container; the in-container check reaches ML3090 (a `[WARN] base URL points at localhost` here is the diagnosis when it does not); the ingest completes and the ledger shows the local stages as in C-08.
- **Evidence:** in-container script output.

#### C-14 — Regression gate with the rows flipped

- **Steps:** with `config/ops.py` flipped and the local vars set: `pytest -q`, `ruff check .`, `mypy`, `python scripts/smoke_flow.py --offline`, `python scripts/eval_answer.py --offline`.
- **Expected:** identical to S-0.1. The unit suite never reads the real routing table (`tests/conftest.py`'s autouse isolation; `test_routing_config.py` uses the real `providers.py` only), and the offline scripts pin the loader to a nonexistent path — so a flipped table must not change any result. Any difference is a FAIL of that isolation.
- **Evidence:** the pytest summary line.

### 2.4 Teardown

Keep or revert S-C.6 as the deployment requires; if reverted, run C-12. Leave
the vLLM systemd unit enabled only if the box is meant to serve it
permanently (guide §4 step 5).

---

## 3. Workstream D — query graph, external search, LangSmith eval

### 3.1 What is being verified

`QueryAgent.answer()` is a LangGraph `StateGraph`: wiki-first `retrieve`,
then a **bounded** loop in which a cheap `agent_step` call picks
`search_wiki` / `search_chunks` / `get_page` — or `search_web` when
`AGENT_WEB_SEARCH_POLICY` offers it — then skill selection, generation and
citation resolution. The claims under test:

1. **Bounds are code-enforced, never model-requested:** `len(steps) ≤
   AGENT_MAX_TOOL_CALLS`; `0` reproduces Phase 0 exactly; `search_web` count
   ≤ `AGENT_MAX_WEB_SEARCHES`; the context budget stops the loop.
2. **Policy gates the *offer*, not the choice:** with `off`, or backend
   `none`, the tool is never in the action schema; with `weak` it is offered
   only after the chunk fallback ran.
3. **Citations stay a property of what was retrieved:** every citation
   resolves to a `raw/` object; a web result is an `external_ref`, never a
   citation.
4. **One `answer()` is one LangSmith trace** shaped as technical document
   §10.2, whose root id is `Answer.run_id`, to which `POST /feedback` attaches
   a correction that `--promote-feedback` can turn into a golden example.
5. **The eval loop closes:** golden set → local table → LangSmith dataset →
   experiment with metadata (version, git sha, routes, bounds) → failures
   exported → corrections promoted.

`probe_query_graph.py` checks 1–3 on every run it makes and 4 with
`--verify-trace`; `eval_answer.py` is 5.

### 3.2 Setup procedure

#### S-D.1 Offline gate (no keys) — D-01 is this, formalised

```bash
pytest tests/unit/test_agent_graph.py tests/unit/test_agent_toolkit.py tests/unit/test_eval.py tests/unit/test_judge.py tests/unit/test_websearch.py tests/unit/test_phase1_scripts.py -q
python scripts/eval_answer.py --offline --judge
python scripts/probe_query_graph.py --offline --matrix --max-tool-calls 4 -v
```

#### S-D.2 `.env` bounds for the live runs

```
AGENT_MAX_TOOL_CALLS=4
AGENT_WEB_SEARCH_POLICY=off        # the probe overrides this per run; keep the file at the default
AGENT_MAX_WEB_SEARCHES=1
WEB_SEARCH_BACKEND=none            # set to tavily in S-D.4
```

`config/ops.py`'s `agent_step` row (`max_tokens: 2048`, `temperature: 0.2`)
and `answer_query` (`max_tokens: 8192`) are the 2026-09-16 values that
survived a reasoning model — leave them.

#### S-D.3 LangSmith

1. smith.langchain.com → Settings → API keys → create.
2. `.env`: `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY=lsv2_…`,
   `LANGSMITH_PROJECT=llmwiki-manual-cd` (a project per test session keeps
   D-14/D-17 unambiguous), `LANGSMITH_EVAL_DATASET=llmwiki-manual-cd`.
3. `llmwiki status | jq .config.query_graph.langsmith_tracing` → `true`.
   For the server: `docker compose up -d --force-recreate` or restart
   `llmwiki serve` — a `restart` does not re-read `.env`.

#### S-D.4 Tavily

1. https://app.tavily.com → API key.
2. `.env`: `WEB_SEARCH_BACKEND=tavily`, `TAVILY_API_KEY=tvly-…`. Leave
   `AGENT_WEB_SEARCH_POLICY=off` — the probe sets the policy per run.

#### S-D.5 A golden set for *this* corpus

The shipped `tests/fixtures/eval/answer_quality.jsonl` names the two fixture
documents' ids, which do not exist in a real corpus. Write three examples
over the S-0.3 sources (one per source; `must_mention` a term you know the
page contains; `expected_sources` the id from S-0.3):

```bash
mkdir -p eval
cat > eval/manual-cd.jsonl <<'JSONL'
{"question": "What is an opening range breakout and when is it entered?", "expected_sources": ["<id of ORB_Day_Trading>"], "must_mention": ["opening range"], "notes": "specific-detail question; a candidate for search_chunks"}
{"question": "What are the building blocks of an LLM agent according to the practical guide?", "expected_sources": ["<id of a-practical-guide>"], "must_mention": ["tools"], "notes": "overview question; wiki-first should suffice"}
{"question": "How is a custom memory layer for an LLM structured?", "expected_sources": ["<id of the memory-layer md>"], "must_mention": ["memory"], "notes": "cross-page question; may call get_page"}
JSONL
```

Plus one **deliberately failing** row for D-15's exit-code check — add it
only when that case runs:

```
{"question": "What colour is the ORB paper's cover?", "expected_sources": ["<id of ORB_Day_Trading>"], "must_mention": ["chartreuse"], "notes": "must fail must_mention"}
```

#### S-D.6 Dev server (for the `curl` and `POST /feedback` variants)

As S-0.4, after S-D.3/S-D.4 so the process has tracing and the search
backend. Confirm with `curl -s localhost:8011/healthz | jq .config.query_graph`.

### 3.3 Test cases

#### D-01 — Offline gate: bounds, evaluators, judge, probe matrix (no keys)

- **Steps:** S-D.1.
- **Expected:** the six test files pass; `EVAL PASS` with `judge_grounded 1.00` on every row (the fake grades everything grounded); probe `9 run(s), 0 failed` / `PROBE PASS` with `steps (0)` everywhere (the offline double answers `agent_step` with `answer` at once — the loop itself is exercised by the unit tests with scripted decisions, and by D-03 onwards with a real model).
- **Evidence:** the three summary lines.

#### D-02 — The bounds in force are the ones configured

- **Steps:** `llmwiki status | jq .config.query_graph`; `curl -s localhost:8011/healthz | jq .config.query_graph`; change `AGENT_MAX_TOOL_CALLS` in `.env` to `2`, run the CLI again (re-reads `.env`), then `curl` again (does not, until restarted).
- **Expected:** both echo `max_tool_calls: 4, web_search_policy: "off", web_search_backend: …, langsmith_tracing: …` from S-D.2/S-D.3; after the edit the CLI shows `2` and the server still shows `4` until `--force-recreate`/restart — this is the documented "edited `.env` never applied" diagnosis. Restore `4`.
- **Evidence:** the four `query_graph` objects.

#### D-03 — The loop runs for real and every invariant holds **(O)**

- **Preconditions:** S-0.3 corpus; cloud routing; tracing on or off.
- **Steps:** `python scripts/probe_query_graph.py -q "What is an opening range breakout and when is it entered?" -v --json eval/probe-runs.jsonl`.
- **Expected:** `ok [cap=4 policy=off backend=… max_web=1]`; `steps (n)` with `0 ≤ n ≤ 4` naming only `search_wiki`/`search_chunks`/`get_page`; `citations` non-empty and every id a real source (the probe checks `source_exists`); the `debug: agent_step: …` lines show the model's *reason* for each tool call and a final `agent_step: stop - answer (...)` or `stop - tool-call cap (4) reached`; a non-empty answer; `PROBE PASS`. Repeat with the other two S-D.5 questions.
- **Evidence:** the three run blocks (they are also in `eval/probe-runs.jsonl`).

#### D-04 — `AGENT_MAX_TOOL_CALLS=0` reproduces Phase 0's sequence

- **Steps:** `python scripts/probe_query_graph.py -q "<D-03 question>" --max-tool-calls 0 -v --verify-trace` (tracing on; without it drop `--verify-trace`).
- **Expected:** `steps (0): -`; no `agent_step:` decision lines at all (no decision call was made); with `--verify-trace`, the tree has `retrieve → select_skills → generate → resolve_citations` and **no** `agent`/`tools` nodes, and **no** LLM run named `agent_step`; `trace: ok`. Compare `citations` with a Phase-0-style `llmwiki ask` — they come from the same retrieval and should match.
- **Evidence:** the run block and the printed tree.

#### D-05 — `AGENT_MAX_TOOL_CALLS=1`: the cap, not the model, ends the loop **(O)**

- **Steps:** `python scripts/probe_query_graph.py -q "<the specific-detail question>" --max-tool-calls 1 -v --verify-trace`.
- **Expected:** `steps ≤ 1`. If the model called a tool: the decision log shows one `agent_step: <tool>(…) - <reason>` followed by `agent_step: stop - tool-call cap (1) reached` — the second decision call was **skipped**, not made and refused; the trace has exactly two `agent` nodes and one `tools` node, and at most 2 × 2 `agent_step` LLM runs (the `STEP_ATTEMPTS` retry bound); `trace: ok`. If the model answered at once, the case is PASS but note "model chose answer" — rerun with a question that needs a detail the gist does not carry.
- **Evidence:** the decision lines.

#### D-06 — Context budget and the repeat-call guard **(O)**

- **Purpose:** the two bounds that are not a count. They cannot be forced from outside; look for them.
- **Steps:** run the probe with `--max-tool-calls 4 -v` over a broad question (`"Summarise everything the knowledge base says about trading strategies"`) two or three times.
- **Expected:** if the model repeats a call with identical args the tool observation reads `… was already called with these arguments …` and the step **still counts** (`steps` grows); if the accumulated context passes `MAX_CONTEXT_CHARS` (12 000) a step shows `[truncated: context budget reached]` / the log shows `agent_step: stop - context budget exhausted`. Either occurrence is a PASS for that bound; neither occurring across three runs is "not observed" (record it, not a FAIL).
- **Evidence:** the relevant lines, or "not observed".

#### D-07 — The `no_answer` short-circuit

- **Purpose:** `retrieve` → `no_answer` → END when retrieval returns *nothing*. Note what triggers it: a nearest-neighbour store always returns *something* for a populated corpus (a nonsense question just gets weak hits and the chunk fallback), so the path is reached through an **empty** corpus, not a strange question.
- **Steps:** an empty in-memory store with the real embedder and routing (no LLM call is made — the graph ends before one):
  ```bash
  STORAGE_BACKEND=local VECTOR_BACKEND=memory LOCAL_STORAGE_PATH=/tmp/llmwiki-empty \
    python scripts/probe_query_graph.py -q "What is an opening range breakout?" -v
  ```
- **Expected:** answer text is exactly `Nothing in the knowledge base addresses that question yet.`; `steps (0)`; `citations=[]`; no `agent_step` decision lines (the loop was never entered); `PROBE PASS`. Then the same question against the real corpus (D-03) answers — the difference is the corpus, not the question.
- **Evidence:** the run block.

#### D-08 — Policy `off`: the key being present changes nothing

- **Preconditions:** S-D.4 (Tavily key set, backend `tavily`).
- **Steps:** `python scripts/probe_query_graph.py -q "What did the Federal Reserve decide at its most recent meeting?" --web-search-policy off -v`.
- **Expected:** no `search_web` step, `external_refs=[]` — the probe's invariant `search_web was called with AGENT_WEB_SEARCH_POLICY=off` would flag any leak; `used_rag_fallback=True` is likely (no strong wiki hit) and the answer is built from whatever weak wiki/chunk hits came back, or says the knowledge base does not cover it — but only from `raw/` material. `PROBE PASS`.
- **Evidence:** the run block.

#### D-09 — Policy `weak`: offered only after the chunk fallback **(O)**

- **Steps:** two probe runs, `--web-search-policy weak -v`: (a) the D-08 outside-the-wiki question, (b) a D-03 inside-the-wiki question.
- **Expected:** (a) `used_rag_fallback=True`, at most one `search_web` step, `external_refs` populated with real URLs, `citations` still only `raw/` ids (possibly empty); (b) `used_rag_fallback=False` and **no** `search_web` — the invariant `search_web was called under policy=weak without the chunk fallback` guards this, and in LangSmith the `agent_step` run's tool schema (`action.enum`) for (b) does not even list `search_web`. Both `PROBE PASS`.
- **Evidence:** both run blocks; a screenshot/paste of the (b) `agent_step` input schema.

#### D-10 — Policy `always`: web results are never citations; the per-question web cap holds **(O)**

- **Steps:** `python scripts/probe_query_graph.py -q "<D-08 question>" --web-search-policy always --max-tool-calls 4 --max-web-searches 1 -v`; then the same with `--max-web-searches 2`.
- **Expected:** `search_web` appears at most once (then at most twice); after the cap the tool is withdrawn from the offer (the decision log shows further steps using only the wiki tools, or `stop`); every `external_refs` URL is absent from `citations`; the answer text presents the web material under the external heading, not as a `[source_id]` citation. `PROBE PASS`.
- **Evidence:** both run blocks.

#### D-11 — Backend `none` with policy `always`: no tool is even constructed

- **Steps:** `python scripts/probe_query_graph.py -q "<D-08 question>" --web-search-backend none --web-search-policy always -v`.
- **Expected:** no `search_web` step, `external_refs=[]`; the invariant `search_web was called with WEB_SEARCH_BACKEND=none (no tool should exist)` is what would catch a regression. `PROBE PASS`.
- **Evidence:** the run block.

#### D-12 — Search backend failure becomes an observation, not a 500

- **Steps:** `TAVILY_API_KEY=tvly-invalid python scripts/probe_query_graph.py -q "<D-08 question>" --web-search-policy always -v`; and via the server: `curl -s 'localhost:8011/answer?q=<same>' | jq '{text: .text[:120], steps, external_refs}'` after setting the bad key and restarting.
- **Expected:** the run completes (`PROBE PASS`), the decision log or a step shows `search_web failed: …` as the tool's observation, `external_refs=[]`, and the model answers from the wiki or with the no-answer text; the server returns 200 with an `Answer`, never a 500. Restore the key.
- **Evidence:** the observation line and the HTTP status.

#### D-13 — One `answer()` is one trace of the documented shape

- **Preconditions:** S-D.3 (tracing on in this process).
- **Steps:** `python scripts/probe_query_graph.py -q "<D-03 question>" --verify-trace -v`; open the printed `run_id` in LangSmith → project `llmwiki-manual-cd`.
- **Expected:** the probe prints the tree — `answer_query [chain]` root; `retrieve`, `agent` (≤ cap+1), `tools` (= number of steps), `select_skills`, `generate`, `resolve_citations`; LLM runs named `agent_step` and `answer_query` (never `ChatOpenAI`) with prompt/completion token counts; a `search_chunks`/`get_page` **tool** run under each `tools`; `trace: ok`. In the UI the root's metadata shows `agent_max_tool_calls` and `agent_web_search_policy`. Also `llmwiki ask "<question>"` prints `run_id: …` on its last line.
- **Evidence:** the printed tree; the LangSmith URL.

#### D-14 — Feedback attaches to that run; refused when it cannot

- **Steps:**
  ```bash
  llmwiki feedback <run_id from D-13> --score 0 --correction "Should cite <id of ORB_Day_Trading>. must mention: opening range, breakout"
  curl -s -X POST localhost:8011/feedback -H "Authorization: Bearer $INGEST_API_TOKEN" -H 'Content-Type: application/json' \
       -d '{"run_id": "<run_id>", "score": 0.5, "correction": "partly right"}'
  curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8011/feedback -H 'Content-Type: application/json' -d '{"run_id": "<run_id>", "score": 0}'   # no token
  LANGSMITH_TRACING=false llmwiki feedback <run_id> --score 0; echo exit=$?
  ```
- **Expected:** `recorded feedback <uuid> on run <run_id>`; the REST call returns `{"ok": true, "feedback_id": …}`; the no-token call is `401`; with tracing off the CLI prints `record_feedback needs LANGSMITH_TRACING=true …` and exits 1 (the REST equivalent is `409`). In LangSmith the run shows feedback key `correctness` with the comment.
- **Evidence:** the four outputs.

#### D-15 — Golden set, local table, gated exit code

- **Preconditions:** S-D.5 (three rows).
- **Steps:** `python scripts/eval_answer.py --dataset eval/manual-cd.jsonl; echo exit=$?`; then append the deliberately failing row and run again.
- **Expected:** first run: a row per example with `citations_resolve 1.00`, `expected_source_cited 1.00`, `must_mention 1.00`, a `tool_calls` metric, `tools: [...]` per row; summary means; `EVAL PASS`, exit 0. Second run: the added row `FAIL` on `must_mention 0.00`, `EVAL FAIL`, exit 1 — the gate works. Remove the row (or keep it for D-17). An `expected_source_cited 0.00` across *every* row means the ids in the JSONL do not match S-0.3 — fix the set, not the code.
- **Evidence:** both summaries and exit codes.

#### D-16 — Push and run as a LangSmith experiment, with metadata

- **Steps:** `python scripts/eval_answer.py --dataset eval/manual-cd.jsonl --push`; then `--langsmith --judge`.
- **Expected:** `pushed to LangSmith dataset 'llmwiki-manual-cd' (<id>)` and the dataset shows the rows in the UI; the experiment prints its name; in the UI each example has the five scores (`judge_grounded` included) and a trace behind it; the experiment metadata carries `version`, the git sha, the `routes` (op → provider/model) and the bounds — that is what makes two experiments comparable after a route change.
- **Evidence:** experiment name/URL; a paste of the metadata.

#### D-17 — Export failures; promote a correction into the set

- **Steps:** `python scripts/eval_answer.py --dataset eval/manual-cd.jsonl --export-failures eval/failures.jsonl` (with the failing row present); `cat eval/failures.jsonl`; then `python scripts/eval_answer.py --dataset eval/manual-cd.jsonl --promote-feedback`.
- **Expected:** `failures.jsonl` has the failing row with the actual answer text/citations attached; `--promote-feedback` prints `promoted 1 corrected run(s)` (the D-14 correction — feedback must be on the root `answer_query` run of `LANGSMITH_PROJECT` and carry a comment) and appends a row whose `expected_sources` and `must_mention` were parsed from the correction text. Re-run the local table: the promoted row scores.
- **Evidence:** the appended JSONL line.

#### D-18 — The judge is its own routed op and never runs on the query path

- **Steps:** `python scripts/eval_answer.py --dataset eval/manual-cd.jsonl --judge`; in LangSmith, open one experiment trace from D-16 and one D-13 query trace.
- **Expected:** local rows gain `judge_grounded` (0/1 with a one-line comment); the experiment trace contains an LLM run named `judge_answer` on the route `llmwiki status` shows for it; the plain query trace (D-13) contains **no** `judge_answer` run. `llmwiki cost` is unchanged by any of this — the query/eval path does not write the compile ledger (see §5).
- **Evidence:** one row's judge line; the two trace observations.

#### D-19 — Reasoning-model token exhaustion does not reach the user **(O)**

- **Purpose:** the 2026-09-16 finding — `finish_reason=length` with empty content once tool results enlarged the context.
- **Steps:** the D-06 broad question with `--max-tool-calls 4 -v --verify-trace`; in the trace check the `answer_query` LLM run's completion tokens.
- **Expected:** non-empty answer text (the probe's `empty answer text` invariant); completion tokens comfortably below the `answer_query` row's `max_tokens` (8192). An empty answer with completion = `max_tokens` is a FAIL to be fixed in `config/ops.py` (plan §20.5), not in code.
- **Evidence:** the completion-token figure.

#### D-20 — In the container (optional; N/A for bare-metal)

- **Steps:** `docker compose --profile ops run --rm eval --offline`; copy the golden set under the one directory the `eval` service mounts (`./.data` → `/data`): `mkdir -p .data/eval && cp eval/manual-cd.jsonl .data/eval/`, then `docker compose --profile ops run --rm eval --dataset /data/eval/manual-cd.jsonl --langsmith --judge`; `curl -s 'localhost:8011/answer?q=<D-03 question>' | jq '{steps, run_id}'` against the `api` service.
- **Expected:** `EVAL PASS` offline; a new experiment from the container with the image's `version`/sha in its metadata; the API's answer carries `steps` and a `run_id` when the container's env has tracing on.
- **Evidence:** experiment name; the `curl` output.

#### D-21 — Regression gate

- **Steps:** S-0.1 again, with whatever `.env` state the session ended in.
- **Expected:** identical to S-0.1 — the unit suite pins `AGENT_MAX_TOOL_CALLS=0`, a temp skills dir and a temp routing table through `tests/conftest.py`'s autouse fixtures, so `.env` changes made during this plan must not alter any result.
- **Evidence:** the pytest summary line.

### 3.4 Teardown

Restore `.env` to S-D.2 (`AGENT_WEB_SEARCH_POLICY=off`, `WEB_SEARCH_BACKEND`
as the deployment wants it, `LANGSMITH_PROJECT` back to the production
name). `eval/manual-cd.jsonl` and `eval/probe-runs.jsonl` are worth keeping
— the former is the start of this corpus's real golden set; `eval/` is not
tracked.

---

## 4. Results log

Copy this table into the session's record (or fill it in place in a branch
of this file) — one row per case, date, environment, PASS/FAIL/BLOCKED/N/A,
and the evidence pointer (log path, LangSmith URL, ledger line).

| Case | Title | Date | Env | Result | Evidence / notes |
|---|---|---|---|---|---|
| S-0.1 | Automated baseline | | dev | | |
| C-01 | Baseline: local providers inactive | | dev | | |
| C-02 | Endpoint reachable, key accepted, model listed (+3 negatives) | | dev→ML3090 | | |
| C-03 | Plain + forced-tool-call completions through the adapter | | dev→ML3090 | | |
| C-04 | Inactive provider named by an op fails at startup | | dev | | |
| C-05 | Routed model not served fails loudly | | dev→ML3090 | | |
| C-06 | Flip visible in `status` and `/healthz` | | dev | | |
| C-07 | Real routed op reaches the local server | | dev→ML3090 | | |
| C-08 | Real compile: local stages, $0 ledger, real tokens | | dev→ML3090 | | |
| C-09 | Compiled output usable (O) | | dev | | |
| C-10 | Cloud + vLLM + llama.cpp active at once | | dev→ML3090 | | |
| C-11 | Server down: loud failure, no cloud fallback | | dev→ML3090 | | |
| C-12 | Rollback restores cloud routes | | dev | | |
| C-13 | From a container (optional) | | container | | |
| C-14 | Regression gate with rows flipped | | dev | | |
| D-01 | Offline gate | | dev | | |
| D-02 | Bounds in force are the ones configured | | dev | | |
| D-03 | Live loop, invariants hold (O) | | dev | | |
| D-04 | Cap 0 = Phase 0 sequence | | dev | | |
| D-05 | Cap 1: the cap ends the loop (O) | | dev | | |
| D-06 | Context budget / repeat guard (O) | | dev | | |
| D-07 | `no_answer` short-circuit | | dev | | |
| D-08 | Policy off ignores the key | | dev | | |
| D-09 | Policy weak needs the fallback (O) | | dev | | |
| D-10 | Policy always: refs never citations, web cap (O) | | dev | | |
| D-11 | Backend none + policy always | | dev | | |
| D-12 | Search failure is an observation | | dev | | |
| D-13 | One trace of the documented shape | | dev + LangSmith | | |
| D-14 | Feedback attaches / is refused | | dev + LangSmith | | |
| D-15 | Golden set, local table, exit code | | dev | | |
| D-16 | Push + experiment + metadata | | dev + LangSmith | | |
| D-17 | Export failures; promote feedback | | dev + LangSmith | | |
| D-18 | Judge is its own op, never on the query path | | dev + LangSmith | | |
| D-19 | Reasoning-model token exhaustion (O) | | dev | | |
| D-20 | In the container (optional) | | container | | |
| D-21 | Regression gate | | dev | | |

---

## 5. Known limitations — what this plan does not (and cannot) verify

- **No automatic failover in C.** `RoutingLLMClient` dispatches by table;
  "llama.cpp fallback" means an op *can be pointed* at it, not that vLLM
  errors are retried there. C-11 pins the actual behaviour. A retry/failover
  layer would be a design change (KB design §4.8.1), not a test gap.
- **The query path writes no cost ledger.** `wiki/_meta/cost.jsonl` is
  appended by the compiler only; `agent_step`/`answer_query`/`judge_answer`
  spend is visible in LangSmith token counts (D-13, D-19) and nowhere else
  offline. Cost-bounding of a question is therefore verified as *call counts*
  (agent nodes ≤ cap+1, `agent_step` runs ≤ 2 × (cap+1)), not dollars.
- **(O) cases depend on the model.** `glm-5.3-flash` at temperature 0.2 has
  been seen to answer at once on short prompts and to answer in prose on very
  long ones (the `STEP_ATTEMPTS` retry exists for that). A tool never being
  called is not a defect; a bound being exceeded is.
- **The offline probe never exercises the loop** (`FakeLLM` answers
  `agent_step` with `answer`). Its value is the invariant and matrix
  plumbing; the loop's own logic is `tests/unit/test_agent_graph.py`.
- **Integration tests are still opt-in and have not run as a suite**
  (`CLAUDE.md`); `tests/integration/test_langsmith_eval.py` is the one worth
  running alongside D-16 since it needs only the LangSmith key.

---

## Appendix A — Ingest entry points on this version (reference)

Asked during the 2026-09-17 review: *how many input channels feed ingest?*
Every one below lands on the same core function,
`tools.ingest_source()` (`src/llmwiki/tools.py`), so the five source kinds
(PDF file, blog URL, YouTube URL, pure text, text file) behave identically
regardless of the door they came through — the modality is detected by the
pipeline, never declared by the caller.

| # | Channel | Entry point | How it reaches ingest |
|---|---|---|---|
| 1 | **REST (JSON)** | `POST /ingest` — `src/llmwiki/api/routes.py` | `{"url": …}` or `{"text": …}`; bearer `INGEST_API_TOKEN` |
| 2 | **REST (multipart)** | `POST /upload` — `src/llmwiki/api/routes.py` | file upload (PDF, `.txt`/`.md`, HTML, image); same token |
| 3 | **MCP tool** | `ingest_source` — `src/llmwiki/mcp/server.py` | FastMCP mounted inside the FastAPI app (`api/app.py`); optional at runtime — a missing/incompatible `fastmcp` degrades to REST-only |
| 4 | **CLI** | `llmwiki ingest --url / --file / --text` — `src/llmwiki/cli.py` | no server needed |
| 5 | **Telegram bot** | `POST /channels/telegram/webhook` — `src/llmwiki/channels/telegram.py` | mounted only when `TELEGRAM_BOT_TOKEN` is set; text / bare URL / document / photo |
| 6 | **Email (Mailgun)** | `POST /channels/email/inbound` — `src/llmwiki/channels/email.py` | mounted only when `MAILGUN_SIGNING_KEY` is set; one ingest per attachment, plus the body or a bare URL |

Plus the **Python API** itself (`from llmwiki import tools;
tools.ingest_source(...)`) for in-process callers — what `smoke_flow.py`,
`eval_answer.py` and `probe_query_graph.py` use.

Two ways to count, depending on the question:

- **Transports** (how a caller reaches the service): REST, MCP, CLI, Python —
  **4**. This is what
  `tests/unit/test_tools_and_mcp.py::test_every_transport_can_ingest_all_five_source_kinds`
  guards: a source kind reachable from one transport but not the others is
  how that surface drifts.
- **Capture channels** (the design doc's "Capture Layer" — things a person
  *sends into*): Telegram and email — **2**, both optional, webhook-based,
  verified under workstreams A/B (`docs/phase1-testing-guide.md` §2–§3,
  `tests/unit/test_channels.py`).

Relevance to this plan: C-08 / C-13 use the CLI and `POST /ingest` doors;
any of the six would exercise the same local-routed compile stages, since the
routing decision is made in `LLMClient.complete(op=...)`, downstream of every
entry point.
