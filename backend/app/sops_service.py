from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from loguru import logger

from .errors import BadRequest, KeyRequired, SopsFailure, UnsupportedFormat
from .formats import guess_encrypted_format
from .logging_setup import sanitize_stderr

_AGE_SECRET_RE = re.compile(r"^AGE-SECRET-KEY-1[0-9A-Z]+$")
_AGE_PUBLIC_RE = re.compile(r"^age1[0-9a-z]+$")


@dataclass(frozen=True)
class KeyMaterial:
    label: str = "none"
    file: Path | None = None
    inline: str | None = None


class SopsRunner:
    def __init__(
        self,
        sops_bin: str = "sops",
        age_keygen_bin: str = "age-keygen",
        timeout: float = 60.0,
    ) -> None:
        self.sops_bin = sops_bin
        self.age_keygen_bin = age_keygen_bin
        self.timeout = timeout
        self._version: str | None = None
        self._version_lock = threading.Lock()
        self._public_cache: dict[tuple[str, float], list[str]] = {}
        self._public_lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    @contextmanager
    def _environment(self, key: KeyMaterial) -> Iterator[dict[str, str]]:
        env = os.environ.copy()
        env["SOPS_DISABLE_VERSION_CHECK"] = "true"
        temp_dir: str | None = None
        try:
            if key.inline:
                temp_dir = tempfile.mkdtemp(prefix="sops-key-")
                os.chmod(temp_dir, 0o700)
                key_path = Path(temp_dir) / "keys.txt"
                key_path.write_text(key.inline.rstrip("\n") + "\n", encoding="utf-8")
                os.chmod(key_path, 0o600)
                # A session key is authoritative: do not silently fall back to
                # the server's ambient age identity.
                for name in (
                    "SOPS_AGE_KEY",
                    "SOPS_AGE_KEY_CMD",
                    "SOPS_AGE_SSH_PRIVATE_KEY_FILE",
                    "SOPS_AGE_SSH_PRIVATE_KEY_CMD",
                ):
                    env.pop(name, None)
                env["SOPS_AGE_KEY_FILE"] = str(key_path)
            elif key.file is not None:
                env["SOPS_AGE_KEY_FILE"] = str(key.file)
            yield env
        finally:
            if temp_dir is not None:
                for path in Path(temp_dir).glob("*"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                try:
                    Path(temp_dir).rmdir()
                except OSError:
                    pass

    def _run(
        self,
        args: list[str],
        *,
        key: KeyMaterial,
        cwd: Path | None = None,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        with self._environment(key) as env:
            if extra_env:
                env.update(extra_env)
            try:
                return subprocess.run(
                    args,
                    cwd=str(cwd) if cwd else None,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout or self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise SopsFailure(
                    "Утилита sops не найдена в контейнере",
                    code="sops_missing",
                    status_code=500,
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise SopsFailure(
                    "SOPS не завершился за отведённое время",
                    code="sops_timeout",
                    status_code=504,
                ) from exc

    def _raise_for_result(self, result: subprocess.CompletedProcess[bytes], operation: str) -> None:
        if result.returncode == 0:
            return
        stderr = sanitize_stderr(result.stderr)
        logger.debug("sops {} failed rc={} stderr={}", operation, result.returncode, stderr)
        low = stderr.lower()
        if (
            "no identity matched" in low
            or "identity did not match any of the recipients" in low
            or "incorrect identity for recipient block" in low
            or "failed to get the data key" in low
            or "no master key" in low
            or "failed to decrypt" in low
        ):
            raise KeyRequired(
                "Не удалось расшифровать файл: нет подходящего приватного ключа. "
                "Задайте age-ключ или проверьте SOPS_AGE_KEY_FILE.",
                details={"operation": operation},
            )
        if "metadata not found" in low or "not a sops" in low:
            raise SopsFailure(
                "Файл не является SOPS-файлом",
                code="not_sops",
                status_code=415,
                details={"operation": operation},
            )
        if "no matching creation rules" in low or "config file not found" in low:
            raise SopsFailure(
                "Для файла нет подходящего правила .sops.yaml",
                code="no_creation_rule",
                status_code=422,
                details={"operation": operation},
            )
        if "failed to parse input" in low or "malformed recipient" in low or "invalid checksum" in low:
            raise SopsFailure(
                "Некорректный age-получатель в .sops.yaml",
                code="invalid_recipient",
                status_code=422,
                details={"operation": operation},
            )
        if "could not run editor" in low or "refusing to reopen" in low:
            raise SopsFailure(
                "SOPS отклонил изменения: проверьте синтаксис YAML/JSON",
                code="invalid_content",
                status_code=422,
                details={"operation": operation},
            )
        if "invalid syntax" in low or "did not find expected" in low or "yaml:" in low or "json:" in low:
            raise SopsFailure(
                "SOPS не смог разобрать содержимое файла",
                code="invalid_content",
                status_code=422,
                details={"operation": operation},
            )
        raise SopsFailure(
            "SOPS завершился с ошибкой",
            code="sops_failed",
            status_code=422,
            details={"operation": operation},
        )

    # ------------------------------------------------------------------- API
    def version(self) -> str:
        with self._version_lock:
            if self._version is not None:
                return self._version
        result = self._run([self.sops_bin, "--version"], key=KeyMaterial())
        first = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
        value = first[0] if first else "unknown"
        with self._version_lock:
            self._version = value
        return value

    @staticmethod
    def _status_input_types(path: Path) -> list[str | None]:
        # ``sops filestatus`` guesses the format from the extension; dotenv files
        # named ``.env.enc``/``.env.prod`` need an explicit --input-type.
        types: list[str | None] = [None]
        name = path.name.lower()
        if name == ".env" or name.startswith(".env") or path.suffix.lower() in (".env", ".dotenv"):
            types.append("dotenv")
        return types

    def is_encrypted(self, path: Path) -> bool:
        import json

        try:
            head = path.read_bytes()[:65536].decode("utf-8", errors="replace")
        except OSError:
            head = ""
        types = list(self._status_input_types(path))
        guessed = guess_encrypted_format(head)
        if guessed:
            types.append(guessed)
        ordered: list[str | None] = []
        for item in types:
            if item not in ordered:
                ordered.append(item)
        for input_type in ordered:
            args = [self.sops_bin, "filestatus"]
            if input_type:
                args.extend(["--input-type", input_type])
            args.append(str(path))
            result = self._run(args, key=KeyMaterial())
            if result.returncode != 0:
                logger.debug(
                    "sops filestatus failed rc={} type={} for {}",
                    result.returncode,
                    input_type,
                    path,
                )
                continue
            try:
                payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if payload.get("encrypted"):
                return True
        return False

    def decrypt(self, path: Path, fmt: str, key: KeyMaterial) -> str:
        result = self._run(
            [
                self.sops_bin,
                "decrypt",
                "--input-type",
                fmt,
                "--output-type",
                fmt,
                str(path),
            ],
            key=key,
            cwd=path.parent,
        )
        self._raise_for_result(result, "decrypt")
        try:
            # CRLF from sops (INI/dotenv on Windows) would leak into values and
            # the editor; the document model is LF-only.
            return result.stdout.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError as exc:
            raise UnsupportedFormat(
                "Бинарные SOPS-файлы не поддерживаются веб-редактором"
            ) from exc

    def edit(self, path: Path, fmt: str, content: str, key: KeyMaterial) -> None:
        before = path.stat()
        temp_dir = Path(tempfile.mkdtemp(prefix="sops-edit-"))
        os.chmod(temp_dir, 0o700)
        try:
            suffix = {
                "yaml": ".yaml",
                "json": ".json",
                "dotenv": ".env",
                "ini": ".ini",
            }.get(fmt, ".txt")
            payload = temp_dir / f"payload{suffix}"
            payload.write_text(content, encoding="utf-8")
            os.chmod(payload, 0o600)
            counter = temp_dir / "editor-used"
            helper = Path(__file__).with_name("sops_editor_helper.py")
            python_exe = Path(sys.executable).resolve()
            if os.name == "nt":
                # SOPS parses SOPS_EDITOR as a shell-like command line and mangles
                # Windows backslashes/quotes. A tiny .cmd wrapper with plain paths
                # works reliably. Windows temp is executable.
                wrapper = temp_dir / "editor.cmd"
                wrapper.write_text(
                    "@echo off\r\n"
                    '"%SOPS_EDITOR_PYTHON%" "%SOPS_EDITOR_HELPER%" %*\r\n',
                    encoding="utf-8",
                )
                editor = wrapper.as_posix()
                editor_env = {
                    "SOPS_EDITOR_PYTHON": python_exe.as_posix(),
                    "SOPS_EDITOR_HELPER": helper.resolve().as_posix(),
                }
            elif os.access(helper, os.X_OK):
                # The Docker image marks the helper executable. This avoids
                # relying on exec permissions in /tmp (Docker tmpfs is noexec
                # by default in some setups).
                editor = helper.resolve().as_posix()
                editor_env = {}
            else:
                # Development checkout: helper is not +x. Execute it via Python.
                # Paths in the project are normally space-free; keep quotes for
                # SOPS implementations that parse them correctly.
                editor = f'"{python_exe.as_posix()}" "{helper.resolve().as_posix()}"'
                editor_env = {}
            extra_env = {
                "SOPS_EDITOR": editor,
                "SOPS_EDITOR_PAYLOAD": payload.as_posix(),
                "SOPS_EDITOR_COUNTER": counter.as_posix(),
                **editor_env,
            }
            result = self._run(
                [
                    self.sops_bin,
                    "edit",
                    "--input-type",
                    fmt,
                    "--output-type",
                    fmt,
                    str(path),
                ],
                key=key,
                cwd=path.parent,
                extra_env=extra_env,
            )
            self._raise_for_result(result, "edit")
            try:
                os.chmod(path, before.st_mode & 0o777)
            except OSError:
                pass
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

    def encrypt(
        self,
        plain: Path,
        fmt: str,
        output: Path,
        recipients: list[str] | None = None,
        filename_override: str | None = None,
    ) -> None:
        """Encrypt ``plain`` into ``output`` (ciphertext only on disk)."""
        args = [
            self.sops_bin,
            "encrypt",
            "--input-type",
            fmt,
            "--output-type",
            fmt,
            "--output",
            str(output),
        ]
        if recipients:
            # Explicit recipients win: encrypt an isolated copy so a nearby
            # .sops.yaml without a matching rule cannot abort the run.
            args.extend(["--age", ",".join(recipients)])
            with tempfile.TemporaryDirectory(prefix="sops-enc-") as tmp:
                isolated = Path(tmp) / plain.name
                isolated.write_bytes(plain.read_bytes())
                os.chmod(isolated, 0o600)
                args.append(str(isolated))
                result = self._run(args, key=KeyMaterial(), cwd=Path(tmp))
        else:
            args.extend(["--filename-override", filename_override or str(output)])
            args.append(str(plain))
            result = self._run(args, key=KeyMaterial(), cwd=plain.parent)
        self._raise_for_result(result, "encrypt")

    def update_keys(self, path: Path, fmt: str, key: KeyMaterial) -> str:
        """Re-encrypt the data key for the recipients from .sops.yaml; returns sops stdout."""
        result = self._run(
            [self.sops_bin, "updatekeys", "--yes", "--input-type", fmt, str(path)],
            key=key,
            cwd=path.parent,
        )
        self._raise_for_result(result, "updatekeys")
        return result.stdout.decode("utf-8", errors="replace").strip()

    def rotate(self, path: Path, fmt: str, key: KeyMaterial) -> None:
        """Generate a new data key and re-encrypt every value in place."""
        result = self._run(
            [
                self.sops_bin,
                "rotate",
                "--in-place",
                "--input-type",
                fmt,
                "--output-type",
                fmt,
                str(path),
            ],
            key=key,
            cwd=path.parent,
        )
        self._raise_for_result(result, "rotate")

    def public_recipients_from_file(self, key_file: Path) -> list[str]:
        """Derive public age recipients from a private key file (cached by mtime)."""
        try:
            mtime = key_file.stat().st_mtime
        except OSError:
            return []
        cache_key = (str(key_file), mtime)
        with self._public_lock:
            cached = self._public_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        try:
            result = subprocess.run(
                [self.age_keygen_bin, "-y", str(key_file)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        recipients: list[str] = []
        if result.returncode == 0:
            for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if _AGE_PUBLIC_RE.fullmatch(line):
                    recipients.append(line)
        with self._public_lock:
            self._public_cache[cache_key] = recipients
        return list(recipients)

    def validate_age_key(self, key_text: str) -> list[str]:
        text = key_text.strip()
        if not text:
            raise BadRequest("Пустой ключ")
        if len(text) > 64 * 1024:
            raise BadRequest("Ключ слишком большой")
        secret_lines = [
            line.strip()
            for line in text.splitlines()
            if _AGE_SECRET_RE.fullmatch(line.strip())
        ]
        if not secret_lines:
            raise BadRequest("Не найден age-ключ вида AGE-SECRET-KEY-1…")
        invalid = [
            line
            for line in text.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and not _AGE_SECRET_RE.fullmatch(line.strip())
        ]
        if invalid:
            raise BadRequest("В ключе есть посторонние строки")
        recipients: list[str] = []
        temp_dir = Path(tempfile.mkdtemp(prefix="sops-agecheck-"))
        os.chmod(temp_dir, 0o700)
        try:
            key_file = temp_dir / "keys.txt"
            key_file.write_text("\n".join(secret_lines) + "\n", encoding="utf-8")
            os.chmod(key_file, 0o600)
            result = subprocess.run(
                [self.age_keygen_bin, "-y", str(key_file)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                raise BadRequest("age-ключ не читается (age-keygen вернул ошибку)")
            for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if _AGE_PUBLIC_RE.fullmatch(line):
                    recipients.append(line)
        except FileNotFoundError:
            # The regex check above is enough when age-keygen is not installed.
            logger.warning("age-keygen не найден, проверка ключа только по формату")
        except subprocess.TimeoutExpired as exc:
            raise BadRequest("Проверка age-ключа не завершилась") from exc
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
        return recipients
