from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .errors import BadRequest, NotFound
from .security import PathGuard

_SAFE_BACKUP_RE = re.compile(r"^[0-9A-Za-z._-]+$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bounded(path: Path, max_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NotFound("Файл не найден") from exc
    if size > max_bytes:
        raise BadRequest(f"Файл больше лимита {max_bytes} байт")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BadRequest("Не удалось прочитать файл") from exc


def write_atomic(path: Path, data: bytes) -> None:
    mode = 0o600
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        pass
    temp = path.parent / f".{path.name}.tmp-{secrets.token_hex(6)}"
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


class BackupStore:
    """Stores only encrypted bytes, never plaintext."""

    def __init__(self, storage: Path, keep: int = 10) -> None:
        self.root = storage / "backups"
        self.keep = max(1, keep)
        self.root.mkdir(parents=True, exist_ok=True)

    def _key_dir(self, path: Path) -> Path:
        key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        return directory

    def create(self, path: Path, data: bytes) -> dict:
        directory = self._key_dir(path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name)[:80] or "file"
        backup_id = f"{stamp}-{secrets.token_hex(4)}-{safe_name}"
        target = directory / backup_id
        with open(target, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self._prune(directory)
        return {
            "id": backup_id,
            "name": path.name,
            "size": len(data),
            "created": datetime.now(timezone.utc).isoformat(),
        }

    def list_for(self, path: Path) -> list[dict]:
        directory = self._key_dir(path)
        items: list[dict] = []
        for entry in directory.iterdir():
            if not entry.is_file() or not PathGuard.backup_id_ok(entry.name):
                continue
            stat = entry.stat()
            items.append(
                {
                    "id": entry.name,
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "created": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
            )
        items.sort(key=lambda item: item["mtime"], reverse=True)
        return items

    def read_for(self, path: Path, backup_id: str) -> tuple[str, bytes]:
        if backup_id in ("", ".", "..") or not PathGuard.backup_id_ok(backup_id) or not _SAFE_BACKUP_RE.fullmatch(backup_id):
            raise BadRequest("Некорректный идентификатор резервной копии")
        directory = self._key_dir(path)
        target = directory / backup_id
        if not target.is_file():
            raise NotFound("Резервная копия не найдена")
        return target.name, target.read_bytes()

    def _prune(self, directory: Path) -> None:
        files: list[Path] = []
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            try:
                entry.stat()
                files.append(entry)
            except OSError:
                continue
        files.sort(key=lambda entry: entry.stat().st_mtime)
        for old in files[: max(0, len(files) - self.keep)]:
            try:
                old.unlink()
            except OSError:
                pass


class LockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        with self._guard:
            if len(self._locks) > 512:
                for stale in [name for name, held in self._locks.items() if not held.locked()]:
                    self._locks.pop(stale, None)
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            yield


def cleanup_uploads(root: Path, max_age_seconds: int = 7 * 24 * 3600) -> None:
    """Remove stale browser-upload directories. Never touches current files."""
    if not root.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    for entry in root.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
