from __future__ import annotations

import threading
import time
from typing import Any

from .errors import AppError

# Operations live for a few minutes: long enough for a stuck proxy, short
# enough that decrypted payloads do not linger in memory.
_DEFAULT_TTL = 600.0


class OperationRegistry:
    """In-memory progress registry for long-running open/save requests.

    The browser cannot see server-side progress when a proxy buffers the
    response, so the client polls this registry with a self-generated id while
    the original request is still in flight.
    """

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL) -> None:
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._ops: dict[str, dict[str, Any]] = {}

    def _purge(self, now: float) -> None:
        expired = [key for key, value in self._ops.items() if now - value["updated"] > self.ttl]
        for key in expired:
            self._ops.pop(key, None)

    def start(self, op_id: str) -> None:
        now = time.time()
        with self._lock:
            self._purge(now)
            self._ops[op_id] = {
                "status": "running",
                "events": [],
                "result": None,
                "error": None,
                "started": now,
                "updated": now,
            }

    def progress(self, op_id: str, phase: str, text: str) -> None:
        with self._lock:
            entry = self._ops.get(op_id)
            if entry is None:
                return
            entry["events"].append({"phase": phase, "text": text})
            entry["updated"] = time.time()

    def finish(self, op_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            entry = self._ops.get(op_id)
            if entry is None:
                return
            entry["status"] = "done"
            entry["result"] = result
            entry["updated"] = time.time()

    def fail(self, op_id: str, exc: AppError) -> None:
        with self._lock:
            entry = self._ops.get(op_id)
            if entry is None:
                return
            entry["status"] = "error"
            entry["error"] = {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "status": exc.status_code,
            }
            entry["updated"] = time.time()

    def get(self, op_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._ops.get(op_id)
            if entry is None:
                return {"status": "unknown", "events": [], "result": None, "error": None}
            return {
                "status": entry["status"],
                "events": list(entry["events"]),
                "result": entry["result"],
                "error": entry["error"],
            }

    def drop(self, op_id: str) -> None:
        with self._lock:
            self._ops.pop(op_id, None)
