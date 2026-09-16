# Phase 1 testing guide

**This is a living document.** It is not a one-shot write-up — it stays at this
path (`docs/phase1-testing-guide.md`) and keeps being updated in place as the
rest of Phase 1's workstreams (local-LLM routing flip, LangGraph query flow +
LangSmith eval) land and need their own recap/testing steps added alongside
the two sections below.

---

## 1. Phase 1 implementation

Phase 1 (KB design §5) is four workstreams. Status as of 2026-09-14:

| # | Workstream | Status |
|---|---|---|
| A | Telegram capture channel (webhook mode) | **Done** — 2026-09-11 |
| B | Email capture channel (Mailgun inbound-parse webhook) | **Done** — 2026-09-11 |
| C | Local-LLM routing (vLLM primary, llama.cpp fallback) | Config/docs groundwork only — **not flipped on**; see §4 for vLLM setup |
| D | LangGraph query flow + lean LangSmith eval | Not started |

Sources: `HISTORY.md`'s 2026-09-11 entry (workstreams A/B) and 2026-09-14
entries (workstream C's provider split), and `CLAUDE.md`'s "Current State"
section.

### A/B — Capture channels

Both are webhooks the provider calls — nothing polls, nothing runs as a
background process. Each channel's router is **optional**: `_mount_channels`
(`src/llmwiki/api/app.py`) only mounts it when its secret env var is set, so a
deployment with neither `TELEGRAM_BOT_TOKEN` nor `MAILGUN_SIGNING_KEY`
configured simply has neither route registered (`404`, not `401`).

| | Telegram | Email |
|---|---|---|
| Route | `POST /channels/telegram/webhook` | `POST /channels/email/inbound` |
| Auth | `X-Telegram-Bot-Api-Secret-Token` header, `secrets.compare_digest` | HMAC-SHA256 of `timestamp+token`, keyed by `MAILGUN_SIGNING_KEY` |
| Env var(s) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | `MAILGUN_SIGNING_KEY` |
| Maps to | `tools.ingest_source(...)` — text/bare-URL/document/photo | `tools.ingest_source(...)` — one call per attachment, plus the body if it's more than a caption |
| Source | `src/llmwiki/channels/telegram.py` | `src/llmwiki/channels/email.py` |
| Tests | `tests/unit/test_channels.py` | `tests/unit/test_channels.py` |

### C — Local-LLM routing (not yet flipped on)

As of 2026-09-14, `vllm` and `llamacpp` are their own entries in
`llmwiki.llm.providers.REGISTRY` (`src/llmwiki/llm/providers.py`) — each still
wraps `ChatOpenAI`/`langchain_openai`, the same class `"openai"` uses, since
both servers expose an OpenAI-compatible route, but each is resolved in
`config/providers.py` to its **own** `*_API_KEY`/`*_BASE_URL` pair
(`VLLM_API_KEY`/`VLLM_BASE_URL`, `LLAMACPP_API_KEY`/`LLAMACPP_BASE_URL`).
This supersedes the original 2026-09-11 groundwork, which reused the shared
`"openai"` row/`OPENAI_BASE_URL` for whichever local server was running — a
real cloud OpenAI key and a local endpoint could never both be active at the
same time under that scheme. See `docs/implement-plan-v1.4.md` §7.4's
2026-09-14 addendum for the full reasoning.

Not yet flipped on: `config/ops.py`'s local-routing example is still
commented out (no RTX 3090 endpoint is reachable yet). See that file's
comment for the two rows to uncomment once it is.

### D — LangGraph query flow + LangSmith eval

Not started.

### Test coverage

`pytest` — 362 passed, 1 skipped, 6 deselected (integration, opt-in) as of
2026-09-14; `ruff check .` and `mypy` clean; `python scripts/smoke_flow.py
--offline` — SMOKE PASS. `tests/unit/test_channels.py` (new, 2026-09-11)
covers both channels' auth, message-shape → `ingest_source(...)` mapping, and
optional-mount behavior.

### Files changed/added (commit `9062238`, workstreams A/B)

| File | Change |
|---|---|
| `src/llmwiki/channels/__init__.py` | new |
| `src/llmwiki/channels/telegram.py` | new |
| `src/llmwiki/channels/email.py` | new |
| `src/llmwiki/api/app.py` | modified — `_mount_channels` |
| `src/llmwiki/config.py` | modified — new settings fields |
| `tests/unit/test_channels.py` | new |
| `.env.example` | modified — channel env vars + `setWebhook` curl |
| `HISTORY.md`, `CLAUDE.md` | modified |

---

## 2. A) Testing the Telegram channel

### Step 1 — Create the bot in the Telegram app

Nothing in the repo documents this — it's outside the codebase entirely:

1. Open Telegram (mobile or desktop), search for **@BotFather**, start a chat.
2. Send `/newbot`, follow its prompts for a display name and a unique
   `...bot`-suffixed username.
3. BotFather replies with the bot's **API token** — this is
   `TELEGRAM_BOT_TOKEN`. Treat it as a secret; never commit it.
4. Optional, only matters if you'll add the bot to a group rather than DM it
   directly: `/setprivacy` → **Disable**, so the bot receives every message
   in the group, not just `/commands`.
5. Generate a random string yourself for `TELEGRAM_WEBHOOK_SECRET` — Telegram
   doesn't issue this one:
   ```bash
   openssl rand -hex 20
   ```
6. Put both values in `.env` (never commit it — `.env.example`'s placeholders
   are at the lines named below).

### Step 2 — Start the dev server

```bash
docker compose --profile dev up dev        # http://localhost:8011 (DEV_PORT)
```

Per README's "Dev mode" section — bind-mounts this working tree, no rebuild
needed after a `src/` edit.

### Step 3 — Connect the bot to llm-wiki-svc via Cloudflare Tunnel

This is the step that wires BotFather's bot to the service now running on
your machine. `cloudflared` is a separate binary, not something already in
this repo or the dev container — check and install it first if needed:

```bash
which cloudflared || echo "not installed"

# If missing, on Debian/Ubuntu (WSL2 included):
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

Expose the dev server with a quick tunnel (no Cloudflare account or login
needed for this form — that's only required for the named/persistent tunnel
setup in `docs/deployment-plan-container-hosting.md`'s production flow):

```bash
cloudflared tunnel --url http://localhost:8011
```

Note the printed `https://<random>.trycloudflare.com` URL — this is the
public HTTPS address Telegram will call into.

This is the ad hoc, no-account quick-tunnel flavor, for local testing only.
The *production* setup is a named Tunnel routed at the deployed box — see
`docs/deployment-plan-container-hosting.md`, Phase 4 steps 13–14 — this guide
doesn't reuse that path.

### Step 4 — Register the webhook with Telegram

This is what actually links the bot to the tunneled endpoint. Exact curl
already in `.env.example:117-119`. `<cloudflared-host>` is the random
`https://xxxx.trycloudflare.com` hostname step 3's `cloudflared` command
printed to stdout — a fresh one each time you start a quick tunnel, since no
account/DNS is involved — substitute it in directly. Quote each `-d` value as
shown: an unquoted `url=` with a stray space before the host (easy to
introduce when pasting) splits into an empty `url=` field plus a second, bare
request — Telegram reads the empty `url=` as "clear the webhook" and replies
`"Webhook is already deleted"`, while the stray second request hits your
service directly without the required secret header and 401s with
`{"detail":"invalid webhook secret"}`. Both symptoms together mean this typo,
not a real auth or tunnel problem:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://<cloudflared-host>/channels/telegram/webhook" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

### Step 5 — Verify registration

Not currently documented anywhere else in the repo:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

Look for `"url"` matching your tunnel host and `"last_error_message"` absent.

### Step 6 — Send test messages from the real Telegram app

Per `src/llmwiki/channels/telegram.py`'s `_capture` logic, one message per
capture shape:

| You send | What happens |
|---|---|
| Plain text | Ingested as `text=` |
| A bare URL (including a YouTube link) | Ingested as `url=` (modality auto-detected downstream) |
| A forwarded/attached document (e.g. a PDF) | Downloaded via `getFile`, ingested as `file=` |
| A photo | Largest size downloaded, ingested as `file=`, mime hardcoded to `image/jpeg` |
| Something uncapturable (a sticker, a poll, ...) | Bot replies "Nothing to capture in that message.", nothing ingested |

The bot replies in-chat with **`Captured. source_id=<id>`** for every
successful capture (`_handle_update`'s `_ack` call) — this is the easiest way
to get the id when testing from the real app.

### Step 7 — Local curl alternative (no real Telegram client needed)

Reuse the fixture payload shapes from `tests/unit/test_channels.py`:

```bash
curl -X POST https://<cloudflared-host>/channels/telegram/webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: $TELEGRAM_WEBHOOK_SECRET" \
  -d '{"message": {"chat": {"id": 1}, "text": "just some notes"}}'

curl -X POST https://<cloudflared-host>/channels/telegram/webhook \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: $TELEGRAM_WEBHOOK_SECRET" \
  -d '{"message": {"chat": {"id": 1}, "text": "https://example.org/post"}}'
```

This only exercises the endpoint's own logic — the HTTP response body is
always just `{"ok": true}` (the in-chat "Captured. source_id=..." reply only
happens for a real `chat_id` Telegram can deliver to). A document/photo
payload will still try to call `api.telegram.org/getFile` unless `file_id` is
a real one, so the two examples above (plain-text, bare-URL) are the safe
self-contained smoke tests.

### Step 8 — Confirm ingestion succeeded

```bash
curl https://<cloudflared-host>/sources/<source_id>
```

`GET /sources/{source_id}` (`src/llmwiki/api/routes.py`) returns a
`SourceStatus` — no bearer token needed. There is no list-all-sources route
(every `@router.*` in that file was checked). For a real Telegram test, the
bot's in-chat reply gives you the id directly (Step 6); via the curl
alternative in Step 7, watch `docker compose --profile dev logs -f dev`
instead, since those payloads don't produce a deliverable chat reply.

### Step 9 — Troubleshooting

- **401** — secret header mismatch. The route fails closed: if
  `TELEGRAM_BOT_TOKEN` is set but `TELEGRAM_WEBHOOK_SECRET` is blank, every
  request 401s rather than being accepted unverified.
- **404** — the route isn't mounted at all; `TELEGRAM_BOT_TOKEN` is unset in
  the running container's environment.

### Step 10 — Teardown

Not currently documented elsewhere — so the bot doesn't keep pointing at a
dead tunnel after the session ends:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/deleteWebhook"
```

---

## 3. B) Testing the email (Mailgun) channel

### Step 1 — Prereqs

A Mailgun account with a sandbox or verified sending domain and inbound
routing enabled. Get `MAILGUN_SIGNING_KEY` from the Mailgun dashboard and set
it in `.env` (`.env.example:120-122`).

### Step 2 — Start the dev server + Cloudflare Tunnel

Same as the Telegram steps 2–3 above — one tunnel serves both channels, since
they're different paths on the same FastAPI app:

```bash
docker compose --profile dev up dev
cloudflared tunnel --url http://localhost:8011
```

### Step 3 — Register the inbound route in Mailgun's dashboard

Mailgun **Routes** UI (not sourced from this repo — supplementary detail,
since there's no deeper Mailgun-routes doc here):

- **Expression**: `match_recipient(".*@yourdomain.mailgun.org")` (or your
  verified domain)
- **Action**: Forward to `https://<cloudflared-host>/channels/email/inbound`

### Step 4 — Send a real test email

Per `src/llmwiki/channels/email.py`'s `_capture` logic:

| You send | What happens |
|---|---|
| Plain email, body only | Captured as `text=` (or `url=` if the body is a bare URL), titled with the subject |
| Attachment(s) + a real cover note in the body | Each attachment ingested as its own `file=` source; the cover note ingested separately as `text=`; both titled with the subject |
| Attachment(s), no meaningful body text | Only the attachment(s) ingested |

### Step 5 — Local curl alternative

Mailgun's signature is `sha256(timestamp + token)`, keyed by
`MAILGUN_SIGNING_KEY` (`_verify` in `email.py`). Compute a valid signature —
the exact recipe from `tests/unit/test_channels.py`'s `_mailgun_form`:

```bash
python3 -c "
import hashlib, hmac
key = 'YOUR_MAILGUN_SIGNING_KEY'
timestamp, token = '1', 'tok'
print(hmac.new(key.encode(), f'{timestamp}{token}'.encode(), hashlib.sha256).hexdigest())
"
```

Bare-URL body:

```bash
curl -X POST https://<cloudflared-host>/channels/email/inbound \
  -F timestamp=1 -F token=tok -F signature=<computed-above> \
  -F subject="a link" -F stripped-text="https://example.org/x" \
  -F attachment-count=0
```

Attachment + cover note:

```bash
curl -X POST https://<cloudflared-host>/channels/email/inbound \
  -F timestamp=1 -F token=tok -F signature=<computed-above> \
  -F subject="paper + note" -F stripped-text="please read this" \
  -F attachment-count=1 \
  -F attachment-1=@paper.pdf\;type=application/pdf
```

### Step 6 — Confirm ingestion succeeded

Unlike Telegram, the email channel's HTTP response body directly includes
the ids:

```json
{"ok": true, "source_ids": ["<id1>", "<id2>"]}
```

Poll `GET /sources/{source_id}` for each one, same as Step 8 above.

### Step 7 — Troubleshooting

- **401** — signature mismatch: wrong signing key, or the `timestamp`/`token`
  fields don't match what was actually signed.
- **404** — the route isn't mounted at all; `MAILGUN_SIGNING_KEY` is unset in
  the running container's environment.

### Step 8 — Teardown

Disable or remove the Mailgun route so future mail doesn't 404 against a
dead tunnel once the session ends.

---

## 4. C) Setting up vLLM for local-LLM routing

This wires a real vLLM endpoint into `VLLM_API_KEY`/`VLLM_BASE_URL` so
`config/ops.py`'s commented-out local-routing example (`src/llmwiki/llm/providers.py`,
`config/providers.py` — see `HISTORY.md`'s 2026-09-14 entry) can be
uncommented for real. Covers two targets: **ML3090** (the existing RTX 3090
24GB host, already running llama.cpp) and a **fresh Ubuntu host with an RTX
4060, 8GB VRAM** — llama.cpp itself isn't covered here since it's already
running on ML3090.

> **Sourcing note:** the model choices, VRAM footprints, and vLLM CLI flags
> below are supplementary guidance, not sourced from this repo. vLLM's flags
> occasionally change between releases — cross-check `vllm serve --help` (or
> https://docs.vllm.ai) against whatever version you actually install.

### Step 1 — Check the GPU and what's already using it

```bash
nvidia-smi
```

Confirms the driver/CUDA version and, on ML3090, how much VRAM llama.cpp is
already holding — you'll need that number in Step 4 to avoid both processes
fighting over the same memory.

### Step 2 — Install vLLM in its own virtualenv

A separate venv from llm-wiki-svc's own `.venv` — this runs on a different
host entirely and has its own, much heavier, torch/CUDA dependency chain:

```bash
python3 -m venv ~/vllm-venv
source ~/vllm-venv/bin/activate
pip install --upgrade pip
pip install vllm
```

`pip install vllm` pulls a CUDA-12.1-matched `torch` wheel by default and
supports Python 3.9–3.12. If the host's driver is older, follow vLLM's own
installation docs for the matching wheel rather than fighting the default.

### Step 3 — Pick a model that fits the available VRAM

vLLM's GGUF support is still experimental — unlike llama.cpp, prefer an
**AWQ/GPTQ-quantized** Hugging Face repo over reusing llama.cpp's `.gguf`
files.

| Host | Available VRAM | Recommended model | Quant | Approx. footprint |
|---|---|---|---|---|
| ML3090 (alongside llama.cpp) | ~24GB minus whatever llama.cpp holds (Step 1) | `Qwen/Qwen2.5-14B-Instruct-AWQ` | AWQ 4-bit | ~9–10GB weights + KV cache |
| Fresh Ubuntu + RTX 4060 | 8GB | `Qwen/Qwen2.5-3B-Instruct-AWQ` | AWQ 4-bit | ~2–3GB weights + KV cache |
| RTX 4060, ultra-safe fallback | 8GB | `Qwen/Qwen2.5-1.5B-Instruct` | fp16 | ~3GB |

The ML3090 choice matches `config/ops.py`'s existing commented example
(`"model": "qwen2.5-14b"`) — keep the same family/size so the local model's
behavior is comparable to what llama.cpp is already serving as the fallback.

### Step 4 — Launch the OpenAI-compatible server

Generate the credential first, so you have it on hand for both the server
and `.env`:

```bash
export VLLM_SECRET=$(openssl rand -hex 20)
echo "$VLLM_SECRET"   # save this - it becomes VLLM_API_KEY in Step 8
```

**ML3090** (leaves headroom for llama.cpp — adjust `--gpu-memory-utilization`
against the free VRAM you saw in Step 1):

```bash
source ~/vllm-venv/bin/activate
vllm serve Qwen/Qwen2.5-14B-Instruct-AWQ \
  --served-model-name qwen2.5-14b \
  --host 0.0.0.0 --port 8100 \
  --api-key "$VLLM_SECRET" \
  --gpu-memory-utilization 0.5 \
  --max-model-len 8192
```

**Fresh Ubuntu + RTX 4060, 8GB:**

```bash
source ~/vllm-venv/bin/activate
vllm serve Qwen/Qwen2.5-3B-Instruct-AWQ \
  --served-model-name qwen2.5-3b \
  --host 0.0.0.0 --port 8100 \
  --api-key "$VLLM_SECRET" \
  --gpu-memory-utilization 0.85 \
  --max-model-len 4096
```

`--host 0.0.0.0` matters — vLLM defaults to `127.0.0.1` only, unreachable
from another host. `--served-model-name` is the string a client must send as
`"model"` — it must match `config/ops.py`'s `"model"` value exactly (Step 9).

### Step 5 — Keep it running

`vllm serve` is a foreground process. For anything beyond a one-off test,
either run it in `tmux`/`screen`, or add a small systemd unit so it survives
a reboot:

```ini
# /etc/systemd/system/vllm.service
[Unit]
Description=vLLM OpenAI-compatible server
After=network.target

[Service]
User=<your-user>
Environment=VLLM_SECRET=<the-value-from-step-4>
ExecStart=/home/<your-user>/vllm-venv/bin/vllm serve Qwen/Qwen2.5-14B-Instruct-AWQ \
  --served-model-name qwen2.5-14b --host 0.0.0.0 --port 8100 \
  --api-key ${VLLM_SECRET} --gpu-memory-utilization 0.5 --max-model-len 8192
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vllm
```

### Step 6 — Verify the server directly

```bash
curl http://localhost:8100/v1/models -H "Authorization: Bearer $VLLM_SECRET"

curl http://localhost:8100/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_SECRET" -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-14b", "messages": [{"role": "user", "content": "say hi"}]}'
```

### Step 7 — Reach it from wherever llm-wiki-svc runs

Same LAN: use `http://<vllm-host-LAN-IP>:8100/v1` directly, and firewall
port 8100 to the trusted subnet — `--api-key` is the only auth vLLM offers,
unlike the channels' HMAC/secret-header schemes.

Different network (e.g. llm-wiki-svc on a VPS): reuse the same Cloudflare
Tunnel mechanism as §2/§3:

```bash
cloudflared tunnel --url http://localhost:8100
```

If this ever needs to stay reachable from the public internet rather than a
throwaway test, put a Cloudflare Access policy in front of that tunnel
hostname too — the same reasoning as
`docs/deployment-plan-container-hosting.md`'s Phase 4 step 14, since a bare
inference endpoint behind only an API key is a thinner defense than the
channels' per-request signatures.

### Step 8 — Wire it into llm-wiki-svc's `.env`

```
VLLM_API_KEY=<the value from Step 4>
VLLM_BASE_URL=http://<vllm-host>:8100/v1
```

(`config/providers.py`'s `"vllm"` row already names these two variables —
nothing to change there.)

### Step 9 — Flip on `config/ops.py`'s local-routing example

Uncomment the two commented rows at the bottom of `config/ops.py`, keeping
the `"model"` value identical to whatever `--served-model-name` was launched
with:

```python
{"op": "summarize_source", "provider": "vllm", "model": "qwen2.5-14b",
 "temperature": 1.0, "max_tokens": 2048},
```

### Step 10 — Verify end-to-end

Restart llm-wiki-svc, then:

```bash
curl http://localhost:8011/healthz | jq .config   # confirms routing mode + resolved paths
```

Ingest a source to trigger `summarize_source` (§2/§3 above, or `POST
/ingest`), and set `LOG_LEVEL=DEBUG` to see which model each stage used in
the logs. Check `wiki/_meta/cost.jsonl` for the entry — `$0.0000` is expected
(local models aren't in `llmwiki.llm.pricing.RATES`), but token counts should
be real and nonzero.

### Step 11 — Troubleshooting

- **Connection refused** — vLLM defaults to `127.0.0.1`; confirm `--host
  0.0.0.0` was passed.
- **CUDA OOM at startup** — lower `--gpu-memory-utilization` or
  `--max-model-len`, or drop to a smaller/more-quantized model; on ML3090,
  re-check `nvidia-smi` (Step 1) for how much llama.cpp is already holding.
- **`RuntimeError: op 'summarize_source' routes to provider 'vllm', which is
  not active`** at llm-wiki-svc startup — `VLLM_API_KEY` is empty/unset in
  *that* process's environment, so `config/providers.py` silently dropped it
  (same failure mode `tests/unit/test_routing_config.py::test_op_naming_an_inactive_provider_fails_loudly`
  guards).
- **401 from vLLM itself** — the `Authorization: Bearer` value doesn't match
  `--api-key` exactly; this is the same string as `VLLM_API_KEY`, so a typo
  in either place breaks the pair.
