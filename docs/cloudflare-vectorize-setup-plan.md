# Plan: stand up the real Cloudflare Vectorize backend

**Status:** proposed, not yet executed. Nothing in this plan has been run against a
live Cloudflare account.

## 1. Where things stand today

Checked against the current `.env`:

| Piece | State |
|---|---|
| R2 bucket + S3 credentials | **present** — `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT_URL` all look real |
| `CF_ACCOUNT_ID` / `CF_API_TOKEN` (Vectorize + Workers AI) | **not set** — both still `changeme` |
| `STORAGE_BACKEND` / `VECTOR_BACKEND` / `EMBEDDING_BACKEND` | `local` / `memory` / `fake` — the whole service is still running offline |

So the R2 side of §6.2 was already done in an earlier session; what's missing is the
Cloudflare API token for Vectorize + Workers AI (§6.3), the two indexes themselves
(§6.4), and then flipping the three backend switches. This plan covers exactly that
gap, plus a repeatable way to check it worked before trusting it with real ingests.

Everything below already has a runbook in `docs/implement-plan.md` Part I §6.2–6.4; this plan
sequences it and adds the missing verification step — a single script that checks
structure, credentials and permissions together, rather than finding out about a
scoping mistake from an opaque 403 mid-ingest.

## 2. Steps

1. **Get the account ID.** Fastest: it's already sitting in this `.env` — the
   subdomain of `R2_ENDPOINT_URL` (`https://<ACCOUNT_ID>.r2.cloudflarestorage.com`) *is*
   the account ID, since R2 and Vectorize/Workers AI are the same Cloudflare account.
   Otherwise `wrangler whoami` prints it, or in the dashboard: `Cmd/Ctrl+K` → search
   "Copy account ID", or Workers & Pages → Account details, or a domain's Overview
   page → API section at the bottom (the old "right sidebar" location has moved —
   [current dashboard locations](https://developers.cloudflare.com/fundamentals/account/find-account-and-zone-ids/)).

2. **Create a scoped Account API token** (Dashboard → Manage Account → Account API
   Tokens → Create Custom Token — `wrangler` cannot mint this one; requires **Super
   Administrator** on the account):
   Three separate grants — the two products name their permission groups
   inconsistently (Vectorize: Read/Write; Workers AI: Read/Edit):
   - `Account` → **Vectorize** → **Write** — covers read+write for Vectorize
     (query/describe accept either Read or Write per Cloudflare's own API docs, so
     Vectorize Read isn't needed separately). If "Vectorize" doesn't appear in the
     initial list, search for it.
   - `Account` → **Workers AI** → **Read**
   - `Account` → **Workers AI** → **Edit** — both Workers AI grants are required
     together to run a model via the REST API (which is all `embedding/workers_ai.py`
     does): "that token will need permissions for both `Workers AI - Read` and
     `Workers AI - Edit`"
     ([Cloudflare's own quickstart](https://developers.cloudflare.com/workers-ai/get-started/rest-api/)).
     Read alone, which is what step 2 originally said, is not sufficient.
   - No expiry for now (Phase 0); paste into `CF_API_TOKEN`.

   Deliberately an **Account** token, not a **User** token: a user token is tied to
   whoever created it and stops working the moment that person loses access to the
   account; an account token is owned by the account itself and is Cloudflare's own
   recommendation for a long-lived credential baked into a server's `.env`
   ([Account API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/)).
   One consequence: it verifies against a different endpoint —
   `GET /accounts/{account_id}/tokens/verify`, not `/user/tokens/verify` — which is
   why `check_cloudflare_setup.py` below is account-scoped, not user-scoped.

   This is also deliberately *not* the same token as the R2 S3 keys — R2 uses
   S3-style credentials, Vectorize/Workers AI use a Cloudflare API token. Keeping them
   separate means an R2 credential leak doesn't carry Vectorize/Workers AI access, and
   vice versa.

3. **Run the new diagnostic script in `--quick` mode** (no writes yet — just confirms
   the token is real and scoped correctly before creating anything):

   ```bash
   python scripts/check_cloudflare_setup.py --quick
   ```

   Expect R2, the token, and Workers AI to pass; Vectorize will report the two indexes
   missing — that's expected at this point.

4. **Create the indexes.**

   ```bash
   python scripts/bootstrap_indexes.py --create
   ```

   Creates `llmwiki-chunks` and `llmwiki-gists` at `EMBEDDING_DIM` (768, cosine), plus
   the `source_id`/`slug`/`type` metadata indexes `vector/vectorize.py` filters on.
   Metadata indexes must exist before the first insert — this is why it's a separate
   step from ingest, not something ingest creates lazily.

5. **Run the full diagnostic** (adds a live upsert → query → delete round trip — the
   only way to actually prove the token's *Write* permission works, not just that the
   token itself is valid):

   ```bash
   python scripts/check_cloudflare_setup.py
   ```

   All six checks should pass. If the write step fails, the token itself is valid but
   is almost always missing the Vectorize Write scope, or the R2 token got
   copy-pasted into `CF_API_TOKEN` by mistake.

6. **Flip the three backend switches** in `.env` (R2 creds are already there; this is
   the only line-count that actually changes runtime behavior):

   ```bash
   STORAGE_BACKEND=r2
   VECTOR_BACKEND=vectorize
   EMBEDDING_BACKEND=workers_ai
   ```

7. **Run the opt-in integration suite** — this is the authoritative correctness check
   (it exercises the real code paths in `storage/r2.py`, `embedding/workers_ai.py`,
   `vector/vectorize.py`, not a parallel script):

   ```bash
   pytest -m integration
   ```

8. **One real smoke test** end to end through the actual service:

   ```bash
   python scripts/smoke_flow.py --url https://arxiv.org/abs/2005.11401
   curl -s localhost:8010/healthz | jq   # once uvicorn is running (API_PORT in .env.example)
   ```

9. **Cost hygiene** (§6.9): set a monthly spend limit in the Anthropic console, and
   optionally the R2 lifecycle rule for `raw/` → Infrequent Access after 90 days
   (§6.2 step 5). Nothing in this plan changes the "never on every ingest" rule for
   global lint/compilation.

## 3. What the new script actually checks

`scripts/check_cloudflare_setup.py` — six independent, individually-reported checks:

1. **Config** — required `.env` values are present and not left as `changeme`.
2. **R2** — put/get/delete round trip against the real bucket (proves the S3
   credentials and the bucket-scope permission).
3. **Cloudflare API token** — `GET /accounts/{account_id}/tokens/verify` (the
   account-scoped endpoint, since this should be an Account API token — see step 2
   above) confirms the token is valid and active (this alone does *not* prove it has
   the right scopes — that's check 6).
4. **Workers AI** — one real embedding call; asserts the returned dimension equals
   `EMBEDDING_DIM` (the single most common Vectorize failure mode per §6.3, and the
   reason it's checked before touching Vectorize at all).
5. **Vectorize structure** — both configured indexes exist, dimensions match, and the
   `source_id`/`slug`/`type` metadata indexes are present.
6. **Vectorize write** (skippable with `--quick`) — upserts one throwaway vector,
   polls for it to become queryable (writes are eventually consistent), then deletes
   it and confirms the delete took. This is the step that actually proves *Write*
   permission, since checks 3 and 5 only need read-level access to pass.

Each check prints `[OK]` / `[FAIL]` / `[SKIP]` with a one-line remediation hint on
failure, and the process exits non-zero if anything failed — usable both by hand and
in a pre-flight CI/cron step later.

This is deliberately a superset of, not a replacement for, `scripts/bootstrap_indexes.py`
(structure only, no credentials/permission check, and it *creates* things) and
`tests/integration/test_cloudflare.py` (pytest-gated, asserts exact behavior, is the
real regression net). The new script is the fast, human-readable preflight you run
once by hand before trusting either of those with a real account.

## 4. Test section (per `CLAUDE.md`)

- **No regressions.** The change adds one new script and this doc; nothing under
  `src/llmwiki/` changes, so the full existing suite is unaffected — `pytest` (315
  tests) is expected to pass unchanged, and `python scripts/smoke_flow.py --offline`
  is expected to still SMOKE PASS.
- **Obsolete tests.** None. Nothing existing is removed or superseded.
- **New tests.** No unit test is added *for* `check_cloudflare_setup.py` itself,
  matching the existing convention: `scripts/bootstrap_indexes.py` (the closest
  analog — also a standalone operational script that talks to the real Cloudflare
  API) has no unit test either, because every one of its meaningful behaviors
  requires a live account and is already covered, for the underlying adapters, by
  `tests/integration/test_cloudflare.py`. Flagging this explicitly rather than
  silently skipping it — if a network-mocked unit test for the new script's argparse
  wiring and pass/fail bookkeeping is wanted anyway, that's a small addition and I'll
  add it on request.
- **Documentation.** This plan doc is the record for now; once the script is written
  and (ideally) run once against a real account, the outcome gets logged in
  `HISTORY.md` per the mandatory-logging rule, including which of the six checks
  passed and the resulting `.env` backend values.
