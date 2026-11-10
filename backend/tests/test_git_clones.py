from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import BadRequest
from app.git_service import GitService


def service(tmp_path: Path) -> tuple[GitService, Path]:
    settings = Settings(
        roots=str(tmp_path),
        storage=str(tmp_path / "storage"),
        allow_git_clone=True,
        git_ssh_dir=str(tmp_path / "no-ssh"),
    )
    return GitService(settings), tmp_path / "storage" / "repos"


def make_clone(root: Path, name: str, age_days: float = 0) -> Path:
    target = root / name
    (target / ".git").mkdir(parents=True)
    (target / "secret.yaml").write_text("secret: value\n", encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(target, (old, old))
    return target


def test_list_and_delete_clone(tmp_path: Path):
    git, repos = service(tmp_path)
    target = make_clone(repos, "demo", age_days=2)

    listed = git.list_clones()
    assert [item["name"] for item in listed] == ["demo"]
    assert listed[0]["path"] == str(target)
    assert listed[0]["age_days"] >= 1.9

    git.delete_clone("demo")
    assert not target.exists()
    assert git.list_clones() == []


def test_delete_rejects_traversal_and_missing(tmp_path: Path):
    git, _repos = service(tmp_path)
    with pytest.raises(BadRequest):
        git.delete_clone("../outside")
    with pytest.raises(BadRequest):
        git.delete_clone("missing")


def test_cleanup_removes_only_old_clones(tmp_path: Path):
    git, repos = service(tmp_path)
    old = make_clone(repos, "old", age_days=10)
    fresh = make_clone(repos, "fresh", age_days=0)

    removed = git.cleanup_clones(7)
    assert removed == ["old"]
    assert not old.exists()
    assert fresh.exists()

    assert git.cleanup_clones(0) == []
