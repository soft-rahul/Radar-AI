"""Phase 1 probe: which SerpApi AI engines answer Indian food questions, in EN and HI?

Spends roughly 8-10 SerpApi searches. Saves every raw response (key scrubbed) as a
cassette under fixtures/serp/probe/ and writes probe_report.md. Never prints a key.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shelfradar.config import PROJECT_ROOT, load_settings

SERPAPI = "https://serpapi.com"
CASSETTES = PROJECT_ROOT / "fixtures" / "serp" / "probe"
REPORT = PROJECT_ROOT / "docs" / "probe_report.md"

VARIANTS = {
    "mumbai-en": {
        "q": "best whey protein for beginners in India",
        "location": "Mumbai, Maharashtra, India",
        "hl": "en",
    },
    "delhi-hi": {
        "q": "भारत में शुरुआती लोगों के लिए सबसे अच्छा व्हे प्रोटीन कौन सा है",
        "location": "Delhi, India",
        "hl": "hi",
    },
}

BRANDS = [
    "MuscleBlaze", "Optimum Nutrition", "MyProtein", "The Whole Truth", "AS-IT-IS",
    "Nakpro", "Avvatar", "Wellcore", "GNC", "Dymatize", "Isopure", "Bigmuscles",
    "HealthKart", "OZiva", "Plix", "Fast&Up", "Labrada", "Ultimate Nutrition",
    "Nutrabay", "Atom", "Scitron", "Amul",
]

TEXT_KEYS = {"snippet", "title", "text", "answer"}
HEX64 = re.compile(r"\b[a-f0-9]{64}\b")


@dataclass
class Probe:
    variant: str
    engine: str
    status: str = "not run"
    http: int | None = None
    error: str | None = None
    text: str = ""
    references: int = 0
    organic: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def brands(self) -> list[str]:
        low = self.text.lower()
        return [b for b in BRANDS if b.lower() in low]


def scrub(payload: dict, key: str) -> dict:
    raw = json.dumps(payload, ensure_ascii=False).replace(key, "REDACTED")
    return json.loads(HEX64.sub("REDACTED", raw))


def collect_text(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TEXT_KEYS and isinstance(v, str):
                out.append(v)
            elif k not in {"link", "source", "thumbnail", "serpapi_link"}:
                out.extend(collect_text(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(collect_text(item))
    return out


def save(name: str, payload: dict, key: str) -> None:
    (CASSETTES / f"{name}.json").write_text(
        json.dumps(scrub(payload, key), ensure_ascii=False, indent=2), encoding="utf-8"
    )


class Client:
    def __init__(self, key: str):
        self.key = key
        self.http = httpx.Client(timeout=90)
        self.searches = 0

    def search(self, params: dict) -> tuple[int, dict]:
        self.searches += 1
        r = self.http.get(f"{SERPAPI}/search.json", params={**params, "api_key": self.key})
        try:
            body = r.json()
        except ValueError:
            body = {"error": f"non-JSON response ({r.status_code})"}
        return r.status_code, body


def run_engine(client: Client, p: Probe, params: dict, name: str) -> dict:
    t0 = time.monotonic()
    code, body = client.search(params)
    p.seconds = round(time.monotonic() - t0, 1)
    p.http = code
    save(name, body, client.key)
    if body.get("error"):
        p.status, p.error = "error", body["error"][:160]
    return body


def probe_google(client: Client, variant: str, v: dict) -> Probe:
    p = Probe(variant, "google + AI Overview")
    body = run_engine(client, p, {"engine": "google", "gl": "in", "no_cache": "true", **v},
                      f"{variant}__google")
    if p.status == "error":
        return p
    p.organic = len(body.get("organic_results", []))
    aio = body.get("ai_overview")
    if not aio:
        p.status = "no_ai_block"
        return p
    if "page_token" in aio and not aio.get("text_blocks"):
        p.notes.append("AIO deferred -> page_token follow-up")
        code, follow = client.search({"engine": "google_ai_overview", "page_token": aio["page_token"]})
        save(f"{variant}__google_ai_overview", follow, client.key)
        if follow.get("error"):
            p.status, p.error = "error", f"follow-up: {follow['error'][:140]}"
            return p
        aio = follow.get("ai_overview", follow)
    p.text = " ".join(collect_text(aio.get("text_blocks", [])))
    p.references = len(aio.get("references", []))
    p.status = "ok" if p.text else "no_ai_block"
    return p


def probe_ai_mode(client: Client, variant: str, v: dict, tag: str = "") -> Probe:
    p = Probe(variant, f"Google AI Mode{tag}")
    body = run_engine(client, p, {"engine": "google_ai_mode", "gl": "in", "no_cache": "true", **v},
                      f"{variant}__google_ai_mode{tag.replace(' ', '_')}")
    if p.status == "error":
        return p
    p.text = body.get("reconstructed_markdown") or " ".join(collect_text(body.get("text_blocks", [])))
    p.references = len(body.get("references", []))
    p.status = "ok" if p.text else "no_ai_block"
    return p


def probe_copilot(client: Client, variant: str, v: dict) -> Probe:
    p = Probe(variant, "Bing Copilot")
    body = run_engine(client, p, {"engine": "bing_copilot", "q": v["q"], "no_cache": "true"},
                      f"{variant}__bing_copilot")
    if p.status == "error":
        return p
    p.text = " ".join(collect_text(body.get("text_blocks", [])))
    p.references = len(body.get("references", []))
    p.status = "ok" if p.text else "no_ai_block"
    p.notes.append("no location/hl sent (Bing params differ)")
    return p


def check_gemini(key: str | None, model: str) -> str:
    if not key:
        return "MISSING key"
    try:
        from google import genai

        client = genai.Client(api_key=key)  # keep a reference: a temporary client is closed before the call
        resp = client.models.generate_content(
            model=model, contents="Reply with the single word OK."
        )
        return f"ok ({(resp.text or '').strip()[:20]!r})"
    except Exception as exc:  # report, never crash the probe
        return f"error: {type(exc).__name__}: {str(exc)[:120]}"


def main() -> None:
    s = load_settings()
    if not s.serpapi_key:
        raise SystemExit("SERPAPI_KEY missing in .env")
    CASSETTES.mkdir(parents=True, exist_ok=True)
    client = Client(s.serpapi_key)

    acct = client.http.get(f"{SERPAPI}/account.json", params={"api_key": s.serpapi_key}).json()
    before = acct.get("total_searches_left")
    plan = acct.get("plan_name")

    locations = {}
    for loc in ("Mumbai", "Delhi"):
        res = client.http.get(f"{SERPAPI}/locations.json", params={"q": loc, "limit": 3}).json()
        locations[loc] = [r.get("canonical_name") for r in res]

    probes: list[Probe] = []
    for variant, v in VARIANTS.items():
        probes.append(probe_google(client, variant, v))
        probes.append(probe_ai_mode(client, variant, v))
        probes.append(probe_copilot(client, variant, v))
    repeat = probe_ai_mode(client, "mumbai-en", VARIANTS["mumbai-en"], tag=" repeat")
    probes.append(repeat)

    gemini = f"{s.gemini_model}: {check_gemini(s.gemini_api_key, s.gemini_model)}"
    after = client.http.get(f"{SERPAPI}/account.json", params={"api_key": s.serpapi_key}).json().get(
        "total_searches_left"
    )

    first = next((p for p in probes if p.engine == "Google AI Mode" and p.variant == "mumbai-en"), None)
    same_brands = first and set(first.brands) == set(repeat.brands)
    same_text = first and first.text.strip() == repeat.text.strip()

    working = {
        eng for eng in ("google + AI Overview", "Google AI Mode", "Bing Copilot")
        if any(p.engine == eng and p.status == "ok" for p in probes)
    }
    verdict = "GO" if len(working) >= 2 else "NO-GO"

    lines = [
        "# Phase 1 probe report",
        f"_Run: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
        f"- Plan: **{plan}** · searches left before: **{before}** · after: **{after}** · "
        f"searches sent by probe: **{client.searches}**",
        f"- Locations: {json.dumps(locations, ensure_ascii=False)}",
        f"- Gemini: **{gemini}**",
        "",
        "| Variant | Engine | Status | HTTP | Text chars | Refs | Organic | Brands named | Secs | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in probes:
        notes = "; ".join(p.notes + ([p.error] if p.error else []))
        lines.append(
            f"| {p.variant} | {p.engine} | {p.status} | {p.http} | {len(p.text)} | {p.references} | "
            f"{p.organic} | {', '.join(p.brands) or '-'} | {p.seconds} | {notes or '-'} |"
        )
    lines += [
        "",
        f"**Variability (AI Mode EN, two no_cache runs):** identical text: {same_text} · "
        f"identical brand set: {same_brands}",
        f"(run 1 brands: {first.brands if first else '-'} · run 2 brands: {repeat.brands})",
        "",
        f"**AI engines working for India:** {sorted(working) or 'none'} → **{verdict}**",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
