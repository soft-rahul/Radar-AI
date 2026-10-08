"""The shared shapes every phase after the adapters works with."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Engine(StrEnum):
    AI_OVERVIEW = "ai_overview"  # Google's AI Overview, from the google engine (+ follow-up)
    AI_MODE = "ai_mode"          # google_ai_mode
    COPILOT = "copilot"          # bing_copilot


class Status(StrEnum):
    OK = "ok"
    NO_AI_BLOCK = "no_ai_block"  # the engine showed no AI answer: not the same as "brand not mentioned"
    ERROR = "error"


class Variant(BaseModel):
    id: str            # e.g. "mumbai-en"
    location: str      # SerpApi canonical-ish location, e.g. "Mumbai, Maharashtra, India"
    hl: str            # Google interface language sent to SerpApi
    lang: str = "en"   # which question text to ask: "en", "hi" or "hinglish"


class Source(BaseModel):
    title: str = ""
    url: str
    domain: str
    publisher: str = ""
    translated: bool = False  # cited through translate.google.com (an English page shown in Hindi)


class OrganicResult(BaseModel):
    position: int
    url: str
    domain: str
    title: str = ""


class AIAnswer(BaseModel):
    engine: Engine
    variant: str
    query: str
    question_id: str = ""
    sample: int = 0
    status: Status
    text: str = ""
    sources: list[Source] = Field(default_factory=list)
    organic: list[OrganicResult] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class Brand(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    # Ordinary words that are also brand names ("Atom"): counted only next to a category term.
    ambiguous_aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class Study(BaseModel):
    category: str
    category_terms: list[str]
    variants: list[Variant]
    brands: list[Brand]
    seeds: dict[str, list[str]] = Field(default_factory=dict)
