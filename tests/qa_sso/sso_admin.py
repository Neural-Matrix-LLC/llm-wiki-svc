"""Run SSO-side QA operations through the SSO's manage.py (``SSO_MANAGE_CMD``), like sso-server QA does.

Every call execs fixtures/sso_ops.py first, so the R27 test-DB guard runs on every single operation.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE / "fixtures" / "sso_ops.py"


class SSOAdmin:
    def __init__(self, manage_cmd: str, cwd: str | None):
        self.argv = shlex.split(manage_cmd)
        self.cwd = cwd

    def _env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("SSO_ALLOW_SYNTHETIC_SEED", "1")
        env["QA_SSO_TESTS_DIR"] = str(HERE)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        return env

    def shell(self, code: str, timeout: int = 120) -> str:
        script = f"exec(open({str(OPS)!r}).read())\n{code}"
        p = subprocess.run(self.argv + ["shell", "-c", script], cwd=self.cwd, env=self._env(),
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise AssertionError(f"SSO manage shell failed ({p.returncode}): {p.stderr[-3000:]}")
        return p.stdout.strip()

    def manage(self, *args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
        p = subprocess.run(self.argv + list(args), cwd=self.cwd, env=self._env(),
                           capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise AssertionError(f"manage {' '.join(args)} failed: {p.stdout[-1500:]} {p.stderr[-1500:]}")
        return p

    def seed(self) -> str:
        out = self.shell("seed()")
        assert "QA_SSO_SEED_OK" in out, out
        return out

    def call(self, fn: str, *args) -> str:
        return self.shell(f"{fn}(*{list(args)!r})")  # repr: Python literals (True, not JSON true)

    def json(self, fn: str, *args):
        return json.loads(self.call(fn, *args).splitlines()[-1])
