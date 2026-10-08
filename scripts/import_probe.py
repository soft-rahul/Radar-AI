"""Re-file the Phase 1 probe responses as SerpClient cassettes, so later phases test offline.

Costs no searches: it only reads fixtures/serp/probe/*.json and writes fixtures/serp/cassettes/.
"""

import json
from pathlib import Path

from probe import CASSETTES as PROBE_DIR, VARIANTS

from shelfradar.serp_client import DEFAULT_CASSETTES, SerpClient

RECORDED_AT = "2026-10-06T11:42:33+00:00"


def requests_for(variant: str, v: dict) -> list[tuple[str, str, dict, int]]:
    google = {"gl": "in", **v}
    reqs = [
        (f"{variant}__google", "google", google, 0),
        (f"{variant}__google_ai_mode", "google_ai_mode", google, 0),
        (f"{variant}__bing_copilot", "bing_copilot", {"q": v["q"]}, 0),
    ]
    first = json.loads((PROBE_DIR / f"{variant}__google.json").read_text(encoding="utf-8"))
    token = (first.get("ai_overview") or {}).get("page_token")
    if token:
        reqs.append((f"{variant}__google_ai_overview", "google_ai_overview", {"page_token": token}, 0))
    if variant == "mumbai-en":
        reqs.append((f"{variant}__google_ai_mode_repeat", "google_ai_mode", google, 1))
    return reqs


def main() -> None:
    client = SerpClient(api_key=None, mode="replay", max_searches=0, cassette_dir=DEFAULT_CASSETTES)
    for variant, v in VARIANTS.items():
        for name, engine, params, sample in requests_for(variant, v):
            body = json.loads((PROBE_DIR / f"{name}.json").read_text(encoding="utf-8"))
            path: Path = client.cassette_path(engine, params, sample)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"request": {"engine": engine, "params": params, "sample": sample},
                      "recorded_at": RECORDED_AT, "response": body}
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{name:42s} -> {path.relative_to(DEFAULT_CASSETTES.parents[2])}")


if __name__ == "__main__":
    main()
