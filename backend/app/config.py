from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_roots() -> str:
    # ``|`` keeps Windows drive letters (C:\...) unambiguous.
    return os.environ.get("SOPS_EDITOR_ROOTS", "/workspace|/data/repos")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOPS_EDITOR_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "SOPS Editor"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8080

    # Directories whose files may be opened. ``|`` or os.pathsep separated.
    roots: str = Field(default_factory=_default_roots)

    # Persistent data: cloned repositories and encrypted backups.
    storage: str = "/data"
    backup_keep: int = 10

    # Private age key used by the server, or a per-browser session key.
    age_key_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SOPS_AGE_KEY_FILE", "SOPS_EDITOR_AGE_KEY_FILE"),
    )

    auth_user: str = "sops"
    auth_password: SecretStr | None = None
    # basic (default) | header (identity supplied by an OIDC-aware reverse proxy) | none
    auth_mode: str = Field(default="basic", pattern="^(basic|header|none)$")
    auth_header: str = Field(default="X-Forwarded-User", max_length=64)
    # Comma separated IPs/CIDRs of the proxies whose header is trusted.
    auth_trusted_proxies: str = ""

    max_file_bytes: int = 5 * 1024 * 1024
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = False
    trust_proxy: bool = False

    sops_bin: str = "sops"
    age_keygen_bin: str = "age-keygen"
    request_timeout: float = 60.0

    allow_git_clone: bool = True
    # Clones under /data/repos are removed after this many days without use.
    # 0 disables automatic cleanup; manual deletion remains available.
    git_clone_ttl_days: int = Field(default=7, ge=0)
    git_token: SecretStr | None = None
    git_username: str = "oauth2"
    # Empty = any host. Comma/space separated host names.
    git_allowed_hosts: str = ""
    git_author_name: str = "SOPS Editor"
    git_author_email: str = "sops-editor@localhost"
    # Optional host ~/.gitconfig mounted read-only for author/committer prefill.
    git_config_global: str = "/gitconfig"

    # SSH git. ``/ssh`` is the read-only mount of the host's ~/.ssh.
    # A private key is auto-detected there unless git_ssh_key is set.
    git_ssh_dir: str = "/ssh"
    git_ssh_key: str = ""
    git_ssh_known_hosts: str = ""
    git_ssh_strict_host_key_checking: str = Field(
        default="accept-new", pattern="^(yes|no|accept-new)$"
    )

    # Master key for the encrypted, write-only server key store. Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    key_store_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SOPS_EDITOR_KEYSTORE_KEY", "SOPS_EDITOR_KEYSTORE_SECRET"
        ),
    )

    @property
    def roots_list(self) -> list[Path]:
        raw = self.roots.strip()
        if not raw:
            return []
        if "|" in raw:
            parts = raw.split("|")
        else:
            parts = raw.split(os.pathsep)
        result: list[Path] = []
        for part in parts:
            part = part.strip()
            if part:
                result.append(Path(part).expanduser())
        return result

    @property
    def storage_path(self) -> Path:
        return Path(self.storage).expanduser()

    @property
    def uploads_path(self) -> Path:
        return self.storage_path / "uploads"

    @property
    def auth_enabled(self) -> bool:
        if self.auth_mode == "none":
            return False
        if self.auth_mode == "header":
            return True
        return bool(self.auth_password and self.auth_password.get_secret_value())

    @property
    def auth_trusted_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for item in self.auth_trusted_proxies.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError as exc:
                raise ValueError(f"Некорректный SOPS_EDITOR_AUTH_TRUSTED_PROXIES: {item}") from exc
        return networks

    @property
    def effective_age_key_file(self) -> Path | None:
        candidates: list[Path] = []
        if self.age_key_file:
            candidates.append(Path(self.age_key_file).expanduser())
        candidates.append(Path("/keys/age.txt"))
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @property
    def git_allowed_hosts_list(self) -> list[str]:
        return [h.strip().lower() for h in self.git_allowed_hosts.replace(",", " ").split() if h.strip()]

    def ensure_storage(self) -> Path:
        path = self.storage_path
        path.mkdir(parents=True, exist_ok=True)
        return path
