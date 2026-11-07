from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from loguru import logger

from .config import Settings
from .errors import BadRequest, Conflict, KeyRequired, SopsFailure, UnsupportedFormat
from .files import BackupStore, LockRegistry, read_bounded, sha256_bytes, write_atomic
from .formats import (
    detect_format,
    format_info,
    guess_encrypted_format,
    semantic_equal,
    summarize_sops_metadata,
    validate_text,
)
from .git_service import GitService
from .security import PathGuard
from .sops_service import KeyMaterial, SopsRunner


Progress = Callable[[str, str], None]


def _emit(progress: Progress | None, phase: str, text: str) -> None:
    if progress is not None:
        try:
            progress(phase, text)
        except Exception:  # progress must never break the operation
            logger.debug("progress callback failed for phase {}", phase)


def _sha(data: bytes) -> str:
    return sha256_bytes(data)


class EditorService:
    def __init__(
        self,
        settings: Settings,
        guard: PathGuard,
        sops: SopsRunner,
        git: GitService,
    ) -> None:
        self.settings = settings
        self.guard = guard
        self.sops = sops
        self.git = git
        self.backups = BackupStore(settings.ensure_storage(), settings.backup_keep)
        self._locks = LockRegistry()

    # ------------------------------------------------------------------ open
    def _format_candidates(self, path: Path) -> list[str]:
        ext = path.suffix.lower()
        name = path.name.lower()
        known = (
            ext in (".yaml", ".yml", ".json", ".env", ".dotenv", ".ini", ".cfg", ".conf")
            or name == ".env"
            or name.startswith(".env.")
        )
        detected = detect_format(path)
        if known:
            return [detected.name]
        candidates = [detected.name, "yaml", "json", "dotenv", "ini"]
        unique: list[str] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

    def _decrypt_source(
        self, source: Path, data: bytes, key: KeyMaterial
    ) -> tuple[str, str, bool]:
        """Return (plaintext, format, is_encrypted) for ``data`` read from ``source``."""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFormat(
                "Бинарные SOPS-файлы не поддерживаются веб-редактором"
            ) from exc
        candidates = self._format_candidates(source)
        preferred = guess_encrypted_format(text)
        if preferred:
            candidates = [preferred] + [item for item in candidates if item != preferred]
        last_error: Exception | None = None
        for fmt in candidates:
            try:
                return self.sops.decrypt(source, fmt, key), fmt, True
            except KeyRequired as exc:
                # Public metadata is readable without any private key:
                # tell the user exactly which recipients are required.
                exc.details = {
                    **exc.details,
                    "encrypted": summarize_sops_metadata(text, fmt),
                }
                raise
            except SopsFailure as exc:
                if exc.code == "not_sops":
                    return text, detect_format(source, text).name, False
                last_error = exc
        raise last_error or SopsFailure("Не удалось расшифровать файл")

    def open_file(
        self, raw_path: str, key: KeyMaterial, progress: Progress | None = None
    ) -> dict:
        _emit(progress, "resolve", "Проверяем путь и разрешения")
        path = self.guard.resolve_file(raw_path)
        _emit(progress, "read", f"Читаем {path.name}")
        encrypted_bytes = read_bounded(path, self.settings.max_file_bytes)
        _emit(progress, "check", "Проверяем SOPS-метаданные")
        try:
            encrypted_text = encrypted_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFormat(
                "Бинарные SOPS-файлы не поддерживаются веб-редактором"
            ) from exc

        candidates = self._format_candidates(path)
        preferred = guess_encrypted_format(encrypted_text)
        if preferred:
            candidates = [preferred] + [item for item in candidates if item != preferred]
        _emit(progress, "decrypt", f"Расшифровываем ({preferred or candidates[0]}, sops decrypt)")

        content, used_format, is_encrypted = self._decrypt_source(path, encrypted_bytes, key)

        _emit(progress, "format", f"Определяем формат: {used_format}")
        info = format_info(used_format)
        # Prefer a format detected from the document for non-standard
        # extensions, but never change the format used for decryption.
        if not (path.suffix.lower() in {".yaml", ".yml", ".json", ".ini", ".cfg", ".conf"}):
            detected = detect_format(path, content)
            if detected.name == used_format:
                info = detected
            elif path.name.lower().startswith(".env"):
                info = format_info("dotenv")
            else:
                info = format_info(used_format)

        stat = path.stat()
        summary = (
            summarize_sops_metadata(encrypted_text, used_format)
            if is_encrypted
            else {
                "key_types": [],
                "age_recipients": [],
                "pgp_fingerprints": [],
                "lastmodified": None,
                "version": None,
                "encrypted_regex": None,
            }
        )
        _emit(progress, "git", "Читаем состояние git")
        try:
            git_info = self.git.repo_info(path)
        except Exception as exc:  # git is optional
            logger.debug("git info failed for {}: {}", path, exc)
            git_info = None
        return {
            "path": str(path),
            "name": path.name,
            "format": info.name,
            "language": info.language,
            "tree": info.tree,
            "content": content,
            "sha256": _sha(encrypted_bytes),
            "size": stat.st_size,
            "content_size": len(content.encode("utf-8")),
            "lines": content.count("\n") + 1,
            "mtime": round(stat.st_mtime, 3),
            "encrypted": summary,
            "encrypted_file": is_encrypted,
            "git": git_info,
            "key_source": key.label,
        }

    # ------------------------------------------------------------------ save
    def save_file(
        self,
        raw_path: str,
        content: str,
        fmt: str,
        expected_sha256: str,
        key: KeyMaterial,
        progress: Progress | None = None,
    ) -> dict:
        _emit(progress, "validate", "Проверяем документ перед шифрованием")
        fmt = (fmt or "").strip().lower()
        info = format_info(fmt)
        validate_text(content, info.name)
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.settings.max_file_bytes:
            raise BadRequest(f"Документ больше лимита {self.settings.max_file_bytes} байт")
        if not expected_sha256 or len(expected_sha256) != 64:
            raise BadRequest("Не передан хеш исходного файла")

        path = self.guard.resolve_file(raw_path)
        with self._locks.lock(str(path)):
            _emit(progress, "read", "Читаем текущий шифротекст и сверяем хеш")
            original = read_bounded(path, self.settings.max_file_bytes)
            current_sha = _sha(original)
            if current_sha != expected_sha256.strip().lower():
                raise Conflict(
                    "Файл изменился на сервере. Перезагрузите его перед сохранением.",
                    details={"sha256": current_sha},
                )
            _emit(progress, "decrypt", "Расшифровываем текущую версию")
            is_encrypted = True
            try:
                old_content = self.sops.decrypt(path, fmt, key)
            except KeyRequired:
                raise
            except SopsFailure as exc:
                if exc.code != "not_sops":
                    raise
                is_encrypted = False
                try:
                    old_content = original.decode("utf-8")
                except UnicodeDecodeError as decode_exc:
                    raise UnsupportedFormat(
                        "Бинарные файлы не поддерживаются веб-редактором"
                    ) from decode_exc

            actual = detect_format(path, old_content)
            if actual.name != fmt:
                raise BadRequest(
                    f"Формат файла на сервере — {actual.name}, а редактор прислал {fmt}"
                )
            if old_content.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                return {
                    "saved": False,
                    "reason": "unchanged",
                    "path": str(path),
                    "sha256": current_sha,
                    "content": old_content,
                    "encrypted_file": is_encrypted,
                }

            if not is_encrypted:
                _emit(progress, "write", "Записываем открытый файл")
                write_atomic(path, content_bytes)
                stat = path.stat()
                _emit(progress, "git", "Обновляем git-статус")
                try:
                    git_info = self.git.repo_info(path)
                except Exception:
                    git_info = None
                return {
                    "saved": True,
                    "path": str(path),
                    "sha256": _sha(content_bytes),
                    "content": content,
                    "size": stat.st_size,
                    "content_size": len(content_bytes),
                    "mtime": round(stat.st_mtime, 3),
                    "backup": None,
                    "git": git_info,
                    "encrypted": {
                        "key_types": [],
                        "age_recipients": [],
                        "pgp_fingerprints": [],
                        "lastmodified": None,
                        "version": None,
                        "encrypted_regex": None,
                    },
                    "encrypted_file": False,
                }

            _emit(progress, "backup", "Создаём резервную копию шифротекста")
            backup = self.backups.create(path, original)
            try:
                _emit(progress, "encrypt", "Шифруем через sops edit")
                self.sops.edit(path, fmt, content, key)
                new_bytes = read_bounded(path, self.settings.max_file_bytes)
                _emit(progress, "verify", "Проверяем результат расшифровкой")
                new_content = self.sops.decrypt(path, fmt, key)
                if not semantic_equal(content, new_content, fmt):
                    raise SopsFailure(
                        "Проверка после шифрования не прошла: содержимое изменилось. "
                        "Исходный файл восстановлен.",
                        code="verify_failed",
                        status_code=500,
                    )
            except Exception as exc:
                restore_error: Exception | None = None
                try:
                    write_atomic(path, original)
                except Exception as restore_exc:  # noqa: BLE001
                    restore_error = restore_exc
                    logger.critical(
                        "FATAL: cannot restore {} from backup {}: {}",
                        path,
                        backup.get("id"),
                        restore_exc,
                    )
                if restore_error is not None:
                    raise SopsFailure(
                        "Ошибка сохранения и не удалось восстановить исходный файл. "
                        "Используйте резервную копию.",
                        code="restore_failed",
                        status_code=500,
                        details={"backup": backup.get("id")},
                    ) from exc
                raise

            stat = path.stat()
            _emit(progress, "git", "Обновляем git-статус")
            try:
                git_info = self.git.repo_info(path)
            except Exception:
                git_info = None
            return {
                "saved": True,
                "path": str(path),
                "sha256": _sha(new_bytes),
                "content": new_content,
                "size": stat.st_size,
                "content_size": len(new_content.encode("utf-8")),
                "mtime": round(stat.st_mtime, 3),
                "backup": backup,
                "git": git_info,
                "encrypted": summarize_sops_metadata(
                    new_bytes.decode("utf-8", errors="replace"), fmt
                ),
                "encrypted_file": True,
            }

    # ------------------------------------------------------------- rotate
    def rotate_keys(
        self,
        raw_path: str,
        mode: str,
        key: KeyMaterial,
        progress: Progress | None = None,
    ) -> dict:
        """Re-key an encrypted file in place (``updatekeys``) or rotate its data key."""
        if mode not in ("updatekeys", "rotate"):
            raise BadRequest("Некорректный режим ротации ключей")
        _emit(progress, "resolve", "Проверяем файл")
        path = self.guard.resolve_file(raw_path)
        with self._locks.lock(str(path)):
            _emit(progress, "read", "Читаем шифротекст")
            original = read_bounded(path, self.settings.max_file_bytes)
            before_sha = _sha(original)
            try:
                ciphertext = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise UnsupportedFormat(
                    "Бинарные SOPS-файлы не поддерживаются веб-редактором"
                ) from exc
            fmt = guess_encrypted_format(ciphertext) or detect_format(path, ciphertext).name
            if not self.sops.is_encrypted(path):
                raise Conflict("Файл не зашифрован SOPS", code="not_encrypted")
            _emit(progress, "decrypt", "Расшифровываем текущую версию")
            plain_before = self.sops.decrypt(path, fmt, key)
            _emit(progress, "backup", "Создаём резервную копию шифротекста")
            backup = self.backups.create(path, original)
            try:
                if mode == "updatekeys":
                    _emit(progress, "rekey", "Обновляем получателей через sops updatekeys")
                    output = self.sops.update_keys(path, fmt, key)
                else:
                    _emit(progress, "rekey", "Перевыпускаем ключ данных через sops rotate")
                    self.sops.rotate(path, fmt, key)
                    output = ""
                new_bytes = read_bounded(path, self.settings.max_file_bytes)
                _emit(progress, "verify", "Проверяем результат")
                summary = summarize_sops_metadata(
                    new_bytes.decode("utf-8", errors="replace"), fmt
                )
                if not summary.get("key_types"):
                    raise SopsFailure(
                        "SOPS не сохранил метаданные ключей",
                        code="rekey_failed",
                        status_code=500,
                    )
                verified = "plaintext"
                warning = None
                try:
                    plain_after = self.sops.decrypt(path, fmt, key)
                except KeyRequired:
                    verified = "ciphertext"
                    warning = (
                        "Новый набор получателей не включает текущий ключ — "
                        "открытый текст проверить не удалось."
                    )
                else:
                    if not semantic_equal(plain_before, plain_after, fmt):
                        raise SopsFailure(
                            "Проверка после ротации не прошла: содержимое изменилось. "
                            "Исходный файл восстановлен.",
                            code="verify_failed",
                            status_code=500,
                        )
            except Exception as exc:
                restore_error: Exception | None = None
                try:
                    write_atomic(path, original)
                except Exception as restore_exc:  # noqa: BLE001
                    restore_error = restore_exc
                    logger.critical(
                        "FATAL: cannot restore {} from backup {}: {}",
                        path,
                        backup.get("id"),
                        restore_exc,
                    )
                if restore_error is not None:
                    raise SopsFailure(
                        "Ошибка ротации и не удалось восстановить исходный файл. "
                        "Используйте резервную копию.",
                        code="restore_failed",
                        status_code=500,
                        details={"backup": backup.get("id")},
                    ) from exc
                raise
            _emit(progress, "git", "Обновляем git-статус")
            try:
                git_info = self.git.repo_info(path)
            except Exception:
                git_info = None
            stat = path.stat()
            return {
                "ok": True,
                "path": str(path),
                "mode": mode,
                "changed": _sha(new_bytes) != before_sha,
                "sha256": _sha(new_bytes),
                "size": stat.st_size,
                "mtime": round(stat.st_mtime, 3),
                "output": [line for line in output.splitlines() if line.strip()][:100],
                "verified": verified,
                "warning": warning,
                "encrypted": summary,
                "backup": backup,
                "git": git_info,
            }

    # ------------------------------------------------------------- git diff
    def git_diff(
        self, raw_path: str, key: KeyMaterial, progress: Progress | None = None
    ) -> dict:
        """Decrypted working-tree file and its HEAD version, for a side-by-side view."""
        _emit(progress, "resolve", "Проверяем путь и git")
        path = self.guard.resolve_file(raw_path)
        root = self.git.find_root(path)
        if root is None:
            raise BadRequest("Файл не входит в git-репозиторий", code="not_a_repo")
        _emit(progress, "read", "Читаем текущий файл")
        current_bytes = read_bounded(path, self.settings.max_file_bytes)
        _emit(progress, "decrypt", "Расшифровываем текущую версию")
        current_text, current_fmt, current_encrypted = self._decrypt_source(
            path, current_bytes, key
        )
        blob = self.git.head_blob(path)
        head = None
        if blob is not None:
            _emit(progress, "head", "Расшифровываем версию из HEAD")
            head = self._head_document(path, blob, key)
        _emit(progress, "git", "Читаем состояние git")
        try:
            git_info = self.git.repo_info(path)
        except Exception:
            git_info = None
        if head is not None and git_info is not None:
            head["commit"] = git_info.get("last_commit")
        return {
            "path": str(path),
            "root": str(root),
            "relative_path": (git_info or {}).get("relative_path"),
            "current": {
                "content": current_text,
                "format": current_fmt,
                "encrypted_file": current_encrypted,
                "sha256": _sha(current_bytes),
            },
            "head": head,
            "git": git_info,
        }

    def _head_document(self, path: Path, blob: bytes, key: KeyMaterial) -> dict:
        """Decrypt a HEAD blob in an isolated directory (no .sops.yaml needed)."""
        sha = _sha(blob)
        if b"\x00" in blob[:8192]:
            return {
                "binary": True,
                "content": None,
                "format": None,
                "encrypted_file": None,
                "sha256": sha,
            }
        with tempfile.TemporaryDirectory(prefix="sops-head-") as tmp:
            candidate = Path(tmp) / path.name
            candidate.write_bytes(blob)
            os.chmod(candidate, 0o600)
            try:
                text, fmt, is_encrypted = self._decrypt_source(candidate, blob, key)
            except UnsupportedFormat:
                return {
                    "binary": True,
                    "content": None,
                    "format": None,
                    "encrypted_file": None,
                    "sha256": sha,
                }
        return {
            "binary": False,
            "content": text,
            "format": fmt,
            "encrypted_file": is_encrypted,
            "sha256": sha,
        }

    # ------------------------------------------------------------- encrypt
    @staticmethod
    def encrypted_sibling(path: Path) -> Path:
        name = path.name
        if name.startswith("."):
            return path.with_name(f"{name}.enc")
        return path.with_name(f"{path.stem}.enc{path.suffix}")

    def _check_plaintext(self, name: str, data: bytes) -> tuple[str, str]:
        if len(data) > self.settings.max_file_bytes:
            raise BadRequest(f"Файл больше лимита {self.settings.max_file_bytes} байт")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFormat(
                "Бинарные файлы не поддерживаются: нужен YAML, JSON, dotenv или INI"
            ) from exc
        info = detect_format(Path(name), text)
        validate_text(text, info.name)
        return text, info.name

    def _verify_encrypted(self, target: Path, fmt: str) -> dict:
        if not self.sops.is_encrypted(target):
            raise SopsFailure(
                "SOPS не создал шифротекст. Проверьте получателей и .sops.yaml.",
                code="encrypt_failed",
                status_code=500,
            )
        encrypted_text = target.read_text(encoding="utf-8", errors="replace")
        summary = summarize_sops_metadata(encrypted_text, fmt)
        if not summary.get("key_types"):
            raise SopsFailure(
                "В зашифрованном файле нет метаданных SOPS",
                code="encrypt_failed",
                status_code=500,
            )
        return summary

    def encrypt_plain(self, name: str, data: bytes, recipients: list[str], target: Path) -> dict:
        """Encrypt uploaded plaintext into ``target``; plaintext stays only in a temp dir."""
        _text, fmt = self._check_plaintext(name, data)
        temp_dir = Path(tempfile.mkdtemp(prefix="sops-plain-"))
        try:
            os.chmod(temp_dir, 0o700)
            plain = temp_dir / name
            plain.write_bytes(data)
            os.chmod(plain, 0o600)
            if self.sops.is_encrypted(plain):
                raise Conflict(
                    "Файл уже зашифрован SOPS — повторное шифрование не требуется",
                    code="already_encrypted",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.sops.encrypt(
                    plain,
                    fmt,
                    target,
                    recipients or None,
                    filename_override=str(target),
                )
            except OSError as exc:
                raise BadRequest("Не удалось записать зашифрованный файл") from exc
        finally:
            for child in temp_dir.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                temp_dir.rmdir()
            except OSError:
                pass
        try:
            summary = self._verify_encrypted(target, fmt)
        except SopsFailure:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        stat = target.stat()
        return {
            "path": str(target),
            "name": target.name,
            "format": fmt,
            "size": stat.st_size,
            "encrypted": summary,
            "recipients": summary.get("age_recipients") or recipients,
        }

    def encrypt_path(self, raw_path: str, recipients: list[str]) -> dict:
        """Encrypt a plaintext file inside the allowed roots into a sibling *.enc file."""
        source = self.guard.resolve_file(raw_path)
        data = read_bounded(source, self.settings.max_file_bytes)
        if self.sops.is_encrypted(source):
            raise Conflict("Файл уже зашифрован SOPS", code="already_encrypted")
        text, fmt = self._check_plaintext(source.name, data)
        target = self.encrypted_sibling(source)
        if target.exists():
            raise Conflict(
                f"Файл {target.name} уже существует",
                code="exists",
                details={"path": str(target)},
            )
        with self._locks.lock(str(source)):
            try:
                self.sops.encrypt(
                    source,
                    fmt,
                    target,
                    recipients or None,
                    filename_override=str(target),
                )
            except SopsFailure:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
            except OSError as exc:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise BadRequest("Не удалось записать зашифрованный файл") from exc
            try:
                summary = self._verify_encrypted(target, fmt)
            except SopsFailure:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise
        stat = target.stat()
        return {
            "path": str(target),
            "name": target.name,
            "source": str(source),
            "format": fmt,
            "lines": text.count("\n") + 1,
            "size": stat.st_size,
            "encrypted": summary,
            "recipients": summary.get("age_recipients") or recipients,
        }

    # --------------------------------------------------------------- backups
    def list_backups(self, raw_path: str) -> list[dict]:
        path = self.guard.resolve_file(raw_path)
        return self.backups.list_for(path)

    def read_backup(self, raw_path: str, backup_id: str) -> tuple[str, bytes]:
        path = self.guard.resolve_file(raw_path)
        return self.backups.read_for(path, backup_id)
