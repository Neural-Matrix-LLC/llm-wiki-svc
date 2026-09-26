#!/usr/bin/env python3
"""Mirror the R2 bucket - ``raw/``, ``status/``, ``wiki/`` - to a local vault for Obsidian.

The knowledge base lives in Cloudflare R2 (``STORAGE_BACKEND=r2``) and Obsidian
only opens a local folder. This script closes that gap (plan II §21) in one
command. The whole bucket is mirrored, not just ``wiki/``: a source note says
``raw/{id}/`` and Karpathy's layout keeps ``raw/`` beside ``wiki/`` in the one
vault, so ``raw/{id}/extracted.md`` opens next to the page that cites it.
``--wiki-only`` is the light alternative when ``raw/`` (PDFs) is too big to
carry around.

  1. Remote   - make sure an rclone remote for R2 exists (``rclone listremotes``).
                If it is missing, or ``--setup`` is given, create it with
                ``rclone config create <name> s3 provider=Cloudflare ...`` from
                the same ``R2_*`` values ``.env`` already holds. The secret goes
                to rclone on its command line, never to stdout. An existing
                remote must carry the same access key id and endpoint as the
                env file (``rclone config dump``) - otherwise the mirror would
                read the wrong account with the right bucket name.
  2. Bucket   - ``rclone lsd <remote>:<bucket>`` answers; the top-level folders
                should include ``wiki``.
  3. Mirror   - ``rclone sync <remote>:<bucket> <dest> --exclude "wiki/_meta/**"``
                (``--wiki-only`` for ``<remote>:<bucket>/wiki`` alone;
                ``--copy`` for ``rclone copy``, which never deletes - for a
                vault that also holds your own notes; ``--include-meta`` to keep
                ``wiki/_meta/gists.json`` and ``wiki/_meta/cost.jsonl``).
  4. Check    - ``<dest>/wiki/index.md`` exists; the page count per wiki folder
                and the number of ``raw/`` sources are printed, so an empty or
                half-finished mirror is visible.

    scripts/sync_wiki.py                       # remote if needed, then mirror the bucket to ./vault
    scripts/sync_wiki.py ~/vault               # another destination
    scripts/sync_wiki.py --wiki-only           # wiki/ alone (no raw/ PDFs, no status/)
    scripts/sync_wiki.py --setup               # (re)create the remote from .env, then mirror
    scripts/sync_wiki.py --copy                # never delete local files
    scripts/sync_wiki.py --dry-run             # show what rclone would move
    scripts/sync_wiki.py --setup-only          # step 1-2 only, no mirror
    scripts/sync_wiki.py --env-file .env.prod --remote r2-prod ~/vault-prod

The mirror is one-directional, R2 -> local (§21.4): capture owns ``raw/``,
the pipeline owns ``status/``, the compiler and the scheduled lint own
``wiki/``. Nothing here ever writes to the bucket.

Needs ``rclone`` on PATH and ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` /
``R2_BUCKET`` / ``R2_ENDPOINT_URL`` in ``.env`` (a read-only R2 token is enough,
and the better choice on a desktop). ``--env-file`` reads another file, e.g.
``.env.prod``; give each environment its own ``--remote`` name when the
credentials differ. Variables already exported in the shell win over any env
file, as everywhere in ``llmwiki``. With ``STORAGE_BACKEND=local`` there is
nothing to mirror - ``LOCAL_STORAGE_PATH/wiki`` already opens as a vault - and
the script says so.

Exit 1 at the first failed step, and the step is named.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from llmwiki.config import Settings, load_settings
from llmwiki.storage.layout import RAW_PREFIX, WIKI_PREFIX

DEFAULT_REMOTE = "r2"
DEFAULT_DEST = Path("./vault")
# `gists.json` / `cost.jsonl` (storage/layout.py GISTS_KEY, COST_KEY): not pages, and
# cost.jsonl grows on every compile. The pattern is relative to the mirror's source.
META_EXCLUDE = f"{WIKI_PREFIX}_meta/**"
META_EXCLUDE_WIKI_ONLY = "_meta/**"
# The folders a compiled wiki is made of (plan I §5.2); the check counts pages per folder.
WIKI_FOLDERS = ("concepts", "entities", "sources")


class StepFailed(RuntimeError):
    """One named step did not hold."""


@dataclass(frozen=True)
class R2Remote:
    """What rclone needs to talk to the bucket, pulled from ``Settings``."""

    name: str
    bucket: str
    endpoint: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_settings(cls, settings: Settings, name: str = DEFAULT_REMOTE) -> R2Remote:
        return cls(
            name=name,
            bucket=settings.r2_bucket,
            endpoint=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        )

    @property
    def bucket_path(self) -> str:
        return f"{self.name}:{self.bucket}"

    @property
    def wiki_path(self) -> str:
        return f"{self.bucket_path}/{WIKI_PREFIX.rstrip('/')}"

    def missing_values(self) -> list[str]:
        """The ``R2_*`` variables that are unset or still ``.env.example``'s placeholder."""
        pairs = {
            "R2_ACCESS_KEY_ID": self.access_key_id,
            "R2_SECRET_ACCESS_KEY": self.secret_access_key,
            "R2_BUCKET": self.bucket,
            "R2_ENDPOINT_URL": self.endpoint,
        }
        return [k for k, v in pairs.items() if not v or v == "changeme" or "changeme" in v]


# --- rclone command lines ------------------------------------------------------
# Built as lists and kept apart from running them, so the test suite can pin
# the exact arguments without an rclone binary.


def config_create_cmd(remote: R2Remote) -> list[str]:
    """``rclone config create`` exactly as plan II §21.2 spells it."""
    return [
        "rclone", "config", "create", remote.name, "s3",
        "provider=Cloudflare",
        f"access_key_id={remote.access_key_id}",
        f"secret_access_key={remote.secret_access_key}",
        f"endpoint={remote.endpoint}",
        "acl=private",
        "no_check_bucket=true",
        "--non-interactive",
    ]


def lsd_cmd(remote: R2Remote) -> list[str]:
    return ["rclone", "lsd", f"{remote.name}:{remote.bucket}"]


def mirror_cmd(
    remote: R2Remote,
    dest: Path,
    *,
    wiki_only: bool = False,
    copy: bool = False,
    include_meta: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> list[str]:
    """``rclone sync`` (or ``copy``) of the bucket - or of ``wiki/`` alone - R2 -> local."""
    src = remote.wiki_path if wiki_only else remote.bucket_path
    cmd = ["rclone", "copy" if copy else "sync", src, str(dest)]
    if not include_meta:
        cmd += ["--exclude", META_EXCLUDE_WIKI_ONLY if wiki_only else META_EXCLUDE]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append("--quiet" if quiet else "-P")
    return cmd


def redact(cmd: list[str], remote: R2Remote) -> str:
    """The command line for printing, with the secret replaced."""
    shown = [a.replace(remote.secret_access_key, "***") if remote.secret_access_key else a
             for a in cmd]
    return " ".join(shown)


# --- steps ---------------------------------------------------------------------


def _run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=capture, check=False)


def remote_exists(name: str) -> bool:
    """``rclone listremotes`` prints one ``name:`` per line."""
    proc = _run(["rclone", "listremotes"])
    if proc.returncode != 0:
        raise StepFailed(f"rclone listremotes failed: {proc.stderr.strip()}")
    return f"{name}:" in {line.strip() for line in proc.stdout.splitlines()}


def remote_config(name: str) -> dict[str, str]:
    """The stored options of one remote, from ``rclone config dump`` (JSON, all remotes)."""
    proc = _run(["rclone", "config", "dump"])
    if proc.returncode != 0:
        raise StepFailed(f"rclone config dump failed: {proc.stderr.strip()}")
    return dict(json.loads(proc.stdout or "{}").get(name, {}))


def remote_mismatch(remote: R2Remote, stored: dict[str, str]) -> list[str]:
    """Which of access key id / endpoint the stored remote disagrees with the env on."""
    expected = {"access_key_id": remote.access_key_id, "endpoint": remote.endpoint}
    return [k for k, v in expected.items() if stored.get(k, "") != v]


def ensure_remote(remote: R2Remote, *, force: bool) -> str:
    """Create the rclone remote if missing (or ``force``); return what was done.

    An existing remote is only accepted when it points at the same account as
    the env file: the bucket name is in the path, but the credentials are in
    the remote, and a dev remote with a prod bucket name is the wrong data.
    """
    if not force and remote_exists(remote.name):
        wrong = remote_mismatch(remote, remote_config(remote.name))
        if wrong:
            raise StepFailed(
                f"remote '{remote.name}:' exists but its {' and '.join(wrong)} "
                f"{'differ' if len(wrong) > 1 else 'differs'} from the env file's R2_* values\n"
                f"         -> another environment? use --remote <other-name>; "
                f"or --setup to overwrite '{remote.name}:'"
            )
        return f"remote '{remote.name}:' already configured (use --setup to recreate it)"
    missing = remote.missing_values()
    if missing:
        raise StepFailed(f"cannot create the remote: {', '.join(missing)} not set in .env")
    cmd = config_create_cmd(remote)
    proc = _run(cmd)
    if proc.returncode != 0:
        raise StepFailed(f"{redact(cmd, remote)}\n         {proc.stderr.strip()}")
    return f"remote '{remote.name}:' written to rclone.conf (endpoint {remote.endpoint})"


def check_bucket(remote: R2Remote) -> list[str]:
    """Top-level folders of the bucket; ``wiki`` must be one of them."""
    proc = _run(lsd_cmd(remote))
    if proc.returncode != 0:
        raise StepFailed(
            f"rclone lsd {remote.name}:{remote.bucket} failed: {proc.stderr.strip()}\n"
            "         -> wrong R2_ENDPOINT_URL / bucket name, or the token lacks Object Read"
        )
    # `rclone lsd` lines look like `          -1 2026-09-21 10:00:00        -1 wiki`
    folders = [line.split()[-1] for line in proc.stdout.splitlines() if line.strip()]
    if WIKI_PREFIX.rstrip("/") not in folders:
        raise StepFailed(
            f"bucket '{remote.bucket}' has no '{WIKI_PREFIX}' prefix "
            f"(found: {folders or 'nothing'})\n"
            "         -> nothing compiled yet? ingest a source first"
        )
    return folders


def mirror(cmd: list[str]) -> None:
    # Not captured: rclone's -P progress is the point of running it interactively.
    proc = _run(cmd, capture=False)
    if proc.returncode != 0:
        raise StepFailed(f"rclone exited {proc.returncode}: {' '.join(cmd)}")


def vault_report(dest: Path, *, wiki_only: bool = False) -> dict[str, int]:
    """Page counts per wiki folder (+ ``raw/`` sources); raises if ``index.md`` is not there.

    With ``wiki_only`` the vault root *is* ``wiki/``; otherwise it is the bucket root.
    """
    wiki = dest if wiki_only else dest / WIKI_PREFIX
    if not (wiki / "index.md").is_file():
        raise StepFailed(f"{wiki / 'index.md'} is missing after the mirror")
    counts = {folder: len(list((wiki / folder).glob("*.md"))) for folder in WIKI_FOLDERS}
    if not wiki_only:
        raw = dest / RAW_PREFIX
        counts["raw sources"] = sum(1 for d in raw.iterdir() if d.is_dir()) if raw.is_dir() else 0
    return counts


# --- main ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", nargs="?", type=Path, default=DEFAULT_DEST,
                    help=f"local vault folder (default {DEFAULT_DEST})")
    ap.add_argument("--remote", default=DEFAULT_REMOTE,
                    help=f"rclone remote name (default {DEFAULT_REMOTE!r})")
    ap.add_argument("--env-file", type=Path, default=None,
                    help="read R2_* from this file instead of .env (e.g. .env.prod)")
    ap.add_argument("--setup", action="store_true",
                    help="(re)create the rclone remote from .env even if it exists")
    ap.add_argument("--setup-only", action="store_true",
                    help="configure and check the remote, do not mirror")
    ap.add_argument("--wiki-only", action="store_true",
                    help="mirror only wiki/ (DEST is then the wiki root; no raw/, no status/)")
    ap.add_argument("--copy", action="store_true",
                    help="rclone copy instead of sync: never delete local files")
    ap.add_argument("--include-meta", action="store_true",
                    help="also mirror wiki/_meta/ (gists.json, cost.jsonl)")
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to rclone")
    ap.add_argument("--quiet", action="store_true",
                    help="no rclone progress (for cron)")
    args = ap.parse_args(argv)

    if shutil.which("rclone") is None:
        print("  [FAIL] rclone is not on PATH -> https://rclone.org/install/")
        return 1

    if args.env_file is not None and not args.env_file.is_file():
        print(f"  [FAIL] --env-file {args.env_file}: no such file")
        return 1
    if args.env_file:
        # pydantic-settings' init-only ``_env_file`` is not in the generated signature.
        settings = Settings(_env_file=args.env_file)  # type: ignore[call-arg]
    else:
        settings = load_settings()
    if settings.storage_backend != "r2":
        local_wiki = settings.local_storage_path / WIKI_PREFIX
        print(f"  [SKIP] STORAGE_BACKEND={settings.storage_backend}: nothing to mirror - "
              f"open {local_wiki} in Obsidian directly (plan I §6.7)")
        return 0
    remote = R2Remote.from_settings(settings, args.remote)

    try:
        print("1. Remote")
        print(f"  [OK]   {ensure_remote(remote, force=args.setup)}")

        print("2. Bucket")
        folders = check_bucket(remote)
        print(f"  [OK]   {remote.name}:{remote.bucket} -> {' '.join(folders)}")
        if args.setup_only:
            return 0

        print("3. Mirror")
        cmd = mirror_cmd(remote, args.dest, wiki_only=args.wiki_only, copy=args.copy,
                         include_meta=args.include_meta, dry_run=args.dry_run, quiet=args.quiet)
        print(f"         $ {redact(cmd, remote)}")
        mirror(cmd)
        if args.dry_run:
            print("  [OK]   dry run, nothing written")
            return 0
        print(f"  [OK]   {cmd[2]} -> {args.dest}")

        print("4. Check")
        counts = vault_report(args.dest, wiki_only=args.wiki_only)
        summary = ", ".join(f"{n} {folder}" for folder, n in counts.items())
        index = args.dest / "index.md" if args.wiki_only else args.dest / WIKI_PREFIX / "index.md"
        print(f"  [OK]   {index} present; {summary}")
        print(f"\nOpen {args.dest.resolve()} in Obsidian: File -> Open folder as vault.")
    except StepFailed as exc:
        print(f"  [FAIL] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
