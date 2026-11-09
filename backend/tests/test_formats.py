from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import BadRequest
from app.formats import detect_format, semantic_equal, summarize_sops_metadata, validate_text


def test_detects_common_formats(tmp_path: Path):
    assert detect_format(tmp_path / "a.yaml").name == "yaml"
    assert detect_format(tmp_path / "a.yml").name == "yaml"
    assert detect_format(tmp_path / "a.json").name == "json"
    assert detect_format(tmp_path / ".env").name == "dotenv"
    assert detect_format(tmp_path / ".env.production").name == "dotenv"
    assert detect_format(tmp_path / "a.ini").name == "ini"


def test_detects_unknown_extension_by_content(tmp_path: Path):
    assert detect_format(tmp_path / "noext", '{"a": 1}').name == "json"
    assert detect_format(tmp_path / "noext", "A=1\nB=two\n").name == "dotenv"
    assert detect_format(tmp_path / "noext", "[main]\na=1\n").name == "ini"
    assert detect_format(tmp_path / "noext", "a: 1\n").name == "yaml"


def test_invalid_yaml_has_line_info():
    with pytest.raises(BadRequest) as exc:
        validate_text("a: [\n", "yaml")
    assert "YAML" in exc.value.message


def test_dotenv_duplicates_rejected():
    with pytest.raises(BadRequest):
        validate_text("A=1\nA=2\n", "dotenv")


def test_semantic_equal_ignores_comments():
    assert semantic_equal("# old\na: 1\n", "a: 1\n# new\n", "yaml")


def test_metadata_summary_reads_age_and_policy():
    encrypted = """apiVersion: v1
kind: Secret
data:
  password: ENC[AES256_GCM,data:xx,iv:yy,tag:zz,type:str]
sops:
  age:
    - recipient: age1abc
      enc: |
        -----BEGIN AGE ENCRYPTED FILE-----
        xxx
        -----END AGE ENCRYPTED FILE-----
  encrypted_regex: ^(data|stringData)$
  lastmodified: "2026-01-01T00:00:00Z"
  version: 3.13.3
"""
    summary = summarize_sops_metadata(encrypted, "yaml")
    assert summary["key_types"] == ["age"]
    assert summary["age_recipients"] == ["age1abc"]
    assert summary["encrypted_regex"] == "^(data|stringData)$"
    assert summary["version"] == "3.13.3"
