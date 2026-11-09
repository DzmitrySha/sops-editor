from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.config import Settings
from app.main import create_app


def open_file(client, path: Path) -> dict:
    response = client.get("/api/file", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def test_open_decrypts_and_reports_metadata(client, workspace):
    payload = open_file(client, workspace["target"])
    assert "password: hunter2" in payload["content"]
    assert "ENC[" not in payload["content"]
    assert payload["format"] == "yaml"
    assert payload["tree"] is True
    assert payload["encrypted"]["key_types"] == ["age"]
    assert workspace["public"] in payload["encrypted"]["age_recipients"]
    assert payload["encrypted"]["encrypted_regex"] == "^(data|stringData)$"
    assert payload["key_source"] == "server_file"
    assert payload["sha256"]


def test_save_reencrypts_preserving_policy_and_makes_backup(client, workspace):
    opened = open_file(client, workspace["target"])
    edited = opened["content"].replace("hunter2", "hunter3") + "    token: abc123\nnewfield: plain\n"
    response = client.post(
        "/api/file",
        json={
            "path": str(workspace["target"]),
            "content": edited,
            "format": "yaml",
            "expected_sha256": opened["sha256"],
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["saved"] is True
    assert saved["sha256"] != opened["sha256"]
    assert "hunter3" in saved["content"]

    encrypted = workspace["target"].read_text(encoding="utf-8")
    assert "stringData:" in encrypted
    assert "password: ENC[AES256_GCM" in encrypted
    assert "newfield: plain" in encrypted
    assert "encrypted_regex: ^(data|stringData)$" in encrypted
    assert workspace["public"] in encrypted

    decrypted = yaml.safe_load(decrypted_text(workspace))
    assert decrypted["stringData"]["password"] == "hunter3"
    assert decrypted["stringData"]["token"] == "abc123"
    assert decrypted["newfield"] == "plain"
    assert decrypted["metadata"]["name"] == "demo"

    backups = client.get("/api/backups", params={"path": str(workspace["target"])})
    assert backups.status_code == 200
    items = backups.json()["backups"]
    assert len(items) >= 1
    download = client.get(
        f"/api/backups/{items[0]['id']}", params={"path": str(workspace["target"])}
    )
    assert download.status_code == 200
    assert download.content.startswith(b"#ENC") or b"sops:" in download.content


def decrypted_text(workspace) -> str:
    from conftest import decrypt

    return decrypt(workspace["target"], workspace["key_file"])


def test_stale_hash_is_rejected(client, workspace):
    opened = open_file(client, workspace["target"])
    workspace["target"].write_bytes(workspace["target"].read_bytes() + b"\n")
    response = client.post(
        "/api/file",
        json={
            "path": str(workspace["target"]),
            "content": opened["content"],
            "format": "yaml",
            "expected_sha256": opened["sha256"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_path_traversal_is_rejected(client, workspace):
    response = client.get("/api/file", params={"path": "../../etc/passwd"})
    assert response.status_code in (403, 404)
    response = client.get("/api/browse", params={"path": "/etc"})
    assert response.status_code in (403, 404)


def test_invalid_yaml_is_rejected_before_sops(client, workspace):
    opened = open_file(client, workspace["target"])
    response = client.post(
        "/api/file",
        json={
            "path": str(workspace["target"]),
            "content": "a: [\n",
            "format": "yaml",
            "expected_sha256": opened["sha256"],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_wrong_key_reports_key_required(workspace):
    from fastapi.testclient import TestClient

    wrong = workspace["tmp"] / "wrong-age.txt"
    from conftest import make_age_key

    make_age_key(wrong)
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(workspace["tmp"] / "storage2"),
        age_key_file=str(wrong),
        sops_bin=str(workspace["settings"].sops_bin),
        age_keygen_bin=str(workspace["settings"].age_keygen_bin),
        allow_git_clone=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/file", params={"path": str(workspace["target"])})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "key_required"


def test_session_key_can_open_and_be_forgotten(workspace, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: workspace["tmp"] / "nohome"))
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(workspace["tmp"] / "storage3"),
        age_key_file=None,
        sops_bin=str(workspace["settings"].sops_bin),
        age_keygen_bin=str(workspace["settings"].age_keygen_bin),
        allow_git_clone=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/file", params={"path": str(workspace["target"])})
        assert response.status_code == 422
        response = client.post("/api/session/key", json={"key": workspace["secret"]})
        assert response.status_code == 200, response.text
        assert response.json()["recipients"] == [workspace["public"]]
        opened = client.get("/api/file", params={"path": str(workspace["target"])})
        assert opened.status_code == 200
        assert opened.json()["key_source"] == "session"
        response = client.delete("/api/session/key")
        assert response.status_code == 200
        response = client.get("/api/file", params={"path": str(workspace["target"])})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "key_required"


def test_basic_auth(workspace):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    settings = workspace["settings"].model_copy(
        update={"auth_user": "alice", "auth_password": SecretStr("secret")}
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/status", auth=("alice", "secret"))
        assert response.status_code == 200
        assert response.json()["auth_enabled"] is True


def test_header_auth_mode(workspace):
    from fastapi.testclient import TestClient

    settings = workspace["settings"].model_copy(
        update={"auth_mode": "header", "auth_header": "X-Forwarded-User"}
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert "www-authenticate" not in client.get("/api/status").headers
        assert client.get("/api/health").status_code == 200
        ok = client.get("/api/status", headers={"X-Forwarded-User": "alice"})
        assert ok.status_code == 200
        assert ok.json()["user"] == "alice"
        assert ok.json()["auth_mode"] == "header"
        assert ok.json()["auth_enabled"] is True


def test_header_auth_rejects_untrusted_peer(workspace):
    from fastapi.testclient import TestClient

    settings = workspace["settings"].model_copy(
        update={
            "auth_mode": "header",
            "auth_header": "X-Forwarded-User",
            "auth_trusted_proxies": "10.0.0.0/8",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # The TestClient peer host ("testclient") is not an IP, so it is untrusted.
        response = client.get("/api/status", headers={"X-Forwarded-User": "alice"})
        assert response.status_code == 401


def test_json_and_dotenv_round_trip(workspace):
    root = workspace["root"]
    json_plain = root / "secrets" / "app.json"
    json_plain.write_text(json.dumps({"token": "one", "count": 2}) + "\n", encoding="utf-8")
    # The repo root now has a deployment .sops.yaml for infra/k8s. Use a local
    # config so the test does not depend on the caller's cwd.
    json_config = workspace["tmp"] / "json.sops.yaml"
    json_config.write_text(
        "creation_rules:\n"
        "  - path_regex: .*\\.json$\n"
        f"    age: {workspace['public']}\n",
        encoding="utf-8",
    )
    env = __import__("os").environ.copy()
    env["SOPS_AGE_KEY_FILE"] = str(workspace["key_file"])
    import subprocess

    result = subprocess.run(
        [
            workspace["settings"].sops_bin,
            "--config",
            str(json_config),
            "--age",
            workspace["public"],
            "--encrypt",
            "--input-type",
            "json",
            "--output-type",
            "json",
            "--in-place",
            str(json_plain),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr.decode()
    from fastapi.testclient import TestClient

    with TestClient(workspace["app"]) as client:
        opened = open_file(client, json_plain)
        assert opened["format"] == "json"
        edited = opened["content"].replace('"one"', '"two"')
        response = client.post(
            "/api/file",
            json={
                "path": str(json_plain),
                "content": edited,
                "format": "json",
                "expected_sha256": opened["sha256"],
            },
        )
        assert response.status_code == 200, response.text
        assert json.loads(response.json()["content"])["token"] == "two"


def test_key_required_error_reports_required_recipients(client, workspace):
    from conftest import PLAINTEXT, encrypt, make_age_key

    other_key = workspace["tmp"] / "foreign-age.txt"
    other_public, _ = make_age_key(other_key)
    other_config = workspace["tmp"] / "foreign.sops.yaml"
    other_config.write_text(
        "creation_rules:\n"
        "  - path_regex: .*\\.enc\\.yaml$\n"
        f"    age: {other_public}\n",
        encoding="utf-8",
    )
    foreign = workspace["root"] / "secrets" / "foreign.enc.yaml"
    foreign.write_text(PLAINTEXT, encoding="utf-8")
    encrypt(foreign, other_key, other_config)

    response = client.get("/api/file", params={"path": str(foreign)})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "key_required"
    required = error["details"]["encrypted"]
    assert required["key_types"] == ["age"]
    assert other_public in required["age_recipients"]
