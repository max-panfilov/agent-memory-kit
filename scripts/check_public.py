#!/usr/bin/env python3
"""Bounded public-safety checks for the candidate repository tree."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gho_|github_pat_)[A-Za-z0-9_]{16,}"),
    "openai-token": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "private-host": re.compile(r"(?:82\.146\.32\.2|188\.120\.255\.174|177\.203\.49\.206|internal\.pd|ci\.pd|ratio\.bz)"),
    "private-path": re.compile(r"(?:/root/|/home/coder/|/var/lib/studio-platform)"),
    "private-repository": re.compile(r"max-panfilov/studio-platform"),
}


def main() -> int:
    violations = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or "backups" in path.parts or path.is_dir():
            continue
        if path.is_symlink():
            violations.append(f"{path.relative_to(ROOT)}: symlink is not allowed")
            continue
        if path.stat().st_size > 2_000_000:
            violations.append(f"{path.relative_to(ROOT)}: file exceeds 2 MB review bound")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(ROOT)}: {label}")
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("public-safety: no forbidden markers or symlinks found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
