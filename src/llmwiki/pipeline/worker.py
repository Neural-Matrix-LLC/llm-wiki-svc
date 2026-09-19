"""The ingest worker: bounded parallelism across sources, strict serialization per domain.

Phase 2 (design v1.4 §4.10.3, plan §21.2 C3/C5). Before this, REST and the
capture channels ran ``process_source`` on the ASGI server's anonymous
thread pool: unbounded, unserialized, and forgotten on restart - two sources
compiling into the same wiki raced on the manifest and the index. Now:

* **one lock per domain** (:func:`domain_lock`), held by the pipeline around
  embed + compile, so a domain's manifest has one writer at a time while
  extraction, routing and other domains' compiles overlap freely;
* **a bounded pool** (``WORKER_THREADS``) fed by :meth:`CompileWorker.submit`;
  ``WORKER_MODE=inline`` runs the job in the caller instead (the CLI, the
  test suite, the MCP tool - anywhere waiting is the point);
* **a pending marker** per source (``status/_pending/{id}``) written at
  capture and removed when processing ends, so :meth:`CompileWorker.recover`
  can resubmit whatever a restart interrupted - bounded by in-flight work,
  never by corpus size;
* **parking** under the monthly hard cap (``capped()`` is supplied by the
  cost guard, plan §21.2 C5): a parked source is marked ``paused`` and
  retried every ``WORKER_RESUME_INTERVAL_S`` seconds; capture, search and
  answer are never blocked.

No broker: this is one process's worker, matching design §6's cost posture.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from llmwiki.config import Settings
from llmwiki.models.plan import WorkerStatus
from llmwiki.models.source import SourceStatus
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import STATUS_PREFIX, is_source_id, pending_key

logger = logging.getLogger(__name__)

PENDING_PREFIX = f"{STATUS_PREFIX}_pending/"

# Process-wide, so every IngestPipeline instance (tools.py builds one per call)
# and the worker's threads share the same lock for the same domain.
_DOMAIN_LOCKS: dict[str, threading.Lock] = {}
_DOMAIN_LOCKS_GUARD = threading.Lock()


def domain_lock(domain: str) -> threading.Lock:
    """The one lock every writer of ``domain``'s manifest must hold."""
    with _DOMAIN_LOCKS_GUARD:
        lock = _DOMAIN_LOCKS.get(domain)
        if lock is None:
            lock = _DOMAIN_LOCKS[domain] = threading.Lock()
        return lock


def mark_pending(store: ObjectStore, source_id: str) -> None:
    store.put(pending_key(source_id), b"", "application/octet-stream")


def clear_pending(store: ObjectStore, source_id: str) -> None:
    try:
        store.delete(pending_key(source_id))
    except (ObjectNotFound, FileNotFoundError):  # pragma: no cover - already gone
        pass


def list_pending(store: ObjectStore) -> list[str]:
    """Source ids whose processing was owed when the process last stopped."""
    ids = []
    for key in store.list(PENDING_PREFIX):
        source_id = key[len(PENDING_PREFIX):]
        if is_source_id(source_id):
            ids.append(source_id)
    return sorted(ids)


class CompileWorker:
    """Runs ``process(source_id)`` for submitted sources; see the module docstring."""

    def __init__(
        self,
        settings: Settings,
        store: ObjectStore,
        process: Callable[[str], SourceStatus],
        set_status: Callable[[SourceStatus], None],
        capped: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._process = process
        self._set_status = set_status
        self._capped = capped or (lambda: False)
        self.mode = settings.worker_mode
        self._pool: ThreadPoolExecutor | None = (
            ThreadPoolExecutor(max_workers=max(1, settings.worker_threads),
                               thread_name_prefix="llmwiki-ingest")
            if self.mode == "threads" else None
        )
        self._state = threading.Lock()
        self._queued: list[str] = []
        self._in_flight: set[str] = set()
        self._parked: deque[str] = deque()
        self._paused_reason = ""
        self._timer: threading.Timer | None = None
        self._closed = False

    # -- submission ---------------------------------------------------------------

    def submit(self, source_id: str) -> Future | None:
        """Queue one source. Inline mode runs it now and returns None."""
        if self._pool is None:
            self._run(source_id)
            return None
        with self._state:
            if self._closed:
                raise RuntimeError("worker is shut down")
            self._queued.append(source_id)
        return self._pool.submit(self._run, source_id)

    def run_now(self, source_id: str) -> SourceStatus:
        """Process in the caller's thread. Domain serialization still holds:
        the pipeline takes ``domain_lock`` itself."""
        return self._run(source_id)

    def recover(self) -> list[str]:
        """Resubmit every source with a pending marker - the restart path."""
        pending = list_pending(self.store)
        for source_id in pending:
            logger.info("worker: recovering pending source %s", source_id)
            self.submit(source_id)
        return pending

    # -- execution ------------------------------------------------------------------

    def _run(self, source_id: str) -> SourceStatus:
        with self._state:
            if source_id in self._queued:
                self._queued.remove(source_id)
        if self._capped():
            return self._park(source_id)
        with self._state:
            self._in_flight.add(source_id)
        try:
            status = self._process(source_id)
        finally:
            with self._state:
                self._in_flight.discard(source_id)
        clear_pending(self.store, source_id)
        return status

    def _park(self, source_id: str) -> SourceStatus:
        reason = ("monthly cost cap COST_HARD_CAP_MONTHLY_USD reached; processing resumes "
                  "when the cap is raised or the month rolls over")
        status = SourceStatus(source_id=source_id, state="paused", error=reason)
        self._set_status(status)
        with self._state:
            self._paused_reason = reason
            if source_id not in self._parked:
                self._parked.append(source_id)
            self._arm_timer_locked()
        logger.warning("worker: parked %s (%s)", source_id, reason)
        return status

    def _arm_timer_locked(self) -> None:
        if self._timer is not None or self._closed or self._pool is None:
            return
        self._timer = threading.Timer(self.settings.worker_resume_interval_s, self._retry_parked)
        self._timer.daemon = True
        self._timer.start()

    def _retry_parked(self) -> None:
        with self._state:
            self._timer = None
            parked = list(self._parked)
            self._parked.clear()
        if not parked:
            return
        if self._capped():
            with self._state:
                self._parked.extend(parked)
                self._arm_timer_locked()
            return
        with self._state:
            self._paused_reason = ""
        for source_id in parked:
            self.submit(source_id)

    def resume(self) -> list[str]:
        """Retry parked sources now (after the cap was raised). Returns what was resubmitted."""
        with self._state:
            parked = list(self._parked)
            self._parked.clear()
            self._paused_reason = ""
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        for source_id in parked:
            self.submit(source_id)
        return parked

    # -- observability --------------------------------------------------------------

    def status(self) -> WorkerStatus:
        with self._state:
            return WorkerStatus(
                mode=self.mode,
                paused=bool(self._parked),
                reason=self._paused_reason,
                queued={"pending": len(self._queued)},
                in_flight=sorted(self._in_flight),
                parked=list(self._parked),
            )

    def shutdown(self, wait: bool = True) -> None:
        with self._state:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
