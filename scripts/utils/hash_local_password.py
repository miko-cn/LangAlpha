#!/usr/bin/env python3
"""Print a PBKDF2 hash for LOCAL_AUTH_PASSWORD_HASH.

Usage:

  uv run python scripts/utils/hash_local_password.py
  uv run python scripts/utils/hash_local_password.py --password '…'
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.server.auth.local import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hash a password for HOST_MODE=local (LOCAL_AUTH_PASSWORD_HASH)."
    )
    parser.add_argument(
        "--password",
        help="Password to hash. Omit to be prompted (no echo).",
    )
    args = parser.parse_args()

    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 1
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1

    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
