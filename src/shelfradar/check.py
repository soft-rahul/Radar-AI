"""Setup check: are the keys loaded, and is .env kept out of git? Never prints a key."""

import subprocess
import sys

from shelfradar.config import PROJECT_ROOT, load_settings


def _mask(value: str | None) -> str:
    if not value:
        return "MISSING"
    return f"set ({len(value)} chars)"


def _env_is_ignored() -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=PROJECT_ROOT, check=False
    )
    return result.returncode == 0


def main() -> None:
    settings = load_settings()
    ignored = _env_is_ignored()
    env_exists = (PROJECT_ROOT / ".env").exists()

    print(f".env file       : {'found' if env_exists else 'MISSING (cp .env.example .env)'}")
    print(f".env gitignored : {'yes' if ignored else 'NO - fix .gitignore before committing'}")
    print(f"SERPAPI_KEY     : {_mask(settings.serpapi_key)}")
    print(f"GEMINI_API_KEY  : {_mask(settings.gemini_api_key)}")
    print(f"SERPAPI_MODE    : {settings.serpapi_mode}")
    print(f"MAX_SEARCHES    : {settings.max_searches}")

    ok = ignored and settings.serpapi_key and settings.gemini_api_key
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
