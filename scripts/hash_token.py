#!/usr/bin/env python3
"""Print a token hash without accepting the token in argv or logging it."""
from __future__ import annotations

import hashlib
import os


def main() -> None:
    variable = os.environ.get("TOKEN_ENV_VAR", "DEMO_WRITER_TOKEN")
    token = os.environ.get(variable)
    if not token:
        raise SystemExit(f"Set {variable} in the local environment first")
    print(hashlib.sha256(token.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()
