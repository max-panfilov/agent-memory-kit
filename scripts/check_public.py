#!/usr/bin/env python3
"""Bounded public-safety checks for the candidate repository tree."""
from __future__ import annotations

import re
import ipaddress
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gho_|github_pat_)[A-Za-z0-9_]{16,}"),
    "openai-token": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "private-host": re.compile(r"(?<![\w.-])(?:[a-z0-9-]+\.)+(?:pd|internal|lan)(?![\w.-])", re.I),
    "private-path": re.compile(r"/(?:Users|home)/[a-zA-Z0-9_.-]+|/root/(?!\.cache(?:/|\b))[a-zA-Z0-9_.-]+"),
}
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def findings(text: str) -> list[str]:
    labels = [label for label, pattern in FORBIDDEN.items() if pattern.search(text)]
    for value in IPV4.findall(text.replace(r"\.", ".")):
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            continue
        documentation = any(address in ipaddress.ip_network(network) for network in (
            "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
        ))
        if not address.is_loopback and not address.is_unspecified and not documentation:
            labels.append("non-example-address")
            break
    return labels


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
        for label in findings(text):
            violations.append(f"{path.relative_to(ROOT)}: {label}")
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("public-safety: no forbidden markers or symlinks found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
