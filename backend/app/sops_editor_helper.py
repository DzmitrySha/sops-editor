#!/usr/bin/env python3
"""One-shot editor used by ``sops edit``.

SOPS decrypts the file, runs this helper, then validates and re-encrypts it.
The helper copies the browser-submitted payload once. If SOPS reopens the
editor (for example after invalid YAML), the helper exits non-zero instead of
looping forever.
"""

from __future__ import annotations

import os
import shutil
import sys


def main() -> int:
    payload = os.environ.get("SOPS_EDITOR_PAYLOAD")
    counter = os.environ.get("SOPS_EDITOR_COUNTER")
    if not payload or not counter:
        sys.stderr.write("SOPS Editor helper: missing environment\n")
        return 2
    if len(sys.argv) < 2:
        sys.stderr.write("SOPS Editor helper: missing target file\n")
        return 2
    if os.path.exists(counter):
        sys.stderr.write("SOPS Editor helper: refusing to reopen the editor\n")
        return 3
    try:
        with open(counter, "w", encoding="ascii") as handle:
            handle.write("1")
        shutil.copyfile(payload, sys.argv[1])
    except OSError as exc:
        sys.stderr.write(f"SOPS Editor helper: {exc}\n")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
