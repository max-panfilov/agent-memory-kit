"""Resolve repository resources from source checkouts and packaged containers."""
from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("AGENT_MEMORY_ROOT")
    if configured:
        return Path(configured).resolve()
    candidates = [Path(__file__).resolve().parents[2], Path.cwd().resolve()]
    for candidate in candidates:
        if (candidate / "migrations").is_dir() and (candidate / "content").is_dir():
            return candidate
    raise RuntimeError("Set AGENT_MEMORY_ROOT to the directory containing migrations/ and content/")


ROOT = project_root()
