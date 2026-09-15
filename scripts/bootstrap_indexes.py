#!/usr/bin/env python3
"""Create or verify the Vectorize indexes and their metadata indexes.

    scripts/bootstrap_indexes.py --check     # verify, change nothing (safe, run first)
    scripts/bootstrap_indexes.py --create    # create what is missing

Metadata indexes must exist *before* any insert that will be filtered on them,
which is why this runs at setup rather than lazily.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from llmwiki.config import load_settings

FILTERABLE = ["source_id", "slug", "type"]


def api(settings) -> httpx.Client:
    settings.require("cf_account_id", "cf_api_token")
    return httpx.Client(
        base_url=(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.cf_account_id}/vectorize/v2"
        ),
        headers={"Authorization": f"Bearer {settings.cf_api_token.get_secret_value()}"},
        timeout=60.0,
    )


def describe(client: httpx.Client, name: str) -> dict | None:
    response = client.get(f"/indexes/{name}")
    if response.status_code in (404, 410):  # 410: deleted and still being torn down
        return None
    response.raise_for_status()
    return response.json().get("result")


def create_index(client: httpx.Client, name: str, dimensions: int) -> None:
    response = client.post(
        "/indexes",
        json={"name": name, "config": {"dimensions": dimensions, "metric": "cosine"}},
    )
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="verify only; change nothing")
    group.add_argument("--create", action="store_true", help="create anything missing")
    args = parser.parse_args()

    settings = load_settings()
    if settings.vector_backend != "vectorize":
        print(f"VECTOR_BACKEND is {settings.vector_backend!r}; nothing to bootstrap.")
        return 0

    indexes = [settings.vectorize_chunks_index, settings.vectorize_gists_index]
    problems = []
    with api(settings) as client:
        for name in indexes:
            print(f"{name}:")
            existing = describe(client, name)
            if existing is None:
                if args.check:
                    problems.append(f"{name} does not exist")
                    print("  MISSING")
                    continue
                create_index(client, name, settings.embedding_dim)
                existing = describe(client, name) or {}

            dimensions = (existing.get("config") or {}).get("dimensions")
            if dimensions and dimensions != settings.embedding_dim:
                problems.append(
                    f"{name} has {dimensions} dimensions but EMBEDDING_DIM is "
                    f"{settings.embedding_dim}; the index must be recreated"
                )
                print(f"  DIMENSION MISMATCH: {dimensions} != {settings.embedding_dim}")
            else:
                print(f"  ok (dimensions={dimensions})")

            if args.create:
                for prop in FILTERABLE:
                    create_metadata_index(client, name, prop)

    if problems:
        print("\nProblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nAll indexes present and correctly dimensioned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
