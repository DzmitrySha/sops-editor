from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from .config import Settings
from .errors import BadRequest, SopsFailure
from .files import write_atomic
from .sops_service import SopsRunner


class KeyStore:
    """Encrypted, write-only storage for the server's age private key.

    The key is encrypted with a Fernet master key supplied out of band via
    ``SOPS_EDITOR_KEYSTORE_KEY``. There is intentionally no API that returns
    the plaintext key; only recipients, fingerprint and timestamps are public.
    """

    def __init__(self, settings: Settings, sops: SopsRunner) -> None:
        self.settings = settings
        self.sops = sops
        self.root = settings.storage_path / "server-key"
        self.path = self.root / "keyring.json"
        self._lock = threading.RLock()
        self._cache: tuple[int, int, str] | None = None

    @property
    def available(self) -> bool:
        return bool(
            self.settings.key_store_secret
            and self.settings.key_store_secret.get_secret_value().strip()
        )

    @property
    def configured(self) -> bool:
        return self.path.is_file()

    def _fernet(self) -> Fernet:
        if not self.available:
            raise BadRequest(
                "Хранилище серверного ключа выключено: задайте SOPS_EDITOR_KEYSTORE_KEY "
                "(32-byte urlsafe base64 Fernet key)."
            )
        raw = self.settings.key_store_secret.get_secret_value().strip().encode()  # type: ignore[union-attr]
        try:
            return Fernet(raw)
        except (ValueError, TypeError) as exc:
            raise BadRequest(
                "SOPS_EDITOR_KEYSTORE_KEY должен быть 32-byte urlsafe base64 ключом Fernet"
            ) from exc

    def _metadata(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SopsFailure(
                "Хранилище серверного ключа повреждено",
                code="key_store_error",
                status_code=500,
            ) from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise SopsFailure(
                "Неподдерживаемый формат хранилища серверного ключа",
                code="key_store_error",
                status_code=500,
            )
        return data

    def status(self) -> dict:
        result = {
            "available": self.available,
            "configured": False,
            "recipients": [],
            "fingerprint": None,
            "updated_at": None,
            "storage": "encrypted",
        }
        if not self.path.is_file():
            return result
        try:
            metadata = self._metadata()
        except SopsFailure as exc:
            result.update({"configured": True, "error": exc.message})
            return result
        result.update(
            {
                "configured": True,
                "recipients": list(metadata.get("recipients") or []),
                "fingerprint": metadata.get("fingerprint"),
                "updated_at": metadata.get("updated_at"),
            }
        )
        return result

    def recipients(self) -> list[str]:
        if not self.configured:
            return []
        return list(self._metadata().get("recipients") or [])

    def load_inline(self) -> str | None:
        """Return the decrypted private key for internal use only."""
        if not self.configured:
            return None
        try:
            stat = self.path.stat()
        except OSError as exc:
            raise SopsFailure(
                "Не удалось прочитать хранилище серверного ключа",
                code="key_store_error",
                status_code=500,
            ) from exc
        cache_key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache is not None and self._cache[:2] == cache_key:
                return self._cache[2]
        metadata = self._metadata()
        token = metadata.get("key")
        if not isinstance(token, str):
            raise SopsFailure(
                "В хранилище серверного ключа нет зашифрованного ключа",
                code="key_store_error",
                status_code=500,
            )
        try:
            plaintext = self._fernet().decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise SopsFailure(
                "Не удалось расшифровать серверный ключ: проверьте SOPS_EDITOR_KEYSTORE_KEY",
                code="key_store_error",
                status_code=500,
            ) from exc
        with self._lock:
            self._cache = (*cache_key, plaintext)
        return plaintext

    def update(self, key_text: str) -> dict:
        """Validate and replace the server key. The plaintext is never returned."""
        text = key_text.strip()
        recipients = self.sops.validate_age_key(text)
        normalized = text + "\n"
        token = self._fernet().encrypt(normalized.encode("utf-8")).decode("ascii")
        metadata = {
            "version": 1,
            "key": token,
            "recipients": recipients,
            "fingerprint": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
            write_atomic(self.path, json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"))
            os.chmod(self.path, 0o600)
            self._cache = None
        logger.info("Server age key updated ({} recipient(s))", len(recipients))
        return self.status()
