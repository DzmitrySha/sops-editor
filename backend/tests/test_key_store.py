from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.errors import BadRequest
from app.key_store import KeyStore
from app.main import create_app
from app.sops_service import SopsRunner


def test_key_store_encrypts_and_cannot_return_plaintext(workspace):
    storage = workspace["tmp"] / "keystore"
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(storage),
        age_key_file=str(workspace["key_file"]),
        sops_bin=workspace["settings"].sops_bin,
        age_keygen_bin=workspace["settings"].age_keygen_bin,
        key_store_secret=SecretStr(Fernet.generate_key().decode()),
    )
    store = KeyStore(settings, SopsRunner(settings.sops_bin, settings.age_keygen_bin))

    assert store.configured is False
    result = store.update(workspace["secret"])
    assert result["configured"] is True
    assert result["recipients"]
    assert "key" not in result
    assert workspace["secret"] not in str(result)

    on_disk = (storage / "server-key" / "keyring.json").read_text(encoding="utf-8")
    assert workspace["secret"] not in on_disk
    assert "AGE-SECRET-KEY" not in on_disk
    assert store.load_inline().strip() == workspace["secret"].strip()
    assert workspace["secret"] not in str(store.status())


def test_key_store_disabled_without_master_key(workspace):
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(workspace["tmp"] / "keystore-disabled"),
        sops_bin=workspace["settings"].sops_bin,
        age_keygen_bin=workspace["settings"].age_keygen_bin,
    )
    store = KeyStore(settings, SopsRunner(settings.sops_bin, settings.age_keygen_bin))
    with pytest.raises(BadRequest, match="KEYSTORE_KEY"):
        store.update(workspace["secret"])


def test_stored_key_is_used_by_the_api_and_never_exposed(workspace):
    storage = workspace["tmp"] / "keystore-api"
    settings = Settings(
        roots=str(workspace["root"]),
        storage=str(storage),
        age_key_file=str(workspace["key_file"]),
        sops_bin=workspace["settings"].sops_bin,
        age_keygen_bin=workspace["settings"].age_keygen_bin,
        allow_git_clone=False,
        key_store_secret=SecretStr(Fernet.generate_key().decode()),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.put("/api/server-key", json={"key": workspace["secret"]})
        assert response.status_code == 200, response.text
        assert workspace["secret"] not in response.text

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["source"] == "server_store"
        assert workspace["secret"] not in session.text

        metadata = client.get("/api/server-key")
        assert metadata.status_code == 200
        assert metadata.json()["configured"] is True
        assert workspace["secret"] not in metadata.text

        opened = client.get("/api/file", params={"path": str(workspace["target"])})
        assert opened.status_code == 200, opened.text
        assert "hunter2" in opened.json()["content"]
