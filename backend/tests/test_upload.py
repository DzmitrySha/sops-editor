from __future__ import annotations

from pathlib import Path

from conftest import decrypt


def test_upload_edit_download_round_trip(client, workspace):
    raw = workspace["target"].read_bytes()
    response = client.post(
        "/api/upload",
        params={"name": "local-app.enc.yaml"},
        content=raw,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    uploaded = response.json()
    path = uploaded["path"]
    assert "/uploads/" in path.replace("\\", "/")
    assert Path(path).is_file()
    assert uploaded["name"] == "local-app.enc.yaml"
    assert "sops_upload" in response.cookies or client.cookies.get("sops_upload")

    opened = client.get("/api/file", params={"path": path})
    assert opened.status_code == 200, opened.text
    payload = opened.json()
    assert "password: hunter2" in payload["content"]

    edited = payload["content"].replace("hunter2", "uploaded-secret")
    saved = client.post(
        "/api/file",
        json={
            "path": path,
            "content": edited,
            "format": "yaml",
            "expected_sha256": payload["sha256"],
        },
    )
    assert saved.status_code == 200, saved.text

    download = client.get("/api/download", params={"path": path})
    assert download.status_code == 200
    assert b"ENC[AES256_GCM" in download.content
    assert "uploaded-secret" not in download.content.decode("utf-8", errors="ignore")
    assert "uploaded-secret" in decrypt(Path(path), workspace["key_file"])


def test_upload_accepts_plain_file_and_opens_it(client, workspace):
    response = client.post(
        "/api/upload",
        params={"name": "plain.yaml"},
        content=b"a: b\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    payload = opened.json()
    assert payload["encrypted_file"] is False
    assert payload["content"] == "a: b\n"
    assert payload["encrypted"]["key_types"] == []

    saved = client.post(
        "/api/file",
        json={
            "path": data["path"],
            "content": "a: c\n",
            "format": "yaml",
            "expected_sha256": payload["sha256"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["encrypted_file"] is False
    assert client.get("/api/file", params={"path": data["path"]}).json()["content"] == "a: c\n"

    encrypted = client.post("/api/encrypt", json={"path": data["path"]})
    assert encrypted.status_code == 200, encrypted.text
    target = Path(encrypted.json()["path"])
    assert target.name == "plain.enc.yaml"
    reopened = client.get("/api/file", params={"path": str(target)})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["encrypted_file"] is True
    assert reopened.json()["content"] == "a: c\n"


def test_download_is_limited_to_uploads(client, workspace):
    response = client.get("/api/download", params={"path": str(workspace["target"])})
    assert response.status_code == 403
