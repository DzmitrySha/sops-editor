from __future__ import annotations

import shutil
import subprocess


def _init_repo(workspace) -> None:
    root = workspace["root"]
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )


def test_git_diff_decrypts_head_and_working_copy(workspace, client):
    _init_repo(workspace)
    opened = client.get("/api/file", params={"path": str(workspace["target"])}).json()
    edited = opened["content"].replace("hunter2", "s3cr3t-new")
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
    diff = client.get("/api/git/diff", params={"path": str(workspace["target"])})
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["head"]["content"] == opened["content"]
    assert body["current"]["content"] == edited
    assert "ENC[" not in body["head"]["content"]
    assert "ENC[" not in body["current"]["content"]
    assert body["current"]["encrypted_file"] is True
    assert body["head"]["encrypted_file"] is True
    assert body["head"]["commit"]["hash"]


def test_git_diff_untracked_file_has_no_head(workspace, client):
    _init_repo(workspace)
    fresh = workspace["root"] / "secrets" / "fresh.enc.yaml"
    shutil.copy(workspace["target"], fresh)  # created after the commit -> untracked
    result = client.get("/api/git/diff", params={"path": str(fresh)})
    assert result.status_code == 200, result.text
    assert result.json()["head"] is None


def test_git_diff_outside_repo(workspace, client):
    result = client.get("/api/git/diff", params={"path": str(workspace["target"])})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "not_a_repo"
