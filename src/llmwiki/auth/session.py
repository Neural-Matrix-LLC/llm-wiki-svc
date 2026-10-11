"""Server-side browser sessions (docs/sso-integration-design.md v1.1 item 2, v1.2).

The ``llmwiki_session`` cookie holds only a random 256-bit session id. The SSO tokens live
server-side in SQLite (``SESSION_DB_PATH``), keyed by the SHA-256 of that id, so a leaked database
does not hand out live cookies and the browser never holds a token. Columns: id hash, username,
sub, access, refresh, access_exp, csrf, created_at, last_seen. Logout and refresh failure delete
the row.

Lifetime is absolute (``SESSION_MAX_AGE_HOURS``, default 168 h = 7 days) from ``created_at``; a
refresh never extends it. A session unused for ``SESSION_IDLE_HOURS`` (default 12, design v1.2
Q-27) ends too. Expired rows are pruned at start and then at most hourly (on the next access).

File safety (design v1.2 D-16): the database is created with mode 0600 (its directory 0700 when
we create it); :meth:`SessionStore.init` refuses to run when the file is accessible by group or
others.

Concurrency: :meth:`SessionStore.refresh_locked` runs inside ``BEGIN IMMEDIATE``, re-reads the
row and only refreshes when no one else already did, so two requests (or two uvicorn workers
sharing the file) never refresh one session twice; the loser reuses the winner's new access
token. The api layer also holds a process-local asyncio lock per session so waiting happens
there, not in SQLite.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

COOKIE_NAME = "llmwiki_session"
LOGIN_CSRF_COOKIE = "llmwiki_login_csrf"
CSRF_HEADER = "X-CSRF-Token"
PRUNE_INTERVAL_S = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id_hash    TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    sub        TEXT NOT NULL,
    access     TEXT NOT NULL,
    refresh    TEXT NOT NULL,
    access_exp INTEGER NOT NULL,
    csrf       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
)
"""


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


@dataclass(frozen=True)
class SessionRecord:
    id_hash: str
    username: str
    sub: str
    access: str
    refresh: str
    access_exp: int
    csrf: str
    created_at: int
    last_seen: int

    def access_expires_within(self, seconds: float, now: float | None = None) -> bool:
        return self.access_exp - (time.time() if now is None else now) <= seconds


class SessionStore:
    def __init__(self, path: Path, max_age_s: int, clock: Callable[[], float] = time.time,
                 idle_s: int | None = None) -> None:
        self.path = Path(path)
        self.max_age_s = max_age_s
        self.idle_s = idle_s if idle_s is not None else max_age_s
        self._clock = clock
        self._last_prune = 0.0
        self._init_lock = threading.Lock()
        self._ready = False

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # explicit transactions
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init(self) -> None:
        with self._init_lock:
            if self._ready:
                return
            if not self.path.parent.exists():
                self.path.parent.mkdir(parents=True, mode=0o700)
            if not self.path.exists():
                os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
                os.chmod(self.path, 0o600)  # whatever the umask
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:
                raise RuntimeError(
                    f"session database {self.path} has mode {mode:04o}: it holds live SSO "
                    f"tokens and must not be accessible by group or others "
                    f"(chmod 600 {self.path})")
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute(_SCHEMA)
            finally:
                conn.close()
            self._ready = True
        self.prune()

    def _conn(self) -> sqlite3.Connection:
        if not self._ready:
            self.init()
        return self._connect()

    def _row(self, conn: sqlite3.Connection, id_hash: str) -> SessionRecord | None:
        row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        return SessionRecord(**dict(row)) if row else None

    def _expired(self, rec: SessionRecord, now: float) -> bool:
        return now - rec.created_at >= self.max_age_s or now - rec.last_seen >= self.idle_s

    def prune(self) -> int:
        """Delete rows past the absolute lifetime. Returns how many."""
        now = self._clock()
        self._last_prune = now
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM sessions WHERE created_at <= ? OR last_seen <= ?",
                               (int(now - self.max_age_s), int(now - self.idle_s)))
            return cur.rowcount
        finally:
            conn.close()

    def _maybe_prune(self) -> None:
        if self._clock() - self._last_prune >= PRUNE_INTERVAL_S:
            self.prune()

    # ------------------------------------------------------------------ API
    def create(self, *, username: str, sub: str, access: str, refresh: str,
               access_exp: int) -> tuple[str, SessionRecord]:
        """A new session; returns ``(session_id, record)``. Only the id's hash is stored."""
        session_id = secrets.token_urlsafe(32)  # 256 bits
        now = int(self._clock())
        rec = SessionRecord(_hash(session_id), username, sub, access, refresh, int(access_exp),
                            secrets.token_urlsafe(32), now, now)
        conn = self._conn()
        try:
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                         (rec.id_hash, rec.username, rec.sub, rec.access, rec.refresh,
                          rec.access_exp, rec.csrf, rec.created_at, rec.last_seen))
        finally:
            conn.close()
        return session_id, rec

    def get(self, session_id: str | None) -> SessionRecord | None:
        """The live session for a cookie value, or None (unknown, malformed or expired)."""
        if not session_id or len(session_id) > 128:
            return None
        self._maybe_prune()
        id_hash = _hash(session_id)
        conn = self._conn()
        try:
            rec = self._row(conn, id_hash)
            if rec is None:
                return None
            now = self._clock()
            if self._expired(rec, now):
                conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
                return None
            if now - rec.last_seen >= 60:  # cheap "last_seen" without a write per request
                conn.execute("UPDATE sessions SET last_seen = ? WHERE id_hash = ?",
                             (int(now), id_hash))
            return rec
        finally:
            conn.close()

    def delete(self, id_hash: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))
        finally:
            conn.close()

    def refresh_locked(self, id_hash: str, seen_refresh: str, margin_s: float,
                       do_refresh: Callable[[SessionRecord], tuple[str, str, int]]
                       ) -> tuple[SessionRecord | None, bool]:
        """Refresh under ``BEGIN IMMEDIATE`` (one writer at a time across processes).

        Re-reads the row: if the refresh token is no longer ``seen_refresh`` (another request
        or worker already rotated it) or the access token is no longer due, returns the row
        unchanged. Otherwise calls ``do_refresh(record) -> (access, refresh, access_exp)`` and
        stores the rotated tokens. ``do_refresh`` raising leaves the row untouched (the caller
        decides whether to delete it). Returns ``(record or None if gone, refreshed_here)``.
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rec = self._row(conn, id_hash)
                # someone else already rotated the refresh token we saw (or it is fresh): reuse
                if (rec is None or rec.refresh != seen_refresh
                        or not rec.access_expires_within(margin_s, self._clock())):
                    conn.execute("COMMIT")
                    return rec, False
                access, refresh, access_exp = do_refresh(rec)
                conn.execute("UPDATE sessions SET access = ?, refresh = ?, access_exp = ?, "
                             "last_seen = ? WHERE id_hash = ?",
                             (access, refresh, int(access_exp), int(self._clock()), id_hash))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return self._row(conn, id_hash), True
        finally:
            conn.close()

    def remaining_s(self, rec: SessionRecord) -> int:
        return max(0, int(rec.created_at + self.max_age_s - self._clock()))


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(expected: str | None, supplied: str | None) -> bool:
    """Constant-time equality of two non-trivial values (session CSRF / login double-submit)."""
    if not expected or not supplied or len(expected) < 32:
        return False
    return secrets.compare_digest(expected, supplied)
