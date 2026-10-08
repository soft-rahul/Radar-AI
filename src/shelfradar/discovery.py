"""Find the buying questions Indians actually ask, in English and Hindi, from real search data."""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from shelfradar.llm import JsonLLM, LLMError
from shelfradar.models import Variant
from shelfradar.serp_client import SerpClient

Lang = Literal["en", "hi"]

# Buying intent: the user is choosing between brands.
_BUY = {
    "en": ["best", "top", "no 1", "no.1", "number one", "number 1", "which", "brand", "company",
           "under", "budget", "cheap", "affordable", "vs", "for beginners", "for women",
           "for muscle", "for weight", "recommend", "good",
           # Hinglish (Hindi in Roman letters), which Indians type a lot
           "sabse", "accha", "achha", "achcha", "kaun sa", "konsa", "kis company"],
    "hi": ["सबसे अच्छा", "सबसे अच्छी", "बेस्ट", "नंबर वन", "नंबर 1", "कौन सा", "कौन सी", "कंपनी",
           "ब्रांड", "सस्ता", "सस्ती", "शुरुआती", "किसके लिए"],
}
# Not a brand choice: health trivia, dosage, celebrities, how-to.
_TRIVIA = {
    "en": ["side effect", "benefit", "how much", "how to", "how many", "what age", "age ",
           "kohli", "salman", "is it safe", "safe to", "when to", "meaning", "in hindi", "kidney",
           "per day", "daily", "kitna", "kitni", "kaise", "nuksan", "fayde"],
    "hi": ["नुकसान", "फायदे", "कितना", "कितनी", "कितने", "उम्र", "कैसे", "कब ", "मतलब", "किडनी",
           "दिन में"],
}


class Candidate(BaseModel):
    text: str
    lang: Lang
    origin: Literal["autocomplete", "people_also_ask"]
    score: int = 0
    reason: str = ""


class QuestionPair(BaseModel):
    id: str
    en: str
    hi: str
    hi_from_search: bool = Field(description="True if the Hindi text is a real search question, "
                                             "False if it was translated")
    note: str = ""


class PairProposal(BaseModel):
    pairs: list[QuestionPair]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[?!.,;:।'\"()]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def lang_of(text: str) -> Lang:
    return "hi" if re.search(r"[ऀ-ॿ]", text) else "en"


def score(text: str, lang: Lang, brands: list[str]) -> tuple[int, str]:
    norm = f" {normalize(text)} "
    if any(normalize(b) in norm for b in brands):
        return -5, "names a brand (navigational)"
    trivia = [t for t in _TRIVIA[lang] if t in norm]
    if trivia:
        return -3, f"not a brand choice: {trivia[0].strip()}"
    hits = [t for t in _BUY[lang] if f" {t}" in norm or norm.startswith(f" {t}")]
    if not hits:
        return 0, "no buying signal"
    return len(hits), "buying intent: " + ", ".join(hits[:3])


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for c in cands:
        key = normalize(c.text)
        if key not in seen or c.origin == "people_also_ask":
            seen[key] = c
    return list(seen.values())


async def autocomplete(client: SerpClient, seed: str, hl: Lang) -> list[Candidate]:
    body = await client.search("google_autocomplete", {"q": seed, "gl": "in", "hl": hl})
    return [Candidate(text=s["value"], lang=lang_of(s["value"]), origin="autocomplete")
            for s in body.get("suggestions") or [] if s.get("value")]


async def people_also_ask(client: SerpClient, variant: Variant, query: str) -> list[Candidate]:
    """Read PAA from the google result we already fetch for the AI Overview (no extra search
    when the same request is recorded)."""
    from shelfradar.engines import google_params

    body = await client.search("google", google_params(variant, query))
    return [Candidate(text=q["question"], lang=lang_of(q["question"]), origin="people_also_ask")
            for q in body.get("related_questions") or [] if q.get("question")]


def rank(cands: list[Candidate], brands: list[str]) -> list[Candidate]:
    for c in cands:
        c.score, c.reason = score(c.text, c.lang, brands)
    return sorted(dedupe(cands), key=lambda c: (-c.score, c.origin != "people_also_ask", c.text))


PAIR_PROMPT = """You are helping design a market study of how AI search engines answer Indian
shoppers' buying questions about {category}.

Below are REAL search questions collected from Google India, ranked by buying intent.
English candidates:
{en}

Hindi candidates:
{hi}

Choose {n} distinct question pairs. Each pair is one English question and one Hindi question
with the SAME buying intent (choosing between brands/products).
Rules:
- Use the English candidates' wording; light grammar fixes only.
- Prefer a Hindi candidate from the list when one matches the intent; set hi_from_search=true.
- Only if no Hindi candidate matches, write a natural Hindi question that an Indian shopper
  would type (Devanagari), set hi_from_search=false and explain in note.
- Never mention any brand name. Never pick health/dosage/celebrity questions.
- ids: q1..q{n}.
"""


def propose_pairs(llm: JsonLLM, en: list[Candidate], hi: list[Candidate], *, category: str,
                  n: int = 5) -> list[QuestionPair]:
    def fmt(cs: list[Candidate]) -> str:
        return "\n".join(f"- {c.text}" for c in cs if c.score > 0) or "- (none)"

    try:
        proposal = llm.generate_json(
            PAIR_PROMPT.format(category=category, en=fmt(en), hi=fmt(hi), n=n), PairProposal)
    except LLMError:
        return []
    hi_texts = {normalize(c.text) for c in hi}
    pairs = proposal.pairs[:n]
    for p in pairs:
        # Trust, but verify: "from search" must really be one of the collected Hindi questions.
        p.hi_from_search = normalize(p.hi) in hi_texts
    return pairs
