#!/usr/bin/env python3
"""Wipe the Vectorize indexes and recreate them empty, ready for a fresh corpus.

    scripts/reset_vectorize.py                 # show what would be deleted; change nothing
    scripts/reset_vectorize.py --yes           # delete both indexes and recreate them
    scripts/reset_vectorize.py --yes --index chunks   # just one of them

Vectorize has no "delete every vector" call and no listing, so the only clean
reset is to delete each index and create it again - at `EMBEDDING_DIM` from
.env, with the same metadata indexes `bootstrap_indexes.py` sets up. Every
vector in the index is gone the moment the delete returns; there is no undo.

Only Vectorize is touched. `raw/` and `wiki/` in R2 (or `.data/`) are left
alone: they are the source of truth the vectors are derived from. To fill the
new indexes again, re-run `process_source` for each source (chunks) and
`backfill.py --force` (gists), or ingest afresh.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from llmwiki.config import Settings, load_settings

# Keep in sync with scripts/bootstrap_indexes.py's FILTERABLE.
FILTERABLE = ["source_id", "slug", "type"]
# Index deletion and name release are asynchronous on Cloudflare's side; a
# create straight after a delete can come back "already exists" for a while.
SETTLE_TIMEOUT_S = 120
SETTLE_INTERVAL_S = 3


def api(settings: Settings) -> httpx.Client:
    settings.require("cf_account_id", "cf_api_token")
    return httpx.Client(
        base_url=(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.cf_account_id}/vectorize/v2"
        ),
        headers={"Authorization": f"Bearer {settings.cf_api_token.get_secret_value()}"},
        timeout=60.0,
    )


def describe(client: httpx.Client, name: str) -> dict | None:
    """The index's config, or None when it does not exist.

    Cloudflare answers 404 for a name that never existed and 410 Gone for one
    that has been deleted (during and after teardown); both are "absent" here.
    """
    response = client.get(f"/indexes/{name}")
    if response.status_code in (404, 410):
        return None
    response.raise_for_status()
    return response.json().get("result")


def vector_count(client: httpx.Client, name: str) -> int | None:
    """Best-effort count from the index info endpoint; None when Cloudflare has none yet."""
    response = client.get(f"/indexes/{name}/info")
    if response.status_code >= 400:
        return None
    result = response.json().get("result") or {}
    count = result.get("vectorCount")
    return int(count) if count is not None else None


def delete_index(client: httpx.Client, name: str) -> None:
    response = client.delete(f"/indexes/{name}")
    if response.status_code not in (404, 410):  # already absent: nothing to wait for
        response.raise_for_status()
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while describe(client, name) is not None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{name} still exists {SETTLE_TIMEOUT_S}s after delete")
        time.sleep(SETTLE_INTERVAL_S)
    print(f"  deleted index {name}")


def create_index(client: httpx.Client, name: str, dimensions: int) -> None:
    body = {"name": name, "config": {"dimensions": dimensions, "metric": "cosine"}}
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        response = client.post("/indexes", json=body)
        if response.status_code < 400:
            break
        # The old name is still being released; try again until it is.
        if "already exists" in response.text and time.monotonic() < deadline:
            time.sleep(SETTLE_INTERVAL_S)
            continue
        response.raise_for_status()
    print(f"  created index {name} (dimensions={dimensions}, metric=cosine)")


def create_metadata_index(client: httpx.Client, name: str, prop: str) -> None:
    response = client.post(
        f"/indexes/{name}/metadata_index/create",
        json={"propertyName": prop, "indexType": "string"},
    )
    if response.status_code >= 400 and "already exists" in response.text:
        return
    response.raise_for_status()
    print(f"  created metadata index {name}.{prop}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--yes", action="store_true",
        help="actually delete and recreate; without it the script only reports",
    )
    parser.add_argument(
        "--index", choices=["all", "chunks", "gists"], default="all",
        help="which index to reset (default: all)",
    )
    args = parser.parse_args()

    settings = load_settings()
    if settings.vector_backend != "vectorize":
        print(f"VECTOR_BACKEND is {settings.vector_backend!r}; nothing to reset on Cloudflare.")
        return 0

    names = {
        "chunks": settings.vectorize_chunks_index,
        "gists": settings.vectorize_gists_index,
    }
    targets = list(names.values()) if args.index == "all" else [names[args.index]]

    with api(settings) as client:
        print("Vectorize indexes to reset:")
        for name in targets:
            existing = describe(client, name)
            if existing is None:
                print(f"  {name}: MISSING (will be created)")
                continue
            dimensions = (existing.get("config") or {}).get("dimensions")
            count = vector_count(client, name)
            shown = "unknown" if count is None else str(count)
            print(f"  {name}: dimensions={dimensions}, vectors={shown} (will be DELETED)")

        if not args.yes:
            print("\nDry run - nothing changed. Re-run with --yes to delete and recreate.")
            return 0

        print()
        for name in targets:
            print(f"{name}:")
            delete_index(client, name)
            create_index(client, name, settings.embedding_dim)
            for prop in FILTERABLE:
                create_metadata_index(client, name, prop)

        print("\nVerifying:")
        problems = []
        for name in targets:
            existing = describe(client, name)
            if existing is None:
                problems.append(f"{name} does not exist after recreate")
                print(f"  {name}: MISSING")
                continue
            dimensions = (existing.get("config") or {}).get("dimensions")
            if dimensions != settings.embedding_dim:
                problems.append(f"{name} has {dimensions} dimensions, wanted "
                                f"{settings.embedding_dim}")
            print(f"  {name}: ok (dimensions={dimensions}, empty)")

    if problems:
        print("\nProblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nReset complete. The indexes are empty; raw/ and wiki/ were not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
