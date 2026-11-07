from __future__ import annotations

from conftest import decrypt, make_age_key


def open_file(client, path) -> dict:
    response = client.get("/api/file", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def test_updatekeys_is_noop_when_recipients_match(workspace, client):
    opened = open_file(client, workspace["target"])
    result = client.post(
        "/api/keys/rotate",
        json={"path": str(workspace["target"]), "mode": "updatekeys"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed"] is False
    assert body["sha256"] == opened["sha256"]
    assert body["verified"] == "plaintext"
    assert body["backup"]["id"]


def test_updatekeys_rekeys_to_new_recipient(workspace, client, tmp_path):
    new_key = tmp_path / "new-age.txt"
    public, _secret = make_age_key(new_key)
    config = workspace["root"] / ".sops.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(workspace["public"], public),
        encoding="utf-8",
    )
    result = client.post(
        "/api/keys/rotate",
        json={"path": str(workspace["target"]), "mode": "updatekeys"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed"] is True
    assert body["encrypted"]["age_recipients"] == [public]
    assert any(line.startswith("+++ ") for line in body["output"])
    plaintext = decrypt(workspace["target"], new_key)
    assert "hunter2" in plaintext


def test_rotate_generates_new_data_key(workspace, client):
    before = open_file(client, workspace["target"])
    result = client.post(
        "/api/keys/rotate",
        json={"path": str(workspace["target"]), "mode": "rotate"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed"] is True
    assert body["verified"] == "plaintext"
    after = open_file(client, workspace["target"])
    assert after["content"] == before["content"]
    assert after["sha256"] != before["sha256"]
    assert "ENC[AES256_GCM" in workspace["target"].read_text(encoding="utf-8")


def test_rotate_rejects_plaintext(workspace, client):
    plain = workspace["root"] / "secrets" / "plain.yaml"
    plain.write_text("token: x\n", encoding="utf-8")
    result = client.post("/api/keys/rotate", json={"path": str(plain), "mode": "rotate"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "not_encrypted"
