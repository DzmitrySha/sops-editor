from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class Session:
    key: str
    created_at: float
    expires_at: float
    recipients: list[str]


class SessionStore:
    """In-memory store for per-browser age keys.

    Plaintext keys never touch disk here. The TTL is sliding: each read of a
    live session extends it, so an active browser does not lose the key.
    """

    def __init__(self, ttl_seconds: int = 8 * 60 * 60) -> None:
        self.ttl_seconds = max(60, ttl_seconds)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def create(self, key: str, recipients: list[str] | None = None) -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            self._sessions[sid] = Session(
                key=key,
                created_at=now,
                expires_at=now + self.ttl_seconds,
                recipients=list(recipients or []),
            )
        return sid

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if session.expires_at <= now:
                self._sessions.pop(sid, None)
                return None
            session.expires_at = now + self.ttl_seconds
            return session

    def delete(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    def purge(self) -> None:
        with self._lock:
            self._purge_locked(time.time())

    def _purge_locked(self, now: float) -> None:
        dead = [sid for sid, session in self._sessions.items() if session.expires_at <= now]
        for sid in dead:
            self._sessions.pop(sid, None)

    def __len__(self) -> int:
        with self._lock:
            self._purge_locked(time.time())
            return len(self._sessions)
