"""Phase 4: collect real buying questions (autocomplete + People Also Ask) and propose EN/HI pairs.

Usage: uv run python scripts/discover.py study/whey.json
Runs in record mode: new autocomplete calls cost 1 search each; anything already recorded is free.
"""

import asyncio
import json
import sys
from pathlib import Path

from shelfradar.config import load_settings
from shelfradar.discovery import autocomplete, people_also_ask, propose_pairs, rank
from shelfradar.llm import Gemini
from shelfradar.models import Variant
from shelfradar.serp_client import SerpClient

PROBE_QUERIES = {
    "mumbai-en": "best whey protein for beginners in India",
    "delhi-hi": "भारत में शुरुआती लोगों के लिए सबसे अच्छा व्हे प्रोटीन कौन सा है",
}


async def collect(study: dict, client: SerpClient):
    cands = []
    for lang, seeds in study["seeds"].items():
        for seed in seeds:
            cands += await autocomplete(client, seed, lang)
    for v in study["variants"]:
        cands += await people_also_ask(client, Variant(**v), PROBE_QUERIES[v["id"]])
    brands = [n for b in study["brands"] for n in [b["name"], *b["aliases"]]]
    ranked = rank(cands, brands)
    return [c for c in ranked if c.lang == "en"], [c for c in ranked if c.lang == "hi"]


def show(title: str, cands) -> None:
    print(f"\n{title}")
    for c in cands:
        mark = "KEEP" if c.score > 0 else "drop"
        print(f"  {mark} {c.score:+d}  [{c.origin[:4]}] {c.text}   ({c.reason})")


async def main(study_path: Path) -> None:
    settings = load_settings()
    study = json.loads(study_path.read_text(encoding="utf-8"))
    async with SerpClient(api_key=settings.serpapi_key, mode="record",
                          max_searches=6) as client:
        en, hi = await collect(study, client)
        print(f"searches spent: {client.ledger.live_calls} · reused from cassettes: {client.ledger.replayed}")

    show("ENGLISH candidates", en)
    show("HINDI candidates", hi)

    pairs = propose_pairs(Gemini(settings.gemini_api_key, settings.gemini_model), en, hi,
                          category=study["category"])
    print("\nPROPOSED PAIRS (Gemini, verified against collected Hindi questions)")
    for p in pairs:
        src = "real Hindi search" if p.hi_from_search else "translated"
        print(f"  {p.id}: EN {p.en}\n      HI {p.hi}   [{src}] {p.note}")

    out = study_path.with_name("questions.json")
    out.write_text(json.dumps({"status": "proposed", "pairs": [p.model_dump() for p in pairs]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out} (status: proposed, awaiting your approval)")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "study/whey.json")))
