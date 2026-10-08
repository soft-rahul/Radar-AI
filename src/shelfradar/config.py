"""Runtime settings, read once from the environment (and a local .env file)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

Mode = Literal["live", "record", "replay"]
_MODES: tuple[Mode, ...] = ("live", "record", "replay")


@dataclass(frozen=True)
class Settings:
    serpapi_key: str | None
    gemini_api_key: str | None
    gemini_model: str
    serpapi_mode: Mode
    max_searches: int


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    mode = os.getenv("SERPAPI_MODE", "replay").strip().lower()
    if mode not in _MODES:
        raise ValueError(f"SERPAPI_MODE must be one of {_MODES}, got {mode!r}")

    return Settings(
        serpapi_key=os.getenv("SERPAPI_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        serpapi_mode=mode,  # type: ignore[arg-type]
        max_searches=int(os.getenv("MAX_SEARCHES", "120")),
    )
