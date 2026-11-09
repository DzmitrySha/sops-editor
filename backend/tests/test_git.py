from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout.decode("utf-8", errors="replace").strip()


def test_git_status_commit_and_push_rejection(client, workspace):
    root = workspace["root"]
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "user.email", "tester@example.com")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")

    opened = client.get("/api/file", params={"path": str(workspace["target"])}).json()
    assert opened["git"]["branch"] == "main"
    assert opened["git"]["dirty"] is False

    edited = opened["content"].replace("hunter2", "hunter4")
    saved = client.post(
        "/api/file",
        json={
            "path": str(workspace["target"]),
            "content": edited,
            "format": "yaml",
            "expected_sha256": opened["sha256"],
        },
    )
    assert saved.status_code == 200, saved.text
    status = client.get("/api/git", params={"path": str(workspace["target"])}).json()
    assert status["git"]["dirty"] is True

    response = client.post(
        "/api/git/commit",
        json={"path": str(workspace["target"]), "message": "rotate demo secret", "push": False},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["committed"] is True
    assert result["hash"]
    assert result["git"]["dirty"] is False
    assert "rotate demo secret" in git(root, "log", "-1", "--format=%s")

    second = client.post(
        "/api/git/commit",
        json={"path": str(workspace["target"]), "message": "nothing", "push": False},
    )
    assert second.status_code == 200
    assert second.json()["committed"] is False
    assert second.json()["reason"] == "no_changes"


def test_commit_uses_user_identity_and_adds_tool_trailer(client, workspace):
    root = workspace["root"]
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "user.email", "tester@example.com")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")

    opened = client.get("/api/file", params={"path": str(workspace["target"])}).json()
    edited = opened["content"].replace("hunter2", "hunter5")
    saved = client.post(
        "/api/file",
        json={
            "path": str(workspace["target"]),
            "content": edited,
            "format": "yaml",
            "expected_sha256": opened["sha256"],
        },
    )
    assert saved.status_code == 200, saved.text

    response = client.post(
        "/api/git/commit",
        json={
            "path": str(workspace["target"]),
            "message": "rotate demo secret",
            "push": False,
            "author_name": "Дмитрий Шаповалов",
            "author_email": "d.shapovalov@example.net",
        },
    )
    assert response.status_code == 200, response.text
    raw = git(
        root,
        "log",
        "-1",
        "--format=%an%x00%ae%x00%cn%x00%ce%x00%B",
    )
    parts = raw.split("\x00")
    assert parts[0] == "Дмитрий Шаповалов"
    assert parts[1] == "d.shapovalov@example.net"
    assert parts[2] == "Дмитрий Шаповалов"
    assert parts[3] == "d.shapovalov@example.net"
    assert parts[4].startswith("rotate demo secret")
    assert "Made-with: SOPS Editor" in parts[4]


def test_git_clone_is_disabled_by_default(client):
    response = client.post(
        "/api/git/clone",
        json={"url": "https://example.invalid/group/repo.git"},
    )
    assert response.status_code == 400
    assert "отключено" in response.text


def test_clone_target_is_browsable_but_other_paths_are_not(workspace, tmp_path):
    from app.config import Settings
    from app.main import create_app

    storage = tmp_path / "clone-storage"
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(storage),
        age_key_file=str(workspace["key_file"]),
        sops_bin=workspace["settings"].sops_bin,
        age_keygen_bin=workspace["settings"].age_keygen_bin,
        allow_git_clone=True,
    )
    app = create_app(settings)
    repo = storage / "repos" / "demo"
    repo.mkdir(parents=True)
    (repo / "secret.yaml").write_text("a: b\n", encoding="utf-8")

    with TestClient(app) as test_client:
        browsed = test_client.get("/api/browse", params={"path": str(repo)})
        assert browsed.status_code == 200, browsed.text
        assert "secret.yaml" in {entry["name"] for entry in browsed.json()["entries"]}

        outside = test_client.get("/api/browse", params={"path": str(tmp_path)})
        assert outside.status_code == 403
