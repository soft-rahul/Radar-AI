"""One shared line to SerpApi: cassettes (record/replay), a credit budget, one retry, key scrubbing.

Modes
- replay: cassettes only; the network is never touched (tests, judges' demo).
- record: use a cassette when one exists, otherwise call SerpApi and save a new one.
- live:   always call SerpApi; nothing is saved.
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from shelfradar.config import PROJECT_ROOT, Mode, Settings

SERPAPI_URL = "https://serpapi.com"
DEFAULT_CASSETTES = PROJECT_ROOT / "fixtures" / "serp" / "cassettes"

# Parameters that never change what SerpApi answers, so they stay out of the fingerprint.
_UNKEYED = {"api_key", "no_cache", "output", "async"}
_HEX64 = re.compile(r"\b[a-f0-9]{64}\b")
_RETRYABLE = {429, 500, 502, 503, 504}
# Only strings this long are treated as secrets, so a short value can never corrupt JSON keys.
_MIN_SECRET_LEN = 16


class SerpClientError(RuntimeError):
    pass


class BudgetExceeded(SerpClientError):
    pass


class CassetteMissing(SerpClientError):
    pass


@dataclass
class Ledger:
    live_calls: int = 0
    replayed: int = 0
    retries: int = 0
    api_errors: int = 0


def fingerprint(engine: str, params: dict[str, Any], sample: int = 0) -> str:
    keyed = {k: str(v) for k, v in params.items() if k not in _UNKEYED}
    blob = json.dumps({"engine": engine, "params": keyed, "sample": sample},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def scrub(value: Any, secret: str | None) -> Any:
    raw = json.dumps(value, ensure_ascii=False)
    if secret and len(secret) >= _MIN_SECRET_LEN:
        raw = raw.replace(secret, "REDACTED")
    return json.loads(_HEX64.sub("REDACTED", raw))


class SerpClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        mode: Mode,
        max_searches: int,
        cassette_dir: Path = DEFAULT_CASSETTES,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 2.0,
        timeout: float = 90.0,
    ):
        if mode != "replay" and not api_key:
            raise SerpClientError(f"SERPAPI_KEY is required in {mode!r} mode")
        self._key = api_key
        self.mode = mode
        self.max_searches = max_searches
        self.cassette_dir = cassette_dir
        self.retry_delay = retry_delay
        self.ledger = Ledger()
        self._http = httpx.AsyncClient(base_url=SERPAPI_URL, timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> "SerpClient":
        return cls(api_key=settings.serpapi_key, mode=settings.serpapi_mode,
                   max_searches=settings.max_searches, **kwargs)

    async def __aenter__(self) -> "SerpClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    def cassette_path(self, engine: str, params: dict[str, Any], sample: int = 0) -> Path:
        return self.cassette_dir / engine / f"{fingerprint(engine, params, sample)}.json"

    async def search(self, engine: str, params: dict[str, Any], *, sample: int = 0) -> dict[str, Any]:
        path = self.cassette_path(engine, params, sample)

        if self.mode in ("record", "replay") and path.exists():
            self.ledger.replayed += 1
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.mode == "replay":
            raise CassetteMissing(
                f"No cassette for {engine} {params} (sample {sample}). Expected {path}. "
                "Run once with SERPAPI_MODE=record to create it."
            )
        if self.ledger.live_calls >= self.max_searches:
            raise BudgetExceeded(f"Search budget of {self.max_searches} used up")

        body = await self._call(engine, params)
        if body.get("error"):
            self.ledger.api_errors += 1
        if self.mode == "record":
            self._save(path, engine, params, sample, body)
        return body

    async def _call(self, engine: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "engine": engine, "api_key": self._key}
        for attempt in (1, 2):
            self.ledger.live_calls += 1
            try:
                resp = await self._http.get("/search.json", params=query)
            except httpx.TransportError as exc:
                if attempt == 1:
                    await self._backoff()
                    continue
                raise SerpClientError(f"{engine}: network error {type(exc).__name__}") from exc
            if resp.status_code in _RETRYABLE and attempt == 1:
                await self._backoff()
                continue
            return self._parse(engine, resp)
        raise AssertionError("unreachable")

    async def _backoff(self) -> None:
        self.ledger.retries += 1
        await asyncio.sleep(self.retry_delay)

    def _parse(self, engine: str, resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = {"error": f"non-JSON response ({resp.status_code})"}
        body = scrub(body, self._key)
        if resp.status_code >= 400:
            raise SerpClientError(f"{engine}: HTTP {resp.status_code}: {str(body.get('error', ''))[:200]}")
        return body

    def _save(self, path: Path, engine: str, params: dict[str, Any], sample: int,
              body: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "request": scrub({"engine": engine, "params": params, "sample": sample}, self._key),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "response": body,
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    async def searches_left(self) -> int | None:
        """Account API: free, never counted against the budget."""
        if not self._key:
            return None
        resp = await self._http.get("/account.json", params={"api_key": self._key})
        return resp.json().get("total_searches_left") if resp.status_code == 200 else None
