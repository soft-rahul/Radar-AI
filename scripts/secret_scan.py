"""Fail if anything that git would publish contains an API key. Never prints a key.

Checks the real values from .env (if present) everywhere, and key-shaped strings everywhere
except uv.lock, whose 64-hex strings are package checksums."""

import re
import subprocess
import sys

from shelfradar.config import load_settings

KEY_SHAPES = [re.compile(r"\b[a-f0-9]{64}\b"), re.compile(r"AIza[0-9A-Za-z_\-]{30,}")]
CHECKSUM_FILES = {"uv.lock"}


def main() -> int:
    s = load_settings()
    secrets = [k for k in (s.serpapi_key, s.gemini_api_key) if k and len(k) >= 16]
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    files = [f for f in out if f]
    problems = []
    for f in files:
        try:
            text = open(f, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if any(k in text for k in secrets):
            problems.append(f"{f}: contains a real key from .env")
        if f not in CHECKSUM_FILES and any(p.search(text) for p in KEY_SHAPES):
            problems.append(f"{f}: contains a key-shaped string")
    if ".env" in files:
        problems.append(".env is not ignored by git")
    print(f"scanned {len(files)} files")
    for p in problems:
        print("  FAIL", p)
    print("OK: no secrets" if not problems else "SECRETS FOUND: do not push")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
