"""Background indexing jobs. Indexing never runs inside a request.

* One job at a time per process (thread) and per index folder (lock file, works on
  Windows and Linux), so several uvicorn workers or the CLI never index simultaneously.
* Other workers pick up the result through retriever.maybe_reload().
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.errors import IndexingAborted
from app.retriever import HybridEvaluationRetriever

log = logging.getLogger("rag.jobs")
STALE_LOCK_SECS = 6 * 3600


class IndexLock:
    """Cross-process lock via O_EXCL file creation (no fcntl, so it works on Windows)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._held = False

    def acquire(self) -> bool:
        try:
            if os.path.exists(self.path) and time.time() - os.path.getmtime(self.path) > STALE_LOCK_SECS:
                os.remove(self.path)                    # left over by a crashed process
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
            os.close(fd)
            self._held = True
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._held:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self._held = False

    def __enter__(self) -> "IndexLock":
        if not self.acquire():
            raise RuntimeError(f"another indexing is running (lock file {self.path})")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def index_lock_for(retriever: HybridEvaluationRetriever) -> IndexLock:
    """The lock lives next to the manifest, so API workers and the CLI share it."""
    return IndexLock(Path(retriever._manifest_file).resolve().parent / "index.lock")


class IndexJobManager:
    def __init__(self, retriever: HybridEvaluationRetriever,
                 run_preflight: bool | None = None,
                 serial_lock: threading.Lock | None = None) -> None:
        self.retriever = retriever
        self.run_preflight = settings.preflight if run_preflight is None else run_preflight
        # multi-RAG: one lock shared by every knowledge base of the process, so bases are
        # indexed one after another (fewer parallel API calls, exact per-base statistics)
        self.serial_lock = serial_lock
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._again = False
        self.status: dict = {"state": "idle"}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str = "update") -> dict:
        if mode not in ("update", "rebuild"):
            raise ValueError("mode must be 'update' or 'rebuild'")
        with self._lock:
            if self.running:
                return {"started": False, "reason": "a job is already running", **self.status}
            self._spawn(mode)
            return {"started": True, **self.status}

    def start_or_queue(self) -> dict:
        """Update now, or once more right after the running job (uploads during indexing
        are never lost)."""
        with self._lock:
            if self.running:
                self._again = True
                # never report "done" while a re-index is pending (uploads during indexing)
                self.status["state"] = "running"
                return {"started": False, "queued": True, **self.status}
            self._spawn("update")
            return {"started": True, **self.status}

    def _spawn(self, mode: str) -> None:
        self.status = {"state": "running", "mode": mode,
                       "started_at": datetime.now().isoformat(timespec="seconds")}
        self._thread = threading.Thread(target=self._loop, args=(mode,),
                                        name=f"index-{mode}", daemon=True)
        self._thread.start()

    def _loop(self, mode: str) -> None:
        while True:
            if self.serial_lock is not None:
                if not self.serial_lock.acquire(blocking=False):
                    self.status["state"] = "queued"
                    self.serial_lock.acquire()
                    self.status["state"] = "running"
                try:
                    self._run(mode)
                finally:
                    self.serial_lock.release()
            else:
                self._run(mode)
            with self._lock:
                if not self._again:
                    self._thread = None      # under the lock: a new upload spawns a new job
                    return
                self._again = False
                mode = "update"
                self.status.update(state="running", mode=mode,
                                   started_at=datetime.now().isoformat(timespec="seconds"))

    def _run(self, mode: str) -> None:
        lock = index_lock_for(self.retriever)
        if not lock.acquire():
            self.status.update(state="skipped", reason="another process is indexing")
            return
        t0 = time.time()
        try:
            if self.run_preflight:
                from app.llm import check_api_key, preflight
                preflight(check_api_key())
            sync = (self.retriever.rebuild_index() if mode == "rebuild"
                    else self.retriever.update_index())
            # an upload arrived while indexing: stay "running", the loop indexes again
            with self._lock:
                self.status.update(state="running" if self._again else "done", sync=sync)
        except IndexingAborted as e:
            self.status.update(state="aborted", error=str(e))
            log.error("indexing aborted: %s", e)
        except Exception as e:
            self.status.update(state="error", error=str(e))
            log.exception("indexing failed")
        finally:
            lock.release()
            self.status.update(secs=round(time.time() - t0, 1),
                               finished_at=datetime.now().isoformat(timespec="seconds"))
