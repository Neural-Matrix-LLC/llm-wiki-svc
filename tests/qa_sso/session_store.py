"""Grey-box access to an llm-wiki QA instance's server-side session store (design v1.1 item 2).

v1.1: SQLite at SESSION_DB_PATH, one row per session with: id hash (SHA-256 of the cookie's random id),
username, sub, access, refresh, access_exp, csrf, created_at, last_seen. Table and exact column names are
not fixed by the design, so they're discovered: the table that has username + refresh + created_at.

Writes are only allowed on DBs under tests/qa_sso/run/state/ or the temp dir (QA instances), and only to age a
session (created_at / last_seen) for the absolute-lifetime and prune cases.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


class SessionStore:
    def __init__(self, path: str):
        self.path = Path(path)
        assert self.path.exists(), f"session DB {path} does not exist (SESSION_DB_PATH)"

    # ---------------------------------------------------------------- discovery
    def _conn(self, write=False):
        if write:
            p = self.path.resolve()
            run = (HERE / "run").resolve()
            ok = (any(q.parent == run and q.name.startswith("state") for q in p.parents)
                  or Path(tempfile.gettempdir()).resolve() in p.parents)
            assert ok, f"refusing to modify {p}: not a QA session DB"
            return sqlite3.connect(str(p), timeout=10)
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)

    def table(self) -> tuple[str, list[str]]:
        with self._conn() as c:
            for (name,) in c.execute("select name from sqlite_master where type='table'"):
                cols = [r[1] for r in c.execute(f'pragma table_info("{name}")')]
                low = [x.lower() for x in cols]
                if any("user" in x for x in low) and any("refresh" in x for x in low) and any("created" in x for x in low):
                    return name, cols
        raise AssertionError(f"no session table (username/refresh/created_at) in {self.path}")

    def _col(self, *needles) -> str:
        _, cols = self.table()
        for n in needles:
            for c in cols:
                if n in c.lower():
                    return c
        raise AssertionError(f"no column like {needles} in {cols}")

    # ---------------------------------------------------------------- reads
    def rows(self) -> list[dict]:
        name, cols = self.table()
        with self._conn() as c:
            return [dict(zip(cols, r)) for r in c.execute(f'select * from "{name}"')]

    def rows_for(self, username: str) -> list[dict]:
        ucol = self._col("username", "user")
        return [r for r in self.rows() if r.get(ucol) == username]

    def row_for_cookie(self, cookie_value: str) -> dict | None:
        """Match by SHA-256 of the cookie id (hex or raw bytes, either form accepted)."""
        h = hashlib.sha256(cookie_value.encode()).hexdigest()
        for r in self.rows():
            for v in r.values():
                if v == h or v == bytes.fromhex(h) or (isinstance(v, str) and v.lower() == h):
                    return r
        return None

    def count(self) -> int:
        return len(self.rows())

    def raw_bytes(self) -> bytes:
        data = b""
        for suffix in ("", "-wal", "-journal"):
            f = Path(str(self.path) + suffix)
            if f.exists():
                data += f.read_bytes()
        return data

    def file_modes(self) -> dict[str, int]:
        out = {}
        for suffix in ("", "-wal", "-shm", "-journal"):
            f = Path(str(self.path) + suffix)
            if f.exists():
                out[f.name] = stat.S_IMODE(os.stat(f).st_mode)
        out[self.path.parent.name + "/"] = stat.S_IMODE(os.stat(self.path.parent).st_mode)
        return out

    # ---------------------------------------------------------------- aging (QA DB only)
    @staticmethod
    def _shift(value, seconds: float):
        if isinstance(value, (int, float)):
            return type(value)(value - seconds)
        if isinstance(value, str):
            try:
                t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return str(float(value) - seconds)
            return (t - dt.timedelta(seconds=seconds)).isoformat()
        raise AssertionError(f"unknown timestamp type {type(value)}")

    def age(self, username: str, column_needle: str, seconds: float) -> int:
        """Move created_at / last_seen of the user's sessions `seconds` into the past. -> rows changed."""
        name, _ = self.table()
        ucol, tcol = self._col("username", "user"), self._col(column_needle)
        rows = self.rows_for(username)
        with self._conn(write=True) as c:
            for r in rows:
                c.execute(f'update "{name}" set "{tcol}" = ? where "{ucol}" = ? and "{tcol}" = ?',
                          (self._shift(r[tcol], seconds), username, r[tcol]))
        return len(rows)

    def value(self, row: dict, needle: str):
        """Column value by name: exact match first ("access" must not pick "access_exp"), then substring."""
        for k, v in row.items():
            if k.lower() == needle:
                return v
        for k, v in row.items():
            if needle in k.lower():
                return v
        raise KeyError(needle)
