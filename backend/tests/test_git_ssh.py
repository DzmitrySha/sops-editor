from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import BadRequest
from app.git_service import GitService


def git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def make_repo(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "user.email", "tester@example.com")
    (root / "infra").mkdir()
    (root / "other").mkdir()
    (root / "infra" / "secret.yaml").write_text("secret: value\n", encoding="utf-8")
    (root / "other" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (root / ".sops.yaml").write_text("creation_rules: []\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")


def test_ssh_url_detection_and_key_copy(tmp_path: Path):
    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    key = ssh_dir / "id_ed25519"
    key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
    settings = Settings(
        roots=str(tmp_path),
        storage=str(tmp_path / "storage"),
        git_ssh_dir=str(ssh_dir),
    )
    service = GitService(settings)

    assert service._is_ssh_url("git@gitlab.example.com:group/repo.git")
    assert service._is_ssh_url("ssh://git@gitlab.example.com:2222/group/repo.git")
    assert not service._is_ssh_url("https://gitlab.example.com/group/repo.git")

    env, temp_dir = service._ssh_auth_env()
    try:
        command = shlex.split(env["GIT_SSH_COMMAND"])
        key_index = command.index("-i")
        copied = Path(command[key_index + 1])
        assert copied.is_file()
        assert copied.read_bytes() == key.read_bytes()
        if os.name != "nt":
            assert stat.S_IMODE(copied.stat().st_mode) == 0o600
        assert "IdentitiesOnly=yes" in command
        known = next(item.split("=", 1)[1] for item in command if item.startswith("UserKnownHostsFile="))
        assert Path(known).is_file()
        assert service.ssh_status() == {"ready": True, "source": "key:id_ed25519"}
    finally:
        assert temp_dir is not None
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_default_identity_from_mounted_gitconfig(tmp_path: Path):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Alice Example\n\temail = alice@example.net\n",
        encoding="utf-8",
    )
    settings = Settings(
        roots=str(tmp_path),
        storage=str(tmp_path / "storage"),
        git_config_global=str(config),
    )
    service = GitService(settings)
    assert service.default_identity() == {
        "name": "Alice Example",
        "email": "alice@example.net",
        "source": "gitconfig",
    }


def test_explicit_missing_ssh_key_is_reported(tmp_path: Path):
    settings = Settings(
        roots=str(tmp_path),
        storage=str(tmp_path / "storage"),
        git_ssh_key=str(tmp_path / "missing"),
    )
    service = GitService(settings)
    with pytest.raises(BadRequest, match="SSH-ключ"):
        service._ssh_auth_env()


def test_sparse_checkout_reuses_auth_environment(tmp_path: Path, monkeypatch):
    settings = Settings(
        roots=str(tmp_path),
        storage=str(tmp_path / "storage"),
        allow_git_clone=True,
        git_ssh_dir=str(tmp_path / "no-ssh"),
    )
    service = GitService(settings)
    auth = {"GIT_SSH_COMMAND": "ssh -i /tmp/key"}
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(args, *, cwd, timeout=30.0, extra_env=None):
        calls.append((args, extra_env))
        if "clone" in args:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if "cat-file" in args:
            return subprocess.CompletedProcess(args, 0, b"tree\n", b"")
        if "sparse-checkout" in args:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, b"main\n", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(service, "_auth_env", lambda url: (auth, None))
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service, "repo_info", lambda path: {})

    service.clone("git@gitlab.example.com:group/repo.git", None, "demo", "infra/k8s")

    sparse_calls = [(args, env) for args, env in calls if "sparse-checkout" in args]
    assert sparse_calls
    assert sparse_calls[0][1] == auth


def test_sparse_clone_checks_out_only_subdir(tmp_path: Path):
    source = tmp_path / "source"
    make_repo(source)
    storage = tmp_path / "storage"
    settings = Settings(
        roots=str(source),
        storage=str(storage),
        allow_git_clone=True,
        git_ssh_dir=str(tmp_path / "no-ssh"),
    )
    service = GitService(settings)

    result = service.clone(source.as_uri(), None, "demo", "infra")
    target = Path(result["path"])
    repo = target.parent
    assert target == storage / "repos" / "demo" / "infra"
    assert (target / "secret.yaml").is_file()
    assert (repo / ".sops.yaml").is_file()
    assert not (repo / "other").exists()
    assert result["subdir"] == "infra"
    assert result["git"]["branch"] == "main"

    with pytest.raises(BadRequest, match="Некорректная папка"):
        service.clone(source.as_uri(), None, "invalid", "../infra")

    with pytest.raises(BadRequest, match="не найдена"):
        service.clone(source.as_uri(), None, "missing", "nope")
    assert not (storage / "repos" / "missing").exists()
