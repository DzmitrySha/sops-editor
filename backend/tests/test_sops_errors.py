from __future__ import annotations

import subprocess

import pytest

from app.errors import KeyRequired
from app.sops_service import SopsRunner


@pytest.mark.parametrize(
    "stderr",
    [
        b"age: no identity matched any of the recipients",
        b"age: identity did not match any of the recipients: incorrect identity for recipient block",
        b"age: incorrect identity for recipient block",
    ],
)
def test_identity_mismatch_maps_to_key_required(stderr: bytes):
    runner = SopsRunner()
    result = subprocess.CompletedProcess(args=["sops"], returncode=128, stdout=b"", stderr=stderr)

    with pytest.raises(KeyRequired):
        runner._raise_for_result(result, "decrypt")
