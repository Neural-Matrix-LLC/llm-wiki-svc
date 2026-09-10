# Deployment Plan — llmwiki as a Docker Container: Cloudflare vs Hostinger

Status: **proposal, not yet approved or implemented.** Nothing in this document
has been executed. Per `CLAUDE.md`'s rule that any plan touching more than
~3 files gets proposed first, this is that proposal — implementation starts
only after the recommended path is confirmed. No `HISTORY.md` entry exists
yet because no change has been made; one is added when a path is actually
deployed.

## 0. tl;dr

Both platforms can run the existing image as-is. The deciding factor is
**not** hosting cost — it's a real architectural mismatch between how
`llmwiki` currently does ingestion and how Cloudflare Containers scale.

- **Recommended for now: Hostinger KVM VPS + Docker Compose**, fronted by a
  free Cloudflare Tunnel for TLS/DNS. The image is **built and pushed to
  Docker Hub locally**; the VPS only ever pulls and runs it — no build
  toolchain, no repo clone, no source code on the box at all. It's a
  near-zero-change deployment of what's already in this repo (`Dockerfile`,
  `docker-compose.yml`), it has zero risk to the background-task ingest
  design, and its flat price is competitive with Cloudflare's own required
  floor.
- **Cloudflare Containers is the better long-term fit** — this project is
  already Cloudflare-native (R2, Vectorize, Workers AI) — but only *after*
  the ingest pipeline moves off in-process `BackgroundTasks` onto something
  that survives a container being scaled down mid-job (e.g. Cloudflare
  Queues). That refactor is out of scope for Phase 0.

Full reasoning below; §6 is the concrete deployment plan for the recommended
path; §7 shows what changing to Cloudflare Containers would look like later.

## 1. What's actually being deployed

Grounded in the repo as it exists today, not in the abstract:

- **`Dockerfile`** — multi-stage, non-root (`uid 10001`), exposes `8000`,
  `HEALTHCHECK` against `/healthz`, `VOLUME /data` used only when
  `STORAGE_BACKEND=local`. Already production-shaped. Port `8000` is
  internal-only (the container's own bind port); the *host*-facing port is
  whatever `docker-compose.yml` maps it to via `API_PORT` — **8010** on
  Hostinger, since 8000 there is already assigned to something else (§6
  Phase 3).
- **`docker-compose.yml`** — `api` (real backends via `.env`), `api-offline`
  (fake adapters, port 8001), `smoke` (runs `scripts/smoke_flow.py
  --offline` against the built image), `lint` (the scheduled global-lint
  profile, design doc §4.4's "not on every ingest" rule). In production the
  `api`/`lint` services pull a prebuilt image from Docker Hub instead of
  building on the box (§6 Phase 3) — `build:` stays in the file for local
  dev (`docker compose up --build` keeps working exactly as today);
  `image:` is what actually gets pulled on the VPS, and needs a real Docker
  Hub repo path instead of the current bare local tag `llmwiki:latest`
  (§8).
- **Storage is already Cloudflare, regardless of where compute runs.**
  `STORAGE_BACKEND=r2`, `VECTOR_BACKEND=vectorize`,
  `EMBEDDING_BACKEND=workers_ai` in `.env.example` mean R2 + Vectorize +
  Workers AI are called over their public APIs. R2 has no egress fees, and
  Vectorize/Workers AI are billed per request/neuron, not by caller
  location — so **the storage/vector/embedding bill is identical no matter
  which vendor hosts the container.** The only thing genuinely in play here
  is *where the FastAPI/MCP process itself runs.*
- **The one fact that matters most for this decision:** `POST /ingest` and
  `POST /upload` ([routes.py:58-89](src/llmwiki/api/routes.py#L58-L89))
  return a `SourceRef` immediately and hand `tools.process_source` to a
  FastAPI `BackgroundTasks` — extraction, embedding, and incremental
  compilation all run **after the HTTP response has already been sent, in
  the same process.** There is no queue, no separate worker (decision D7:
  single process, on purpose, for Phase 0). This means: **the moment a
  platform can suspend or kill the container because "the request finished,"
  an in-flight ingest can be silently lost** — the raw object may land in
  R2 with no compiled wiki page or vector entries to show for it.

## 2. Option A — Cloudflare Containers

Cloudflare's own container product (Workers-orchestrated Docker containers,
GA since 2025): you push the image, Wrangler defines a container binding,
Workers routes HTTP to it, and it scales toward zero instances when idle.

**Fit:**
- Same account as R2/Vectorize/Workers AI already in `.env.example` — one
  bill, one dashboard, one set of API tokens.
- Metered pricing (vCPU-seconds + memory-GiB-seconds + disk-GB-time) sits
  behind a **required Workers Paid plan ($5/mo base)** — Containers are not
  available on the free plan.
- Scale-to-zero is exactly the right shape for a single-researcher, bursty
  workload *if* every request were self-contained.

**The problem: it isn't.** `min_instances=0` (the cost-saving default) means
Cloudflare is free to spin the container down right after the `/ingest`
response is returned — precisely when `tools.process_source` starts running
in the background. Two ways to fix it, both with a real cost:
1. **Set `min_instances=1`.** One instance stays warm continuously, which
   removes most of the scale-to-zero savings and makes the pricing
   comparison to Hostinger close to apples-to-apples (§4).
2. **Refactor ingestion onto Cloudflare Queues** (Worker enqueues on
   `/ingest`, a queue consumer — itself a Container — does the compile).
   Correct fit for Containers, but it's new infrastructure and a pipeline
   change, not a hosting swap. This is Phase 1+ work, not Phase 0
   ("do not build Phase 2/3 features... unless explicitly asked" —
   `CLAUDE.md`).

**Other things to verify before committing** (Cloudflare Containers pricing,
regional availability, and per-instance memory/disk tiers have all moved
since my last confirmed knowledge — check
https://developers.cloudflare.com/containers/ and the current pricing page
before budgeting):
- exact vCPU-second / GiB-second / disk rates and what's included in the
  $5/mo Workers Paid allowance
- max concurrent instances and per-instance memory ceiling
- cold-start latency with `min_instances=0` (matters if you ever do want it)

## 3. Option B — Hostinger VPS + Docker

A standard KVM VPS (Ubuntu + Docker), not a managed container product. You
SSH in and run close to exactly what's already in `docker-compose.yml`.

**Fit:**
- **Near-zero delta from what's already built and tested locally.** The
  image is built and pushed to Docker Hub from your own machine; the box
  only ever runs `docker compose pull && up -d` against `.env` populated
  the same way it is for local real-backend runs. No build toolchain on the
  VPS, no repo clone, no new config language, no new mental model — and
  deploys are faster and cheaper than rebuilding on a small KVM instance
  every time.
- **Always-on process removes the background-task risk entirely** — nothing
  ever suspends the container mid-compile. This is the main reason it's the
  safer default for the current architecture.
- Flat, predictable monthly price; no metering surprises.
- No native S3-compatible object storage bundled into every plan the way R2
  is bundled into a Cloudflare account, but that's moot here — `STORAGE_BACKEND=r2`
  stays R2 regardless of who hosts compute (§1).
- **No built-in edge/DNS/TLS/DDoS layer** the way Cloudflare gives you for
  free in front of a Worker. Recommend fronting the VPS with a **Cloudflare
  Tunnel** (`cloudflared`, free): no inbound port opened on the VPS at all,
  free TLS via Cloudflare's edge, and — importantly given
  `INGEST_API_TOKEN` is explicitly a "static bearer token (Phase 0 only)"
  ([.env.example](.env.example)) — you can put a **Cloudflare Access**
  policy in front of `/ingest` and `/upload` for real authentication instead
  of relying on the static token alone.

**Sizing:** the service itself is a single lightweight FastAPI/uvicorn
process with no local vector index or local LLM (`STORAGE_BACKEND=r2`,
`VECTOR_BACKEND=vectorize`) — the smallest KVM tier is enough. Headroom
matters only for concurrent PDF extraction (`pymupdf`) and video/blog
fetches, which are bursty, not sustained.

## 4. Side-by-side

| | Cloudflare Containers | Hostinger VPS + Docker |
|---|---|---|
| Matches existing `Dockerfile`/`docker-compose.yml` | Needs a Wrangler container definition layered on top | Runs the compose file almost unchanged |
| Background-task ingest (D7) safety | **At risk** unless `min_instances=1` or the pipeline is re-architected | Safe — process never suspends mid-job |
| Pricing model | Metered, scale-to-zero *if* `min_instances=0` is safe to use (it isn't yet) | Flat monthly, always-on |
| Required floor | $5/mo Workers Paid, plus metered usage | ~$5-9/mo entry KVM tier (verify current listing) |
| Storage/vector/embedding cost (R2/Vectorize/Workers AI) | Identical either way (§1) | Identical either way (§1) |
| Edge TLS/DNS/DDoS | Built in (Workers routing) | Needs Cloudflare Tunnel (free, easy) or a reverse proxy you manage |
| Vendor lock-in | Cloudflare-specific container runtime & Wrangler config | Portable — any VPS/Docker host works the same way |
| Best-fit phase | Once ingestion is queue-based (Phase 1+) | Phase 0, as built today |

## 5. Recommendation

**Deploy on Hostinger (KVM VPS) now, behind a Cloudflare Tunnel, using the
existing `docker-compose.yml` almost unchanged** — built and pushed to
Docker Hub locally, pulled and run on the box. It is the lower-risk,
lower-effort choice for the architecture as it exists in this repo today,
and its cost is not meaningfully worse than Cloudflare's own required floor
once Cloudflare is used safely (`min_instances=1`).

Revisit Cloudflare Containers when/if the ingest path moves to Cloudflare
Queues — at that point Cloudflare becomes strictly better (same-vendor
billing, true scale-to-zero, no separate box to patch/reboot).

## 6. Deployment plan — Hostinger VPS + Docker (recommended path)

### Phase 1 — Provision
1. Hostinger KVM 1 (or 2 for headroom), Ubuntu 22.04/24.04 LTS, Docker
   pre-installed template if offered, else install Docker Engine + Compose
   plugin manually.
2. Create a non-root deploy user with `docker` group membership; disable
   root SSH login; key-based auth only.

### Phase 2 — Secrets
3. Do **not** commit real values. Copy `.env.example` → `.env` on the box
   only, populate real Cloudflare (`CF_ACCOUNT_ID`, `CF_API_TOKEN`,
   `R2_*`) and LLM provider credentials, and a real (non-default)
   `INGEST_API_TOKEN`.
4. If `config/providers.py` / `config/ops.py` routing is used in
   production, copy those over too (they hold no secrets themselves, per
   their own docstrings) — confirmed present as untracked files in this
   repo already (`config/ops.py`, `config/providers.py`).
5. Decide whether the Docker Hub repo is public or private. Public is
   simplest (no auth needed to pull); if private, create a Docker Hub
   **access token** (not your account password) for `docker login` on the
   box in Phase 3.

### Phase 3 — Build, push, deploy

The box never builds an image — it only ever pulls one. All building
happens on your own machine.

**Locally (your dev machine):**

6. Build, tagging for Docker Hub (replace `<dockerhub-user>` with your
   actual username/org — see §8 for the matching `docker-compose.yml`
   change):
   ```bash
   docker build -t <dockerhub-user>/llmwiki:latest --target runtime .
   # or, once docker-compose.yml's image: points at the same repo (§8):
   #   docker compose build api
   ```
7. Sanity-check the image **before** it reaches Docker Hub — this replaces
   the old on-box build-time gate, since the box no longer builds anything:
   ```bash
   docker compose --profile test run --rm smoke
   ```
8. Push (tag by date or git SHA too, e.g. `:2026.09.10`, so a bad push is
   reversible by pulling the previous tag on the box):
   ```bash
   docker login                              # once; credentials are cached
   docker push <dockerhub-user>/llmwiki:latest
   ```

**On the VPS:**

9. No `git clone` needed — the box only needs two files:
   `docker-compose.yml` and `.env` (scp them over, or keep a minimal
   deploy-only checkout of the repo for convenience — either way, the
   `Dockerfile` and application source never need to exist on the box).
10. If the Docker Hub repo is private, `docker login` on the box too, using
    the access token from Phase 2 step 5.
11. ```bash
    docker compose pull        # pulls the image just pushed, never builds
    docker compose up -d       # the api service — real backends
    ```
12. Confirm `curl -s http://127.0.0.1:8010/healthz` returns healthy before
    opening it to the internet.

    Port is 8010, not the container's internal 8000: `API_PORT=8010` in
    `.env` on the box, because port 8000 is already assigned to something
    else on this Hostinger VPS. `docker-compose.yml`'s `api` service maps
    `${API_PORT:-8010}:8000` — only the host-facing side changes; the
    container's own internal port (`Dockerfile` `EXPOSE`/`CMD`/`HEALTHCHECK`)
    stays 8000 and never needs to know about the host conflict.

### Phase 4 — Ingress, TLS, auth
13. Install `cloudflared` on the box, create a named Tunnel, route a
    subdomain (e.g. `llmwiki.<yourdomain>`) to `http://localhost:8010`
    (the host-facing port from Phase 3, not the container's internal 8000).
    No inbound firewall port needed — outbound-only connection to
    Cloudflare's edge.
14. Add a Cloudflare Access policy in front of the tunnel hostname (email
    OTP or GitHub login) so `/ingest`, `/upload`, and the MCP endpoint at
    `/mcp` aren't relying on `INGEST_API_TOKEN` alone against the open
    internet.

### Phase 5 — Operability
15. `restart: unless-stopped` is already set on the `api` service — survives
    a VPS reboot.
16. Wire the `lint` profile (`docker compose --profile ops run --rm lint`)
    to host cron, weekly, per the existing comment in
    [docker-compose.yml:66-68](docker-compose.yml#L66-L68) — design doc
    §4.4's "global lint on a schedule, not every ingest."
17. Log shipping: `docker compose logs` is enough for Phase 0; revisit if/when
    volume grows.
18. **Shipping a new version**: repeat Phase 3's local build/smoke/push
    steps (6-8), then on the box just `docker compose pull && up -d` again
    (step 11) — no rebuild on the VPS, no `git pull`, downtime is only the
    container restart.

### Phase 6 — CI/CD (optional, propose separately)
19. GitHub Actions: on push to `main`, run `pytest` +
    `python scripts/smoke_flow.py --offline`, build the image, `docker
    login` + push to Docker Hub, then `ssh` to the box and
    `docker compose pull && up -d`. This automates exactly the manual
    Phase 3 steps above. Not required for a first deploy — you're pushing
    manually for now — flagging so it's not forgotten.

## 7. If/when moving to Cloudflare Containers later

Sketch only — not part of this proposal's approval ask:
- Refactor `/ingest`/`/upload` to enqueue onto a Cloudflare Queue instead of
  `BackgroundTasks`; a Queue consumer (itself a Container) runs
  `tools.process_source`.
- Add a `wrangler.toml` container definition pointing at the same image
  built by the existing `Dockerfile`.
- `min_instances=0` becomes safe once ingestion no longer depends on the
  request-serving container staying alive after the response.

## 8. Files this touches (if approved)

Deploying does **not** require changing application code, but the
build-locally/push-to-Docker-Hub workflow does need one real
`docker-compose.yml` change, plus documentation:
- **`docker-compose.yml`** — the `api` and `lint` services' `image:` value
  changes from the generic local tag `llmwiki:latest` to a real Docker Hub
  repo path, e.g. `<dockerhub-user>/llmwiki:latest`, so `docker compose
  push`/`pull` resolve against Docker Hub instead of just tagging an image
  that only exists locally. `build:` stays untouched, so `docker compose up
  --build` keeps working exactly as today for local dev — this is a
  one-line value change, not a restructure.
- `docs/deployment-plan-container-hosting.md` (this file)
- A deploy runbook addition to `implement-plan.md` §6 (operational runbook
  already lives there) or a new `docs/runbook-hostinger.md`
- Possibly a `.github/workflows/deploy.yml` if Phase 6 (CI/CD) is approved
- No `.env.example` changes needed — every variable this plan uses already
  exists there; the Docker Hub repo path is a plain (non-secret) value
  hardcoded in `docker-compose.yml`, not something that belongs in `.env`

## 9. Test section (per `CLAUDE.md`'s mandatory rule)

1. **No regressions:** deployment changes don't touch `src/llmwiki`; the
   existing 315 unit tests and `test_layering.py` /
   `test_compiler_no_full_scan.py` / `test_agent.py` /
   `test_providers.py` guardrails are unaffected.
2. **Obsolete tests to remove:** none — nothing existing becomes
   inapplicable.
3. **New tests/checks to add:**
   - A deploy-time smoke check (not pytest): `docker compose --profile test
     run --rm smoke` against the freshly built image, run **locally, right
     after building and before pushing to Docker Hub** (Phase 3 step 7) —
     the gate that used to run on the box after `--build` now has to run
     before the image ever leaves your machine, since the box never builds
     anything anymore. Already exists as a profile; in Phase 6 this becomes
     a CI step instead of a manual one.
   - A post-deploy health check step (`curl /healthz` through the public
     Tunnel hostname) as part of the runbook, to confirm the Tunnel + Access
     policy path works end-to-end, not just `localhost`.
   - If Phase 6 CI/CD is built: a GitHub Actions workflow test run
     (`pytest` + `smoke_flow.py --offline`) gating the build step.
4. **Documentation:** this file is the record until a path is chosen; once
   implemented, log the actual change (goal, files touched, test results)
   in `HISTORY.md` per the mandatory-logging rule, and fold the Hostinger
   runbook into `implement-plan.md` §6 alongside the existing Cloudflare
   bootstrap steps.
