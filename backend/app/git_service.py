from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from .config import Settings
from .errors import BadRequest, Conflict
from .logging_setup import sanitize_stderr

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SCP_LIKE_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9._-]+):(.+)$")


def redact_git_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https", "ssh") and parts.hostname:
            netloc = parts.hostname
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        pass
    return url


class GitService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # ``rev-parse --show-toplevel`` is spawned on every open/save; the
        # answer only changes when repositories are created or moved.
        self._root_cache: dict[str, tuple[float, Path | None]] = {}
        self._root_lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    def _run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        if extra_env:
            env.update(extra_env)
        # Repositories are mounted from the host; Docker often makes their
        # owner differ from the container user. Explicit trust is fine here:
        # only paths inside SOPS_EDITOR_ROOTS reach git.
        command = list(args)
        if command and command[0] == "git":
            command = [command[0], "-c", "safe.directory=*", *command[1:]]
        try:
            return subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BadRequest("git не установлен в контейнере") from exc
        except subprocess.TimeoutExpired as exc:
            raise BadRequest("git не завершился за отведённое время") from exc

    @staticmethod
    def _text(result: subprocess.CompletedProcess[bytes]) -> str:
        return result.stdout.decode("utf-8", errors="replace").strip()

    def find_root(self, path: Path) -> Path | None:
        if not path.is_dir():
            path = path.parent
        key = str(path)
        now = time.monotonic()
        with self._root_lock:
            cached = self._root_cache.get(key)
        if cached is not None and now - cached[0] < 30.0:
            return cached[1]
        result = self._run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], cwd=path)
        root: Path | None = None
        if result.returncode == 0:
            candidate = Path(self._text(result)).resolve()
            root = candidate if candidate.is_dir() else None
        with self._root_lock:
            if len(self._root_cache) > 1024:
                self._root_cache.clear()
            self._root_cache[key] = (now, root)
        return root

    def repo_info(self, path: Path) -> dict | None:
        root = self.find_root(path)
        if root is None:
            return None
        relative = path.resolve().relative_to(root).as_posix()
        branch_result = self._run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
        branch = self._text(branch_result) if branch_result.returncode == 0 else "?"
        status_result = self._run(
            ["git", "-C", str(root), "status", "--porcelain", "--", relative], cwd=root
        )
        status_lines = [line for line in self._text(status_result).splitlines() if line.strip()]
        log_result = self._run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "-1",
                "--format=%h%x00%s%x00%an%x00%cI",
                "--",
                relative,
            ],
            cwd=root,
        )
        last_commit = None
        if log_result.returncode == 0:
            parts = self._text(log_result).split("\x00")
            if len(parts) == 4:
                last_commit = {
                    "hash": parts[0],
                    "subject": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                }
        remote_result = self._run(["git", "-C", str(root), "remote", "get-url", "origin"], cwd=root)
        remote = redact_git_url(self._text(remote_result)) if remote_result.returncode == 0 else None
        return {
            "root": str(root),
            "relative_path": relative,
            "branch": branch,
            "dirty": bool(status_lines),
            "status": status_lines,
            "last_commit": last_commit,
            "remote": remote,
        }

    def head_blob(self, path: Path) -> bytes | None:
        """Content of ``path`` at HEAD, or None when the file is not committed."""
        root = self.find_root(path)
        if root is None:
            raise BadRequest("Файл не входит в git-репозиторий", code="not_a_repo")
        relative = path.resolve().relative_to(root).as_posix()
        exists = self._run(
            ["git", "-C", str(root), "cat-file", "-e", f"HEAD:{relative}"], cwd=root
        )
        if exists.returncode != 0:
            return None
        result = self._run(["git", "-C", str(root), "show", f"HEAD:{relative}"], cwd=root)
        if result.returncode != 0:
            raise BadRequest("Не удалось прочитать версию из HEAD", code="git_failed")
        return result.stdout

    def _author_args(self, root: Path) -> list[str]:
        args: list[str] = []
        for key, default in (
            ("user.name", self.settings.git_author_name),
            ("user.email", self.settings.git_author_email),
        ):
            result = self._run(["git", "-C", str(root), "config", "--get", key], cwd=root)
            if result.returncode != 0 or not self._text(result):
                args.extend(["-c", f"{key}={default}"])
        return args

    def _config_file_value(self, key: str) -> str:
        config = Path(self.settings.git_config_global).expanduser()
        if not config.is_file():
            return ""
        result = self._run(
            ["git", "config", "--file", str(config), "--get", key],
            cwd=Path.cwd(),
        )
        return self._text(result) if result.returncode == 0 else ""

    def default_identity(self) -> dict[str, str]:
        """Identity from the optional host .gitconfig, for UI prefill only."""
        name = self._config_file_value("user.name")
        email = self._config_file_value("user.email")
        if name or email:
            return {"name": name, "email": email, "source": "gitconfig"}
        return {"name": "", "email": "", "source": "none"}

    @staticmethod
    def _validate_identity(name: str, email: str) -> None:
        if not name or not email:
            raise BadRequest("Укажите имя и email автора коммита (как в GitLab)")
        if len(name) > 100 or len(email) > 254:
            raise BadRequest("Слишком длинное имя или email автора")
        forbidden = set("\r\n\x00<>")
        if any(ch in forbidden or ord(ch) < 32 for ch in name + email):
            raise BadRequest("Некорректное имя или email автора")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
            raise BadRequest("Некорректный email автора")

    def _identity_args(
        self,
        root: Path,
        author_name: str | None,
        author_email: str | None,
    ) -> list[str]:
        if author_name is None and author_email is None:
            return self._author_args(root)
        name = (author_name or "").strip()
        email = (author_email or "").strip()
        self._validate_identity(name, email)
        # Set both author and committer: GitLab badges such a commit as the
        # user's, rather than "SOPS Editor <sops-editor@localhost>".
        return ["-c", f"user.name={name}", "-c", f"user.email={email}"]

    @staticmethod
    def _message_with_tool_note(message: str) -> str:
        trailer = "Made-with: SOPS Editor"
        if re.search(r"(?im)^Made-with:\s*SOPS Editor\s*$", message):
            return message
        return message.rstrip() + "\n\n" + trailer

    def _askpass_env(self) -> tuple[dict[str, str], Path | None]:
        token = self.settings.git_token.get_secret_value() if self.settings.git_token else ""
        if not token:
            return {}, None
        static = Path("/usr/local/bin/sops-editor-askpass")
        if static.is_file() and os.access(static, os.X_OK):
            return {
                "GIT_ASKPASS": str(static),
                "SOPS_EDITOR_GIT_TOKEN": token,
                "SOPS_EDITOR_GIT_USERNAME": self.settings.git_username,
            }, None
        # /tmp is noexec in some container setups; keep the helper on the
        # writable /data volume instead.
        temp_dir = Path(
            tempfile.mkdtemp(prefix="git-askpass-", dir=str(self.settings.ensure_storage()))
        )
        os.chmod(temp_dir, 0o700)
        helper = temp_dir / "askpass.sh"
        helper.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *sername*) printf '%s\\n' \"$SOPS_EDITOR_GIT_USERNAME\" ;;\n"
            "  *assword*) printf '%s\\n' \"$SOPS_EDITOR_GIT_TOKEN\" ;;\n"
            "  *) printf '\\n' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(helper, 0o700)
        env = {
            "GIT_ASKPASS": str(helper),
            "SOPS_EDITOR_GIT_TOKEN": token,
            "SOPS_EDITOR_GIT_USERNAME": self.settings.git_username,
        }
        return env, temp_dir

    @staticmethod
    def _is_ssh_url(url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme == "ssh":
            return True
        if parts.scheme:
            return False
        return bool(_SCP_LIKE_RE.match(url))

    def _ssh_key(self) -> tuple[Path | None, str | None]:
        """Find a usable private key on the read-only ``/ssh`` mount."""
        if self.settings.git_ssh_key:
            candidates = [Path(self.settings.git_ssh_key).expanduser()]
        else:
            base = Path(self.settings.git_ssh_dir).expanduser()
            preferred = ("id_ed25519", "id_ecdsa", "id_rsa")
            candidates = [base / name for name in preferred if (base / name).is_file()]
            try:
                candidates.extend(
                    sorted(
                        (entry for entry in base.iterdir() if entry.is_file()),
                        key=lambda entry: entry.name,
                    )
                )
            except OSError:
                pass
        for candidate in candidates:
            if candidate.name.lower() in {"known_hosts", "config", "authorized_keys"}:
                continue
            if candidate.suffix.lower() in {".pub", ".ppk"}:
                continue
            try:
                if candidate.stat().st_size > 64 * 1024:
                    continue
                with candidate.open("rb") as handle:
                    head = handle.read(256)
            except OSError:
                continue
            if b"PRIVATE KEY-----" not in head:
                continue
            return candidate, f"key:{candidate.name}"
        return None, None

    def _known_hosts_file(self) -> Path | None:
        if self.settings.git_ssh_strict_host_key_checking == "no":
            return None
        try:
            directory = self.settings.ensure_storage() / "ssh"
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            target = directory / "known_hosts"
            if not target.exists():
                if self.settings.git_ssh_known_hosts:
                    source = Path(self.settings.git_ssh_known_hosts).expanduser()
                else:
                    source = Path(self.settings.git_ssh_dir).expanduser() / "known_hosts"
                data = b""
                try:
                    if source.is_file():
                        data = source.read_bytes()[: 1024 * 1024]
                except OSError:
                    data = b""
                target.write_bytes(data)
                os.chmod(target, 0o600)
            return target
        except OSError as exc:
            logger.warning("cannot prepare ssh known_hosts: {}", exc)
            return None

    def _ssh_auth_env(self) -> tuple[dict[str, str], Path | None]:
        key, _source = self._ssh_key()
        if key is None and self.settings.git_ssh_key:
            raise BadRequest(
                "SSH-ключ не найден или не является приватным ключом. "
                "Проверьте SOPS_EDITOR_GIT_SSH_KEY и монтирование /ssh."
            )
        strict = self.settings.git_ssh_strict_host_key_checking
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            "-o",
            f"StrictHostKeyChecking={strict}",
        ]
        known_hosts = self._known_hosts_file()
        if known_hosts is not None:
            command.extend(["-o", f"UserKnownHostsFile={known_hosts}"])
        temp_dir: Path | None = None
        if key is not None:
            # The host ~/.ssh bind mount is often 0777 on Windows; OpenSSH
            # rejects such keys, so use a private 0600 copy for one operation.
            temp_dir = Path(
                tempfile.mkdtemp(
                    prefix="git-ssh-",
                    dir=str(self.settings.ensure_storage()),
                )
            )
            os.chmod(temp_dir, 0o700)
            private = temp_dir / "key"
            shutil.copyfile(key, private)
            os.chmod(private, 0o600)
            command.extend(["-i", str(private), "-o", "IdentitiesOnly=yes"])
        return {"GIT_SSH_COMMAND": shlex.join(command)}, temp_dir

    def _auth_env(self, url: str) -> tuple[dict[str, str], Path | None]:
        if self._is_ssh_url(url):
            return self._ssh_auth_env()
        return self._askpass_env()

    def _remote_url(self, root: Path) -> str:
        result = self._run(["git", "-C", str(root), "remote", "get-url", "origin"], cwd=root)
        return self._text(result) if result.returncode == 0 else ""

    def ssh_status(self) -> dict:
        key, source = self._ssh_key()
        if key is not None:
            return {"ready": True, "source": source}
        if os.environ.get("SSH_AUTH_SOCK"):
            return {"ready": True, "source": "agent"}
        return {"ready": False, "source": None}

    @staticmethod
    def _rmtree(path: Path) -> None:
        # Windows marks .git objects read-only, so plain rmtree can leave a
        # half-deleted clone behind.
        if not path.exists():
            return

        def onerror(func, target, _exc) -> None:  # type: ignore[no-untyped-def]
            try:
                os.chmod(target, 0o700)
                func(target)
            except OSError:
                pass

        shutil.rmtree(path, onerror=onerror)

    @staticmethod
    def _cleanup_dir(path: Path | None) -> None:
        if path is not None:
            shutil.rmtree(path, ignore_errors=True)

    def commit(
        self,
        path: Path,
        message: str,
        push: bool = False,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> dict:
        message = (message or "").strip()
        if not message:
            raise BadRequest("Сообщение коммита пустое")
        if len(message) > 500:
            raise BadRequest("Сообщение коммита слишком длинное")
        root = self.find_root(path)
        if root is None:
            raise BadRequest("Файл не находится в git-репозитории")
        identity = self._identity_args(root, author_name, author_email)
        message = self._message_with_tool_note(message)
        relative = path.resolve().relative_to(root).as_posix()
        add = self._run(["git", "-C", str(root), "add", "--", relative], cwd=root)
        if add.returncode != 0:
            logger.warning("git add failed for {}: {}", relative, sanitize_stderr(add.stderr))
            raise BadRequest("git не смог добавить файл в индекс")
        staged = self._run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--", relative], cwd=root)
        if staged.returncode == 0:
            return {"committed": False, "reason": "no_changes", "git": self.repo_info(path)}
        commit = self._run(
            ["git", *identity, "-C", str(root), "commit", "--only", "-m", message, "--", relative],
            cwd=root,
        )
        if commit.returncode != 0:
            logger.warning("git commit failed for {}: {}", relative, sanitize_stderr(commit.stderr))
            raise BadRequest("git не смог создать коммит (проверьте настройки пользователя)")
        hash_result = self._run(["git", "-C", str(root), "rev-parse", "HEAD"], cwd=root)
        commit_hash = self._text(hash_result) if hash_result.returncode == 0 else None
        response: dict = {
            "committed": True,
            "hash": commit_hash,
            "message": message,
            "git": self.repo_info(path),
        }
        if push:
            response["push"] = self.push(path)
        return response

    def push(self, path: Path) -> dict:
        root = self.find_root(path)
        if root is None:
            raise BadRequest("Файл не находится в git-репозитории")
        env, temp_dir = self._auth_env(self._remote_url(root))
        try:
            result = self._run(
                ["git", "-C", str(root), "push"],
                cwd=root,
                timeout=90,
                extra_env=env,
            )
        finally:
            self._cleanup_dir(temp_dir)
        if result.returncode != 0:
            logger.warning("git push failed: {}", sanitize_stderr(result.stderr, 500))
            return {
                "ok": False,
                "error": "git push не удался. Проверьте remote и учётные данные.",
            }
        return {"ok": True, "output": self._text(result)[:1000]}

    @staticmethod
    def _normalize_subdir(subdir: str | None) -> str | None:
        value = (subdir or "").strip().strip("/")
        if not value:
            return None
        if (
            len(value) > 500
            or value.startswith("-")
            or "\\" in value
            or any(ch in value for ch in "\r\n\x00")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)
        ):
            raise BadRequest("Некорректная папка в репозитории")
        parts = PurePosixPath(value).parts
        if any(part in {"", ".", "..", ".git"} for part in parts):
            raise BadRequest("Некорректная папка в репозитории")
        return value

    @staticmethod
    def _clone_error(url: str, stderr: bytes) -> str:
        if not GitService._is_ssh_url(url):
            return "git clone не удался. Проверьте URL, ветку и доступ."
        text = stderr.decode("utf-8", errors="replace").lower()
        if "permission denied" in text and "publickey" in text:
            return (
                "SSH: GitLab не принял ключ. Смонтируйте host ~/.ssh в /ssh "
                "или добавьте ключ как deploy key с доступом на запись."
            )
        if "host key verification failed" in text or "remote host identification has changed" in text:
            return (
                "SSH: host key не прошёл проверку. Добавьте GitLab в "
                "known_hosts или задайте SOPS_EDITOR_GIT_SSH_STRICT_HOST_KEY_CHECKING=accept-new."
            )
        if "could not resolve hostname" in text:
            return "SSH: не удалось разрешить имя хоста. Проверьте URL и DNS контейнера."
        if "incorrect passphrase" in text or "load key" in text:
            return (
                "SSH: ключ защищён паролем, контейнер не может его запросить. "
                "Используйте ssh-agent или отдельный deploy key без пароля."
            )
        return "git clone по SSH не удался. Проверьте ключ, known_hosts и доступ к GitLab."

    def clone(
        self,
        url: str,
        branch: str | None,
        name: str | None,
        subdir: str | None = None,
    ) -> dict:
        if not self.settings.allow_git_clone:
            raise BadRequest("Клонирование git отключено (SOPS_EDITOR_ALLOW_GIT_CLONE=0)")
        url = (url or "").strip()
        if not url or len(url) > 2000 or any(ch in url for ch in "\r\n\x00 "):
            raise BadRequest("Некорректный git URL")
        if url.startswith("-"):
            raise BadRequest("Некорректный git URL")
        host = self._host_from_url(url)
        allowed = self.settings.git_allowed_hosts_list
        if allowed and (host or "").lower() not in allowed:
            raise BadRequest(f"Хост {host!r} не разрешён для клонирования")
        subdir = self._normalize_subdir(subdir)
        repo_root = self.settings.storage_path / "repos"
        repo_root.mkdir(parents=True, exist_ok=True)
        if name:
            if not _SAFE_NAME_RE.fullmatch(name) or name in (".", ".."):
                raise BadRequest("Некорректное имя репозитория")
            folder = name
        else:
            folder = self._name_from_url(url)
        target = (repo_root / folder).resolve()
        if repo_root.resolve() not in target.parents and target != repo_root.resolve():
            raise BadRequest("Некорректный путь репозитория")
        if target.exists():
            if any(target.iterdir()):
                raise Conflict(
                    f"Репозиторий уже склонирован: {target}",
                    details={"path": str(target)},
                )
            target.rmdir()
        env, temp_dir = self._auth_env(url)
        try:
            args = ["git", "clone", "--no-tags"]
            if subdir:
                # Partial + sparse clone: only the requested subtree (and the
                # root config files, e.g. .sops.yaml) is checked out.
                args.extend(["--filter=blob:none", "--sparse"])
            if branch:
                if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", branch):
                    raise BadRequest("Некорректное имя ветки")
                args.extend(["--branch", branch])
            args.extend(["--", url, str(target)])
            result = self._run(args, cwd=repo_root, timeout=180, extra_env=env)
        finally:
            self._cleanup_dir(temp_dir)
        if result.returncode != 0:
            self._rmtree(target)
            logger.warning("git clone failed for {}: {}", redact_git_url(url), sanitize_stderr(result.stderr, 500))
            raise BadRequest(self._clone_error(url, result.stderr))
        result_path = target
        if subdir:
            kind = self._run(
                ["git", "-C", str(target), "cat-file", "-t", f"HEAD:{subdir}"],
                cwd=target,
            )
            if kind.returncode != 0 or self._text(kind) != "tree":
                self._rmtree(target)
                raise BadRequest(f"Папка {subdir!r} не найдена в репозитории")
            sparse = self._run(
                ["git", "-C", str(target), "sparse-checkout", "set", "--cone", subdir],
                cwd=target,
                timeout=120,
            )
            if sparse.returncode != 0:
                self._rmtree(target)
                logger.warning("sparse checkout failed for {}: {}", subdir, sanitize_stderr(sparse.stderr, 500))
                raise BadRequest("Не удалось включить sparse checkout для выбранной папки")
            result_path = target / subdir
        info = self.repo_info(result_path)
        branch_result = self._run(["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"], cwd=target)
        return {
            "path": str(result_path),
            "repo_root": str(target),
            "subdir": subdir,
            "branch": self._text(branch_result) if branch_result.returncode == 0 else branch,
            "remote": redact_git_url(url),
            "git": info,
        }

    def pull(self, path: Path) -> dict:
        root = self.find_root(path)
        if root is None:
            raise BadRequest("Файл не находится в git-репозитории")
        status = self._run(["git", "-C", str(root), "status", "--porcelain"], cwd=root)
        if self._text(status):
            raise Conflict("В репозитории есть локальные изменения; pull отменён")
        env, temp_dir = self._auth_env(self._remote_url(root))
        try:
            result = self._run(
                ["git", "-C", str(root), "pull", "--ff-only"],
                cwd=root,
                timeout=120,
                extra_env=env,
            )
        finally:
            self._cleanup_dir(temp_dir)
        if result.returncode != 0:
            logger.warning("git pull failed: {}", sanitize_stderr(result.stderr, 500))
            raise BadRequest("git pull не удался (нужен fast-forward)")
        return {"ok": True, "output": self._text(result)[:1000], "git": self.repo_info(path)}

    @staticmethod
    def _host_from_url(url: str) -> str | None:
        try:
            parts = urlsplit(url)
            if parts.scheme in ("http", "https", "ssh", "git"):
                return parts.hostname
        except ValueError:
            return None
        match = _SCP_LIKE_RE.match(url)
        return match.group(1) if match else None

    @staticmethod
    def _name_from_url(url: str) -> str:
        candidate = url.rstrip("/").rsplit("/", 1)[-1]
        if ":" in candidate and "/" not in candidate:
            candidate = candidate.rsplit(":", 1)[-1]
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-_")
        return candidate[:60] or "repo"
