#!/usr/bin/env python3
"""Diagnose a real Cloudflare setup before flipping the backends on.

See docs/cloudflare-vectorize-setup-plan.md for the full runbook this supports.
Six independent checks, each reported pass/fail with a remediation hint:

  1. Config      - required .env values are present (not "changeme")
  2. R2           - bucket put/get/delete round trip (Object Read & Write scope)
  3. CF API token - token is valid and active (assumes an Account API Token, not a
                    User API Token - see docs/cloudflare-vectorize-setup-plan.md #2)
  4. Workers AI   - one embedding call; its dimension must equal EMBEDDING_DIM
  5. Vectorize    - both indexes exist, dimensions match, metadata indexes present
  6. Vectorize    - live upsert/query/delete round trip - the only check that
                    actually proves the token's *Write* permission works, since
                    checks 3 and 5 only need read-level access to pass

    scripts/check_cloudflare_setup.py            # full check, including the live write test
    scripts/check_cloudflare_setup.py --quick    # read-only: skip step 6

Costs a small amount of Workers AI usage (one or two embedding calls); the
Vectorize write test cleans up the single vector it creates.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

from llmwiki.config import Settings, load_settings

# Keep in sync with scripts/bootstrap_indexes.py's FILTERABLE.
FILTERABLE = ["source_id", "slug", "type"]
POLL_TIMEOUT_S = 30
POLL_INTERVAL_S = 2


class Report:
    """Collects pass/fail/skip lines and remembers whether anything failed."""

    def __init__(self) -> None:
        self.failed = False

    def ok(self, message: str) -> None:
        print(f"  [OK]   {message}")

    def fail(self, message: str, hint: str = "") -> None:
        self.failed = True
        print(f"  [FAIL] {message}")
        if hint:
            print(f"         -> {hint}")

    def skip(self, message: str) -> None:
        print(f"  [SKIP] {message}")


def check_config(settings: Settings, report: Report) -> bool:
    print("1. Config")
    try:
        settings.require(
            "cf_account_id", "cf_api_token",
            "r2_access_key_id", "r2_secret_access_key", "r2_endpoint_url",
        )
    except RuntimeError as exc:
        report.fail(
            str(exc),
            "copy .env.example to .env and fill these in (implement-plan.md Part I §6.2-6.4)",
        )
        return False
    report.ok("all required Cloudflare/.env values are present")
    return True


def check_r2(settings: Settings, report: Report) -> None:
    print("2. R2 (object storage)")
    from llmwiki.storage.r2 import R2ObjectStore

    store = R2ObjectStore(
        endpoint_url=settings.r2_endpoint_url,
        bucket=settings.r2_bucket,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key.get_secret_value(),
    )
    key = f"_healthcheck/{uuid.uuid4().hex}.txt"
    try:
        store.put(key, b"cloudflare setup check", "text/plain")
        body = store.get(key)
        if body != b"cloudflare setup check":
            report.fail("R2 read-after-write returned different bytes")
        else:
            report.ok(f"put/get round trip on bucket {settings.r2_bucket!r}")
        store.delete(key)
        if store.exists(key):
            report.fail("R2 delete did not remove the object")
        else:
            report.ok("delete confirmed")
    except Exception as exc:  # noqa: BLE001 - report every failure mode, not just ClientError
        report.fail(
            f"R2 round trip raised {exc.__class__.__name__}: {exc}",
            "check R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY and the token's bucket scope (6.2)",
        )


def check_api_token(settings: Settings, report: Report) -> None:
    print("3. Cloudflare API token")
    # Account-scoped endpoint, not /user/tokens/verify - CF_API_TOKEN is expected to
    # be an Account API Token (docs/cloudflare-vectorize-setup-plan.md #2), which
    # verifies differently than a User API Token.
    with httpx.Client(
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{settings.cf_account_id}",
        headers={"Authorization": f"Bearer {settings.cf_api_token.get_secret_value()}"},
        timeout=30.0,
    ) as client:
        try:
            response = client.get("/tokens/verify")
        except httpx.HTTPError as exc:
            report.fail(f"could not reach Cloudflare API: {exc}")
            return
        if response.status_code == 401:
            report.fail("token rejected (401)", "CF_API_TOKEN is wrong or was revoked")
            return
        response.raise_for_status()
        result = response.json().get("result") or {}
        status = result.get("status")
        if status == "active":
            report.ok(f"token is active (id ...{result.get('id', '')[-6:]})")
        else:
            report.fail(f"token status is {status!r}, expected 'active'")


def embed(settings: Settings, texts: list[str]) -> list[list[float]]:
    from llmwiki.embedding.workers_ai import WorkersAIEmbedder

    embedder = WorkersAIEmbedder(
        account_id=settings.cf_account_id,
        api_token=settings.cf_api_token.get_secret_value(),
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
    return embedder.embed(texts)


def check_workers_ai(settings: Settings, report: Report) -> list[float] | None:
    print("4. Workers AI (embeddings)")
    try:
        vectors = embed(settings, ["cloudflare setup check"])
    except Exception as exc:  # noqa: BLE001
        report.fail(
            f"embedding call raised {exc.__class__.__name__}: {exc}",
            "token needs both Account -> Workers AI -> Read and -> Edit (6.3); "
            "or EMBEDDING_MODEL is wrong",
        )
        return None
    dim = len(vectors[0])
    if dim != settings.embedding_dim:
        report.fail(
            f"model returned {dim}-dim vectors but EMBEDDING_DIM is {settings.embedding_dim}",
            "fix EMBEDDING_DIM, or recreate the Vectorize indexes with the printed dimension (6.3)",
        )
        return None
    report.ok(f"{settings.embedding_model} returns {dim}-dim vectors, matches EMBEDDING_DIM")
    return vectors[0]


def check_vectorize_structure(settings: Settings, report: Report) -> None:
    print("5. Vectorize (index structure)")
    with httpx.Client(
        base_url=(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.cf_account_id}/vectorize/v2"
        ),
        headers={"Authorization": f"Bearer {settings.cf_api_token.get_secret_value()}"},
        timeout=30.0,
    ) as client:
        for name in (settings.vectorize_chunks_index, settings.vectorize_gists_index):
            response = client.get(f"/indexes/{name}")
            if response.status_code == 404:
                report.fail(
                    f"index {name!r} does not exist",
                    "run: python scripts/bootstrap_indexes.py --create",
                )
                continue
            response.raise_for_status()
            info = response.json().get("result") or {}
            config = info.get("config") or {}
            dims = config.get("dimensions")
            if dims != settings.embedding_dim:
                report.fail(
                    f"{name} has dimensions={dims}, expected {settings.embedding_dim}",
                    "the index must be deleted and recreated at the right dimension",
                )
            else:
                report.ok(f"{name} exists (dimensions={dims}, metric={config.get('metric')})")

            # Not verified against current Cloudflare docs (implement-plan.md Part I §6's
            # accuracy note applies here too) - if the path is wrong this degrades
            # to a SKIP rather than a false FAIL, since only a 200 is trusted.
            metadata_response = client.get(f"/indexes/{name}/metadata_index/list")
            if metadata_response.status_code == 200:
                indexed = {
                    m.get("propertyName")
                    for m in (metadata_response.json().get("result") or {}).get(
                        "metadataIndexes", []
                    )
                }
                missing = [p for p in FILTERABLE if p not in indexed]
                if missing:
                    report.fail(
                        f"{name} is missing metadata indexes on {missing}",
                        "run: python scripts/bootstrap_indexes.py --create",
                    )
                else:
                    report.ok(f"{name} has metadata indexes on {sorted(indexed)}")
            else:
                report.skip(
                    f"{name}: could not list metadata indexes "
                    f"(HTTP {metadata_response.status_code}) - checked structure only"
                )


def check_vectorize_write(
    settings: Settings, report: Report, probe_vector: list[float] | None
) -> None:
    print("6. Vectorize (live write/query/delete - proves Write permission)")
    if probe_vector is None:
        report.skip("no probe vector (Workers AI check failed above)")
        return
    from llmwiki.vector.vectorize import VectorizeStore

    store = VectorizeStore(
        settings.cf_account_id,
        settings.cf_api_token.get_secret_value(),
        probe_dim=settings.embedding_dim,
    )
    source_id = f"_healthcheck-{uuid.uuid4().hex[:12]}"
    vector_id = f"{source_id}:0"
    index = settings.vectorize_chunks_index
    try:
        store.upsert(index, [vector_id], [probe_vector], [{"source_id": source_id}])
    except Exception as exc:  # noqa: BLE001
        report.fail(
            f"upsert raised {exc.__class__.__name__}: {exc}",
            "token needs Account -> Vectorize -> Write (6.3)",
        )
        store.close()
        return
    report.ok(f"upsert into {index} accepted")

    deadline = time.monotonic() + POLL_TIMEOUT_S
    hits: list = []
    while time.monotonic() < deadline and not hits:
        hits = store.query(index, probe_vector, k=5, where={"source_id": source_id})
        if not hits:
            time.sleep(POLL_INTERVAL_S)
    if hits:
        report.ok(f"upserted vector became visible within {POLL_TIMEOUT_S}s (eventual consistency)")
    else:
        report.fail(f"upserted vector never became queryable within {POLL_TIMEOUT_S}s")

    deleted = store.delete_by_ids(index, [vector_id])
    if deleted == 1:
        report.ok("delete_by_ids cleaned up the probe vector")
    else:
        report.fail(
            "delete_by_ids did not report the probe vector as deleted",
            f"check for a leftover vector: {vector_id}",
        )
    store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--quick", action="store_true",
        help="skip the live Vectorize write/query/delete round trip (step 6)",
    )
    args = parser.parse_args()

    settings = load_settings()
    report = Report()

    if not check_config(settings, report):
        print("\nFix the above before continuing; no network calls were made.")
        return 1

    check_r2(settings, report)
    check_api_token(settings, report)
    probe_vector = check_workers_ai(settings, report)
    check_vectorize_structure(settings, report)
    if args.quick:
        print("6. Vectorize (live write/query/delete - proves Write permission)")
        report.skip("--quick: not run")
    else:
        check_vectorize_write(settings, report, probe_vector)

    print()
    if report.failed:
        print("Some checks FAILED - see the -> hints above.")
        return 1
    print(
        "All checks passed. Safe to set STORAGE_BACKEND=r2, VECTOR_BACKEND=vectorize, "
        "EMBEDDING_BACKEND=workers_ai in .env."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
