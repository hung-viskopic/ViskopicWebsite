"""External AI for vivas: transcribing spoken answers, plus the template questions used when Visq is off.

Question drafting and follow-ups moved to Visq (`viskopic/viva/visq.py`). Transcription still uses OpenAI.

Local testing with synthetic essays only. Sending a student's voice to an outside provider breaks
"Viskopic cannot see university data", so it runs only when VISKOPIC_VIVA_AI_PROVIDER=openai AND
VISKOPIC_ALLOW_EXTERNAL_AI=true, and every use is labelled with the provider.

The AI never judges a student: it transcribes audio a reviewer can listen to.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import get_settings

log = logging.getLogger("viskopic.viva.ai")

MAX_QUESTION_CHARS = 400
MAX_PASSAGE_CHARS = 1000   # Visq passages are up to 120 words

DEFAULT_QUESTIONS = {
    "style": [
        "Walk me through how you planned and drafted this essay, from the first notes to the final version.",
        "Pick one paragraph you are pleased with and explain how you wrote it.",
        "How does the way you wrote this essay compare with your earlier assignments?",
        "Which sources did you find hardest to use, and how did you deal with that?",
    ],
    "reasoning": [
        "In two or three sentences, what is the main argument of your essay?",
        "What is the strongest objection to your argument, and how would you respond?",
        "Explain one key concept from your essay as if to someone new to the subject.",
        "If you had another week, what would you change and why?",
    ],
}


class AIUnavailable(Exception):
    pass


@dataclass
class DraftQuestion:
    text: str
    source: str                  # template
    passage: str | None = None
    passage_start: int | None = None
    passage_end: int | None = None


@dataclass
class Transcript:
    text: str
    timeline: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""


class Provider(Protocol):
    name: str

    def transcribe(self, audio: bytes, mime_type: str) -> dict: ...


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        s = get_settings()
        if not s.openai_api_key:
            raise AIUnavailable("VISKOPIC_OPENAI_API_KEY is not set")
        self._s = s
        self._headers = {"Authorization": f"Bearer {s.openai_api_key}"}

    def transcribe(self, audio: bytes, mime_type: str) -> dict:
        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "video/webm": "webm", "video/mp4": "mp4"}[mime_type]
        r = httpx.post(
            f"{self._s.openai_base_url}/audio/transcriptions",
            headers=self._headers,
            data={"model": self._s.openai_transcribe_model, "response_format": "verbose_json",
                  "timestamp_granularities[]": "segment", "language": "en"},
            files={"file": (f"answer.{ext}", audio, mime_type)},
            timeout=120,
        )
        if r.status_code != 200:
            raise AIUnavailable(f"transcription failed ({r.status_code})")
        return r.json()


_provider: Provider | None = None


def enabled() -> bool:
    s = get_settings()
    return s.allow_external_ai and s.viva_ai_provider != "none" and (
        _provider is not None or (s.viva_ai_provider == "openai" and bool(s.openai_api_key)))


def provider() -> Provider:
    if not enabled():
        raise AIUnavailable("external AI is switched off")
    global _provider
    if _provider is None:
        _provider = OpenAIProvider()
    return _provider


def set_provider(p: Provider | None) -> None:
    """Tests install a fake provider."""
    global _provider
    _provider = p


# ------------------------------------------------------------------ passages


def locate(text: str, passage: str) -> tuple[int, int] | None:
    """Find a passage in the text, tolerating differences in whitespace. Returns character offsets."""
    if not passage.strip():
        return None
    words = passage.split()
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def template_questions(kind: str, count: int) -> list[DraftQuestion]:
    return [DraftQuestion(text=q, source="template") for q in DEFAULT_QUESTIONS[kind][:count]]


# ------------------------------------------------------------------ transcription


def timeline_from_segments(raw: dict) -> dict:
    """Speech and silence in milliseconds, from the transcription's segment timestamps. Numbers only."""
    segs = sorted(((float(s.get("start", 0)), float(s.get("end", 0))) for s in raw.get("segments") or []
                   if str(s.get("text", "")).strip()), key=lambda x: x[0])
    duration = float(raw.get("duration") or (segs[-1][1] if segs else 0))
    ms = lambda x: int(round(x * 1000))  # noqa: E731
    if not segs:
        return {"duration_ms": ms(duration), "first_speech_ms": None, "speech_ms": 0, "segments": [],
                "longest_pause_ms": ms(duration), "pauses_over_3s": 0}
    gaps = [segs[0][0]] + [max(0.0, b[0] - a[1]) for a, b in zip(segs, segs[1:])]
    return {
        "duration_ms": ms(duration),
        "first_speech_ms": ms(segs[0][0]),
        "speech_ms": ms(sum(e - s for s, e in segs)),
        "segments": [[ms(s), ms(e)] for s, e in segs[:200]],
        "longest_pause_ms": ms(max(gaps)),
        "pauses_over_3s": sum(1 for g in gaps[1:] if g >= 3),
    }


def transcribe(audio: bytes, mime_type: str) -> Transcript:
    p = provider()
    raw = p.transcribe(audio, mime_type)
    return Transcript(text=str(raw.get("text", "")).strip(), timeline=timeline_from_segments(raw),
                      provider=p.name, model=get_settings().openai_transcribe_model)
