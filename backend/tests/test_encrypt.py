from __future__ import annotations

from pathlib import Path


def test_encrypt_upload_round_trip(client, workspace):
    plain = b"db_password: hunter2\napi_key: abc123\n"
    response = client.post(
        "/api/encrypt/upload",
        params={"name": "app.yaml", "recipients": workspace["public"]},
        content=plain,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recipients"] == [workspace["public"]]
    target = Path(data["path"])
    assert target.is_file()
    assert "ENC[AES256_GCM" in target.read_text(encoding="utf-8")

    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["content"] == plain.decode()
    assert workspace["public"] in opened.json()["encrypted"]["age_recipients"]

    download = client.get("/api/download", params={"path": data["path"]})
    assert download.status_code == 200
    assert b"hunter2" not in download.content


def test_encrypt_upload_uses_server_key_by_default(client, workspace):
    response = client.post(
        "/api/encrypt/upload",
        params={"name": "config.json"},
        content=b'{"token": "abc"}',
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recipients"] == [workspace["public"]]
    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["format"] == "json"


def test_encrypt_upload_rejects_already_encrypted(client, workspace):
    raw = workspace["target"].read_bytes()
    response = client.post(
        "/api/encrypt/upload",
        params={"name": "again.yaml", "recipients": workspace["public"]},
        content=raw,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "already_encrypted"


def test_encrypt_server_file_creates_sibling_and_keeps_plaintext(client, workspace):
    plain = workspace["root"] / "secrets" / "plain.yaml"
    plain.write_text("token: abc123\n", encoding="utf-8")
    response = client.post("/api/encrypt", json={"path": str(plain)})
    assert response.status_code == 200, response.text
    data = response.json()
    target = Path(data["path"])
    assert target.name == "plain.enc.yaml"
    assert target.is_file()
    assert plain.read_text(encoding="utf-8") == "token: abc123\n"
    assert "ENC[" in target.read_text(encoding="utf-8")

    opened = client.get("/api/file", params={"path": str(target)})
    assert opened.status_code == 200, opened.text
    assert opened.json()["content"] == "token: abc123\n"


def test_encrypt_server_file_falls_back_to_server_key(client, workspace):
    plain = workspace["root"] / "outside.yaml"
    plain.write_text("a: b\n", encoding="utf-8")
    response = client.post("/api/encrypt", json={"path": str(plain)})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["recipients"] == [workspace["public"]]
    assert Path(data["path"]).name == "outside.enc.yaml"


def test_encrypt_server_file_rejects_already_encrypted(client, workspace):
    response = client.post("/api/encrypt", json={"path": str(workspace["target"])})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_encrypted"


def test_encrypt_server_file_errors_without_any_recipient(client, workspace):
    settings = workspace["settings"]
    original = settings.age_key_file
    settings.age_key_file = None
    try:
        plain = workspace["root"] / "nokey.yaml"
        plain.write_text("a: b\n", encoding="utf-8")
        response = client.post("/api/encrypt", json={"path": str(plain)})
        assert response.status_code == 400, response.text
        assert "age-получател" in response.text
    finally:
        settings.age_key_file = original


def test_encrypt_upload_dotenv_and_ini(client, workspace):
    dotenv = b"APP_ENV=production\nTOKEN=abc123\n"
    response = client.post(
        "/api/encrypt/upload",
        params={"name": ".env", "recipients": workspace["public"]},
        content=dotenv,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["format"] == "dotenv"
    assert opened.json()["content"] == dotenv.decode()
    assert opened.json()["encrypted"]["key_types"] == ["age"]

    ini = b"[api]\ntoken = abc123\n"
    response = client.post(
        "/api/encrypt/upload",
        params={"name": "app.ini", "recipients": workspace["public"]},
        content=ini,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["format"] == "ini"
    assert opened.json()["encrypted"]["key_types"] == ["age"]
    assert workspace["public"] in opened.json()["encrypted"]["age_recipients"]


def test_decrypt_normalizes_crlf(client, workspace):
    plain = b"[api]\ntoken = abc123\n"
    response = client.post(
        "/api/encrypt/upload",
        params={"name": "crlf.ini", "recipients": workspace["public"]},
        content=plain,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    opened = client.get("/api/file", params={"path": response.json()["path"]})
    assert opened.status_code == 200, opened.text
    assert "\r" not in opened.json()["content"]
    assert opened.json()["content"] == plain.decode()


def test_encrypted_dotenv_with_unknown_extension_opens(client, workspace):
    plain = b"TOKEN=abc123\nEMBEDDING_MODEL=x\n"
    response = client.post(
        "/api/encrypt/upload",
        params={"name": ".env_enc.test", "recipients": workspace["public"]},
        content=plain,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    target = Path(data["path"])
    assert target.name == ".env_enc.test"

    opened = client.get("/api/file", params={"path": data["path"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["format"] == "dotenv"
    assert opened.json()["content"] == plain.decode()

    again = client.post(
        "/api/upload",
        params={"name": ".env_enc.test"},
        content=target.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert again.status_code == 200, again.text
    reopened = client.get("/api/file", params={"path": again.json()["path"]})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["content"] == plain.decode()
