"""scripts/sync_wiki.py: the rclone command lines it builds and the judgement it applies.

The script is a standalone entry point that shells out to ``rclone`` (plan II
§21). Nothing here runs rclone: ``subprocess.run`` is replaced with a recorder,
and what is pinned is (a) the exact ``config create`` / ``sync`` argument
lists - the whole bucket (``raw/``, ``status/``, ``wiki/``) by default,
``wiki/`` alone under ``--wiki-only``, ``_meta/**`` excluded either way,
never a write to the bucket - (b) that the secret never reaches stdout, and
(c) the pass/fail decisions: remote already there vs. created, placeholder
``R2_*`` values refused, the ``local`` backend skipped, a mirror without
``index.md`` failed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from llmwiki.config import Settings

REPO = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sw() -> ModuleType:
    return _load("sync_wiki")


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        llm_provider="fake",
        storage_backend="r2",
        r2_access_key_id="AKIA-test",
        r2_secret_access_key="s3cr3t-value",
        r2_bucket="llmwiki",
        r2_endpoint_url="https://acct.r2.cloudflarestorage.com",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def remote(sw: ModuleType):
    return sw.R2Remote.from_settings(_settings())


STORED_R2 = {"type": "s3", "access_key_id": "AKIA-test",
             "endpoint": "https://acct.r2.cloudflarestorage.com"}


class FakeRclone:
    """Records every command; answers each by its subcommand.

    ``calls`` is summarised by ``.subs`` as ``listremotes`` / ``dump`` /
    ``create`` / ``lsd`` / ``sync`` / ``copy``. ``stored`` is what
    ``rclone config dump`` returns - by default a remote matching ``_settings()``.
    """

    def __init__(self, *, remotes: str = "", lsd: str = "", fail: set[str] = frozenset(),
                 stored: dict | None = None):
        self.calls: list[list[str]] = []
        self.remotes = remotes
        self.lsd = lsd
        self.fail = set(fail)
        self.stored = {"r2": STORED_R2} if stored is None else stored

    @property
    def subs(self) -> list[str]:
        return [c[2] if c[1] == "config" else c[1] for c in self.calls]

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        sub = cmd[2] if cmd[1] == "config" else cmd[1]
        rc = 1 if sub in self.fail else 0
        out = {"listremotes": self.remotes, "lsd": self.lsd,
               "dump": json.dumps(self.stored)}.get(sub, "")
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="boom" if rc else "")


# --- command lines (§21.2, §21.3) ----------------------------------------------


def test_config_create_matches_plan_21_2(sw: ModuleType, remote) -> None:
    cmd = sw.config_create_cmd(remote)
    assert cmd[:5] == ["rclone", "config", "create", "r2", "s3"]
    assert "provider=Cloudflare" in cmd
    assert "access_key_id=AKIA-test" in cmd
    assert "secret_access_key=s3cr3t-value" in cmd
    assert "endpoint=https://acct.r2.cloudflarestorage.com" in cmd
    assert "acl=private" in cmd and "no_check_bucket=true" in cmd
    assert "--non-interactive" in cmd


def test_mirror_is_whole_bucket_r2_to_local(sw: ModuleType, remote) -> None:
    """raw/, status/ and wiki/ land side by side, the way a source note's raw/{id}/ expects."""
    cmd = sw.mirror_cmd(remote, Path("vault"))
    assert cmd[:4] == ["rclone", "sync", "r2:llmwiki", "vault"]
    assert cmd[4:6] == ["--exclude", "wiki/_meta/**"]
    assert "-P" in cmd and "--dry-run" not in cmd
    # §21.4: the local folder is never the source.
    assert cmd.index("r2:llmwiki") < cmd.index("vault")


def test_wiki_only_mirrors_the_wiki_prefix(sw: ModuleType, remote) -> None:
    cmd = sw.mirror_cmd(remote, Path("vault"), wiki_only=True)
    assert cmd[:4] == ["rclone", "sync", "r2:llmwiki/wiki", "vault"]
    assert cmd[4:6] == ["--exclude", "_meta/**"]  # relative to wiki/ now


def test_mirror_flags(sw: ModuleType, remote) -> None:
    cmd = sw.mirror_cmd(remote, Path("v"), copy=True, include_meta=True, dry_run=True, quiet=True)
    assert cmd[1] == "copy"
    assert "--exclude" not in cmd
    assert "--dry-run" in cmd
    assert "--quiet" in cmd and "-P" not in cmd


def test_redact_hides_the_secret(sw: ModuleType, remote) -> None:
    shown = sw.redact(sw.config_create_cmd(remote), remote)
    assert "s3cr3t-value" not in shown
    assert "secret_access_key=***" in shown
    assert "AKIA-test" in shown  # the key id is not a secret


# --- remote setup ---------------------------------------------------------------


def test_existing_remote_is_not_recreated(sw: ModuleType, remote, monkeypatch) -> None:
    fake = FakeRclone(remotes="gdrive:\nr2:\n")
    monkeypatch.setattr(sw.subprocess, "run", fake)
    msg = sw.ensure_remote(remote, force=False)
    assert "already configured" in msg
    assert fake.subs == ["listremotes", "dump"]


def test_existing_remote_for_another_account_is_refused(
    sw: ModuleType, remote, monkeypatch,
) -> None:
    """A dev remote must not serve a prod env file: bucket is in the path, creds in the remote."""
    stored = {"r2": {**STORED_R2, "access_key_id": "AKIA-dev"}}
    fake = FakeRclone(remotes="r2:\n", stored=stored)
    monkeypatch.setattr(sw.subprocess, "run", fake)
    with pytest.raises(sw.StepFailed, match="access_key_id differs") as exc:
        sw.ensure_remote(remote, force=False)
    assert "--remote <other-name>" in str(exc.value)
    assert fake.subs == ["listremotes", "dump"]  # never created, never mirrored

    both = {"r2": {**STORED_R2, "access_key_id": "AKIA-dev", "endpoint": "https://other"}}
    monkeypatch.setattr(sw.subprocess, "run", FakeRclone(remotes="r2:\n", stored=both))
    with pytest.raises(sw.StepFailed, match="access_key_id and endpoint differ"):
        sw.ensure_remote(remote, force=False)


def test_missing_remote_is_created(sw: ModuleType, remote, monkeypatch) -> None:
    fake = FakeRclone(remotes="gdrive:\n")
    monkeypatch.setattr(sw.subprocess, "run", fake)
    msg = sw.ensure_remote(remote, force=False)
    assert "written to rclone.conf" in msg
    assert fake.subs == ["listremotes", "create"]
    assert fake.calls[1] == sw.config_create_cmd(remote)


def test_setup_forces_recreation(sw: ModuleType, remote, monkeypatch) -> None:
    fake = FakeRclone(remotes="r2:\n")
    monkeypatch.setattr(sw.subprocess, "run", fake)
    sw.ensure_remote(remote, force=True)
    assert fake.subs == ["create"]  # no dump: --setup overwrites whatever is there


def test_placeholder_r2_values_refuse_to_create(sw: ModuleType, monkeypatch) -> None:
    remote = sw.R2Remote.from_settings(_settings(
        r2_access_key_id="changeme",
        r2_endpoint_url="https://changeme.r2.cloudflarestorage.com",
    ))
    fake = FakeRclone(remotes="")
    monkeypatch.setattr(sw.subprocess, "run", fake)
    with pytest.raises(sw.StepFailed, match="R2_ACCESS_KEY_ID, R2_ENDPOINT_URL"):
        sw.ensure_remote(remote, force=False)
    assert fake.subs == ["listremotes"]  # config create never ran


def test_create_failure_message_is_redacted(sw: ModuleType, remote, monkeypatch) -> None:
    monkeypatch.setattr(sw.subprocess, "run", FakeRclone(remotes="", fail={"create"}))
    with pytest.raises(sw.StepFailed) as exc:
        sw.ensure_remote(remote, force=False)
    assert "s3cr3t-value" not in str(exc.value)
    assert "boom" in str(exc.value)


# --- bucket check -----------------------------------------------------------------


def _lsd(*folders: str) -> str:
    """What ``rclone lsd`` prints: one ``<size> <date> <time> <count> <name>`` line per folder."""
    return "".join(f"          -1 2026-09-21 10:00:00        -1 {f}\n" for f in folders)


def test_bucket_must_have_wiki_prefix(sw: ModuleType, remote, monkeypatch) -> None:
    monkeypatch.setattr(sw.subprocess, "run", FakeRclone(lsd=_lsd("raw", "wiki")))
    assert sw.check_bucket(remote) == ["raw", "wiki"]

    monkeypatch.setattr(sw.subprocess, "run", FakeRclone(lsd=_lsd("raw")))
    with pytest.raises(sw.StepFailed, match="no 'wiki/' prefix"):
        sw.check_bucket(remote)

    monkeypatch.setattr(sw.subprocess, "run", FakeRclone(fail={"lsd"}))
    with pytest.raises(sw.StepFailed, match="Object Read"):
        sw.check_bucket(remote)


# --- post-mirror check ----------------------------------------------------------


def _fake_wiki(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("# index\n")
    (root / "concepts").mkdir()
    (root / "concepts" / "a.md").write_text("a")
    (root / "concepts" / "b.md").write_text("b")
    (root / "sources").mkdir()
    (root / "sources" / "s.md").write_text("s")


def test_vault_report_counts_pages_and_needs_index(sw: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(sw.StepFailed, match="wiki/index.md"):
        sw.vault_report(tmp_path)
    _fake_wiki(tmp_path / "wiki")
    # a wiki with no raw/ yet is reported, not failed
    assert sw.vault_report(tmp_path) == {"concepts": 2, "entities": 0, "sources": 1,
                                         "raw sources": 0}
    (tmp_path / "raw" / "abc-doc").mkdir(parents=True)
    (tmp_path / "raw" / "abc-doc" / "extracted.md").write_text("x")
    (tmp_path / "raw" / "def-vid").mkdir()
    assert sw.vault_report(tmp_path)["raw sources"] == 2


def test_vault_report_wiki_only_root_is_the_wiki(sw: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(sw.StepFailed, match="index.md"):
        sw.vault_report(tmp_path, wiki_only=True)
    _fake_wiki(tmp_path)
    assert sw.vault_report(tmp_path, wiki_only=True) == {"concepts": 2, "entities": 0,
                                                         "sources": 1}


# --- main -------------------------------------------------------------------------


def test_main_end_to_end_with_fake_rclone(
    sw: ModuleType, monkeypatch, tmp_path: Path, capsys,
) -> None:
    dest = tmp_path / "vault"

    class Mirroring(FakeRclone):
        def __call__(self, cmd, **kw):
            if cmd[1] == "sync":  # pretend rclone wrote the vault
                _fake_wiki(dest / "wiki")
                (dest / "raw" / "abc-doc").mkdir(parents=True)
            return super().__call__(cmd, **kw)

    fake = Mirroring(remotes="", lsd=_lsd("wiki"))
    monkeypatch.setattr(sw.subprocess, "run", fake)
    monkeypatch.setattr(sw.shutil, "which", lambda _: "/usr/bin/rclone")
    monkeypatch.setattr(sw, "load_settings", _settings)

    assert sw.main([str(dest)]) == 0
    assert fake.subs == ["listremotes", "create", "lsd", "sync"]
    assert fake.calls[3][2] == "r2:llmwiki"  # the bucket, not wiki/
    out = capsys.readouterr().out
    assert "s3cr3t-value" not in out
    assert "1 raw sources" in out
    assert "Open" in out and "Obsidian" in out


def test_main_setup_only_stops_before_mirror(sw: ModuleType, monkeypatch) -> None:
    fake = FakeRclone(remotes="r2:\n", lsd=_lsd("wiki"))
    monkeypatch.setattr(sw.subprocess, "run", fake)
    monkeypatch.setattr(sw.shutil, "which", lambda _: "/usr/bin/rclone")
    monkeypatch.setattr(sw, "load_settings", _settings)
    assert sw.main(["--setup-only"]) == 0
    assert fake.subs == ["listremotes", "dump", "lsd"]


def test_main_env_file_selects_another_environment(
    sw: ModuleType, monkeypatch, tmp_path: Path, capsys,
) -> None:
    """--env-file .env.prod: its R2_* values drive the remote, the bucket and the mirror."""
    env = tmp_path / ".env.prod"
    env.write_text(
        "STORAGE_BACKEND=r2\nLLM_PROVIDER=fake\n"
        "R2_ACCESS_KEY_ID=AKIA-prod\nR2_SECRET_ACCESS_KEY=prod-secret\n"
        "R2_BUCKET=llmwiki-prod\nR2_ENDPOINT_URL=https://prod.r2.cloudflarestorage.com\n"
    )
    for var in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT_URL",
                "STORAGE_BACKEND"):
        monkeypatch.delenv(var, raising=False)  # the shell must not shadow the file
    fake = FakeRclone(remotes="r2:\n", lsd=_lsd("wiki"))  # only the dev remote exists
    monkeypatch.setattr(sw.subprocess, "run", fake)
    monkeypatch.setattr(sw.shutil, "which", lambda _: "/usr/bin/rclone")

    assert sw.main(["--env-file", str(env), "--remote", "r2-prod", "--setup-only"]) == 0
    assert fake.subs == ["listremotes", "create", "lsd"]
    create, lsd = fake.calls[1], fake.calls[2]
    assert create[3] == "r2-prod"
    assert "access_key_id=AKIA-prod" in create
    assert "endpoint=https://prod.r2.cloudflarestorage.com" in create
    assert lsd == ["rclone", "lsd", "r2-prod:llmwiki-prod"]
    assert "prod-secret" not in capsys.readouterr().out

    # Same env file against the *dev* remote name: refused, nothing created or listed.
    fake = FakeRclone(remotes="r2:\n", lsd=_lsd("wiki"))
    monkeypatch.setattr(sw.subprocess, "run", fake)
    assert sw.main(["--env-file", str(env), "--setup-only"]) == 1
    assert fake.subs == ["listremotes", "dump"]
    assert "differ from the env file" in capsys.readouterr().out


def test_main_missing_env_file_fails(sw: ModuleType, monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(sw.shutil, "which", lambda _: "/usr/bin/rclone")
    assert sw.main(["--env-file", str(tmp_path / "nope.env")]) == 1
    assert "no such file" in capsys.readouterr().out


def test_main_local_backend_has_nothing_to_mirror(sw: ModuleType, monkeypatch, capsys) -> None:
    fake = FakeRclone()
    monkeypatch.setattr(sw.subprocess, "run", fake)
    monkeypatch.setattr(sw.shutil, "which", lambda _: "/usr/bin/rclone")
    monkeypatch.setattr(sw, "load_settings", lambda: _settings(storage_backend="local"))
    assert sw.main([]) == 0
    assert fake.calls == []
    assert "[SKIP]" in capsys.readouterr().out


def test_main_without_rclone_fails(sw: ModuleType, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sw.shutil, "which", lambda _: None)
    assert sw.main([]) == 1
    assert "rclone is not on PATH" in capsys.readouterr().out
