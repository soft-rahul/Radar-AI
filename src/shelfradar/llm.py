"""Thin Gemini wrapper: structured JSON output validated by a Pydantic schema."""

import hashlib
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JsonLLM(Protocol):
    def generate_json(self, prompt: str, schema: type[T]) -> T: ...


class LLMError(RuntimeError):
    pass


class Gemini:
    def __init__(self, api_key: str, model: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)  # keep a reference; see Phase 1 notes
        self.model = model

    def generate_json(self, prompt: str, schema: type[T]) -> T:
        from google.genai import types

        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                ),
            )
        except Exception as exc:
            raise LLMError(f"{self.model}: {type(exc).__name__}: {str(exc)[:160]}") from exc
        if isinstance(resp.parsed, schema):
            return resp.parsed
        try:
            return schema.model_validate_json(resp.text or "")
        except ValueError as exc:
            raise LLMError(f"{self.model}: response did not match {schema.__name__}") from exc


class TapeMissing(LLMError):
    pass


class TapedLLM:
    """Record/replay for LLM calls, like the SerpApi cassettes: the demo needs no Gemini key."""

    def __init__(self, inner: JsonLLM | None, tape_dir: Path, mode: str, model: str = ""):
        self.inner, self.mode, self.model = inner, mode, model
        self.tape_dir = Path(tape_dir)
        self.calls = self.replayed = 0

    def _path(self, prompt: str, schema: type[BaseModel]) -> Path:
        key = hashlib.sha256(f"{self.model}|{schema.__name__}|{prompt}".encode()).hexdigest()[:20]
        return self.tape_dir / schema.__name__ / f"{key}.json"

    def generate_json(self, prompt: str, schema: type[T]) -> T:
        path = self._path(prompt, schema)
        if path.exists() and self.mode in ("record", "replay"):
            self.replayed += 1
            return schema.model_validate_json(path.read_text(encoding="utf-8"))
        if self.mode == "replay" or self.inner is None:
            raise TapeMissing(f"No LLM tape at {path}; run once in record mode with GEMINI_API_KEY")
        result = self.inner.generate_json(prompt, schema)
        self.calls += 1
        if self.mode == "record":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result


class FallbackLLM:
    """Use the primary model's tapes when they exist; otherwise call (and tape) a fallback model.
    Remembers which model produced each prompt's answer so the study can disclose it."""

    def __init__(self, primary: TapedLLM, fallback: TapedLLM):
        self.primary, self.fallback = primary, fallback
        self.model_for_prompt: dict[str, str] = {}

    def generate_json(self, prompt: str, schema: type[T]) -> T:
        try:
            result = self.primary.generate_json(prompt, schema)
            self.model_for_prompt[prompt] = self.primary.model
        except LLMError:
            result = self.fallback.generate_json(prompt, schema)
            self.model_for_prompt[prompt] = self.fallback.model
        return result
