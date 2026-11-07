from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".tools"


def _find_tool(name: str, windows_name: str) -> str | None:
    if sys.platform == "win32":
        candidate = TOOLS / windows_name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


SOPS_BIN = _find_tool("sops", "sops.exe")
AGE_KEYGEN_BIN = _find_tool("age-keygen", "age/age-keygen.exe")


def require_sops() -> None:
    if not SOPS_BIN or not AGE_KEYGEN_BIN:
        pytest.skip("sops/age-keygen не найдены")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kwargs)


def make_age_key(path: Path) -> tuple[str, str]:
    result = run([AGE_KEYGEN_BIN, "-o", str(path)])  # type: ignore[list-item]
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    text = path.read_text(encoding="utf-8")
    public = ""
    for line in text.splitlines():
        if line.startswith("# public key:"):
            public = line.split(":", 1)[1].strip()
    secret = "\n".join(line for line in text.splitlines() if line.startswith("AGE-SECRET-KEY-")) + "\n"
    assert public and secret
    return public, secret


def encrypt(path: Path, key_file: Path, config: Path) -> None:
    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(key_file)
    result = run(
        [SOPS_BIN, "--config", str(config), "--encrypt", "--in-place", str(path)],  # type: ignore[list-item]
        env=env,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


PLAINTEXT = """# demo secret
apiVersion: v1
kind: Secret
metadata:
  name: demo
  namespace: sops-editor
type: Opaque
stringData:
  password: hunter2
  username: admin
"""


@pytest.fixture()
def workspace(tmp_path: Path) -> dict:
    require_sops()
    root = tmp_path / "workspace"
    secrets = root / "secrets"
    secrets.mkdir(parents=True)
    (tmp_path / "repos").mkdir()
    (tmp_path / "storage").mkdir()
    key_file = tmp_path / "age.txt"
    public, secret = make_age_key(key_file)
    config = root / ".sops.yaml"
    config.write_text(
        "creation_rules:\n"
        "  - path_regex: secrets[/\\\\].*\\.enc\\.yaml$\n"
        "    encrypted_regex: ^(data|stringData)$\n"
        f"    age: {public}\n",
        encoding="utf-8",
    )
    target = secrets / "app.enc.yaml"
    target.write_text(PLAINTEXT, encoding="utf-8")
    encrypt(target, key_file, config)

    settings = Settings(
        roots=f"{root}|{tmp_path / 'repos'}",
        storage=str(tmp_path / "storage"),
        age_key_file=str(key_file),
        sops_bin=str(SOPS_BIN),
        age_keygen_bin=str(AGE_KEYGEN_BIN),
        allow_git_clone=False,
        backup_keep=3,
        request_timeout=30,
    )
    app = create_app(settings)
    return {
        "tmp": tmp_path,
        "root": root,
        "target": target,
        "key_file": key_file,
        "secret": secret,
        "public": public,
        "settings": settings,
        "app": app,
    }


@pytest.fixture()
def client(workspace: dict):
    from fastapi.testclient import TestClient

    with TestClient(workspace["app"]) as test_client:
        yield test_client


def decrypt(path: Path, key_file: Path) -> str:
    env = os.environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(key_file)
    result = run([SOPS_BIN, "decrypt", "--input-type", "yaml", "--output-type", "yaml", str(path)], env=env)  # type: ignore[list-item]
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout.decode("utf-8")
