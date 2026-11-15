#!/usr/bin/env python3
"""Fetch the published judges at the pinned commit.

The official judge repository has no explicit license file; it is kept out of
this repository and fetched into third_party/ at a pinned commit instead of
being vendored. Record the commit in tests/inventory.json.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PIN = "44fa7ac65f292580c3974a8252f533c4484ebe6b"
REPO = "https://github.com/oscomp/oskernel-testsuits-cooperation.git"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "third_party" / "oskernel-testsuits-cooperation"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    if DEST.exists():
        head = subprocess.check_output(["git", "-C", str(DEST), "rev-parse", "HEAD"], text=True).strip()
        if head != PIN:
            run(["git", "-C", str(DEST), "fetch", "origin", PIN])
            run(["git", "-C", str(DEST), "checkout", PIN])
        else:
            print(f"already at {PIN}")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--filter=blob:none", "--no-checkout", REPO, str(DEST)])
    run(["git", "-C", str(DEST), "fetch", "origin", PIN])
    run(["git", "-C", str(DEST), "checkout", PIN])
    print(f"judges ready at {DEST} ({PIN})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
