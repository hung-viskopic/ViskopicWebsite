"""Visq: the model team's viva engine, used to write the main questions and to decide follow-ups.

Visq is vendored unchanged in `vendor/visq_v2/visq/` (only the modules Visq 1 needs: argument extraction,
question planning and writing, follow-up decisions). This module is our side of the integration:

- checks the vendored files' SHA-256 before importing them,
- talks to OpenRouter through our own small HTTP client, with Visq's zero-data-retention provider setting
  on every call (Visq's own client reads a .env file and needs the openai package; ours reads our settings),
  or, with VISKOPIC_VISQ_PROVIDER=openai, to OpenAI's Responses API (store=false; zero retention is then up to
  the OpenAI organisation's own agreement, so the engine label says so),
- never lets Visq write to disk: its extraction cache is switched off, so no essay-derived text is stored
  outside our encrypted tables,
- maps Visq's output to our question records. Passages must be found in the submission, or are dropped.

The student never sees the reviewer note ("what a good answer covers") or Visq's reason for a follow-up.
Visq never rates an answer.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import get_settings
from . import ai

log = logging.getLogger("viskopic.viva.visq")

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "visq_v2"
VENDOR_COMMIT = "3d631d8"           # keep in step with vendor/visq_v2/VENDORED.md
FILE_SHA256 = {
    "__init__.py": "0e678bd88d8f0c598548716a779aed7e3c9b9225936b6fca3f9efb078cf678c3",
    "aroa.py": "e55f854449c0354adbccbc294a8491d0671414031ab6c478bbe566589c6c13b3",
    "config.py": "1e6fd169c40895d32e401944ab6b75547adb614180396fb30347a18d702b9ef1",
    "fake.py": "e0b5a60e5aca9ff9dd95f40ba6311d88ce5a29d52062c3973903449ae2f390bd",
    "followups.py": "0725e37114d15e0c9a66a2c07981f17363373294f3808f2062e9e02d5b7ac55e",
    "llm.py": "7a337c7e7d944fc3b7637a3355579560aa885d61aca21fe2ac346b8fe66dedf2",
    "prompts.py": "220c21feeea200e09727566d181033b14a2a5424bd4991edff38a1f3cce4ddeb",
    "questions.py": "ca2f69cea60a326d9180bd79fe72401e0798293df2b84891b3639914984afef2",
    "text.py": "8d06b38825a497f4dd8802cf613788a44d3436637b14dc2669daf0d276650d5f",
}

_lock = threading.Lock()
_mods = None
_client_factory = None


class VisqUnavailable(Exception):
    pass


def modules():
    """Visq's modules, imported once after their hashes are checked."""
    global _mods
    with _lock:
        if _mods is None:
            pkg = VENDOR_DIR / "visq"
            for name, want in FILE_SHA256.items():
                got = hashlib.sha256((pkg / name).read_bytes()).hexdigest()
                if got != want:
                    raise RuntimeError(f"vendored visq/{name} has SHA-256 {got}, expected {want}; "
                                       "re-copy it unchanged from the model repository")
            if str(VENDOR_DIR) not in sys.path:
                sys.path.insert(0, str(VENDOR_DIR))
            import visq
            from visq import aroa, config, fake, followups, llm, questions, text

            _mods = type("VisqModules", (), dict(visq=visq, aroa=aroa, config=config, fake=fake, followups=followups,
                                                  llm=llm, questions=questions, text=text))
        return _mods


# ------------------------------------------------------------------ client


class OpenRouterClient:
    """Implements Visq's client interface (`chat(...) -> LLMResult`) with httpx and our settings."""

    def __init__(self, budget=None):
        s = get_settings()
        if not s.openrouter_api_key:
            raise VisqUnavailable("VISKOPIC_OPENROUTER_API_KEY is not set")
        m = modules()
        if m.config.REQUIRE_ZDR and m.config.PROVIDER_PREFS.get("zdr") is not True:
            raise VisqUnavailable("Visq requires zero data retention but its provider settings do not ask for it")
        self._s = s
        self._m = m
        self.budget = budget or m.llm.Budget(m.config.MAX_SPEND_PER_SESSION_USD)

    def chat(self, system, user, *, temperature, max_tokens, model=None):
        m = self._m
        self.budget.check()
        body = {"model": model or model_name(), "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "provider": m.config.PROVIDER_PREFS}
        if m.config.REASONING_EFFORT:
            body["reasoning"] = {"effort": m.config.REASONING_EFFORT, "exclude": True}
        try:
            r = httpx.post(f"{self._s.openrouter_base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {self._s.openrouter_api_key}"},
                           timeout=httpx.Timeout(m.config.CALL_TIMEOUT_S, connect=30))
        except httpx.HTTPError as e:
            raise m.llm.CallFailed(f"{type(e).__name__}") from e
        if r.status_code == 402:
            raise m.llm.InsufficientCredits("OpenRouter: payment required")
        if r.status_code != 200:
            raise m.llm.CallFailed(f"HTTP {r.status_code}")
        d = r.json()
        ch = (d.get("choices") or [{}])[0]
        cost = (d.get("usage") or {}).get("cost")
        self.budget.add(cost)
        return m.llm.LLMResult((ch.get("message") or {}).get("content") or "", ch.get("finish_reason"), cost,
                               d.get("model"), d.get("provider"))


class OpenAIClient:
    """Visq's client interface on OpenAI's Responses API. Responses are not stored (store=false). Visq's prompts
    were tuned on another model, so questions may read differently; the engine label records the model."""

    def __init__(self, budget=None):
        s = get_settings()
        if not s.openai_api_key:
            raise VisqUnavailable("VISKOPIC_OPENAI_API_KEY is not set")
        self._s = s
        self._m = modules()
        self.budget = budget or self._m.llm.Budget(self._m.config.MAX_SPEND_PER_SESSION_USD)

    def chat(self, system, user, *, temperature, max_tokens, model=None):
        m = self._m
        self.budget.check()
        # No temperature: reasoning-capable OpenAI models reject it; reasoning is switched off instead.
        body = {"model": model or model_name(), "instructions": system, "input": user,
                "max_output_tokens": max_tokens, "store": False, "reasoning": {"effort": "none"}}
        try:
            r = httpx.post(f"{self._s.openai_base_url}/responses", json=body,
                           headers={"Authorization": f"Bearer {self._s.openai_api_key}"},
                           timeout=httpx.Timeout(m.config.CALL_TIMEOUT_S, connect=30))
        except httpx.HTTPError as e:
            raise m.llm.CallFailed(f"{type(e).__name__}") from e
        if r.status_code == 429:
            raise m.llm.CallFailed("OpenAI rate limit or quota reached (HTTP 429)")
        if r.status_code != 200:
            raise m.llm.CallFailed(f"HTTP {r.status_code}")
        d = r.json()
        text = "\n".join(c.get("text", "") for item in d.get("output", []) if item.get("type") == "message"
                         for c in item.get("content", []) if c.get("type") == "output_text")
        finish = "stop" if d.get("status") == "completed" else "length"
        return m.llm.LLMResult(text, finish, None, d.get("model"), "openai")


def set_client_factory(factory) -> None:
    """Tests install Visq's own offline FakeClient here."""
    global _client_factory
    _client_factory = factory


def _provider() -> str:
    return "openai" if get_settings().visq_provider.strip().lower() == "openai" else "openrouter"


def model_name() -> str:
    s = get_settings()
    if s.visq_model:
        return s.visq_model
    return s.visq_openai_model if _provider() == "openai" else modules().config.MODEL


def enabled() -> bool:
    s = get_settings()
    if not s.allow_external_ai:
        return False
    if _client_factory is not None:
        return True
    return bool(s.openai_api_key) if _provider() == "openai" else bool(s.openrouter_api_key)


def client():
    if not enabled():
        key = "VISKOPIC_OPENAI_API_KEY" if _provider() == "openai" else "VISKOPIC_OPENROUTER_API_KEY"
        raise VisqUnavailable(f"Visq is switched off (VISKOPIC_ALLOW_EXTERNAL_AI and {key})")
    if _client_factory:
        return _client_factory()
    return OpenAIClient() if _provider() == "openai" else OpenRouterClient()


def engine_label() -> str:
    m = modules()
    if _client_factory:
        model = "offline stand-in"
    elif _provider() == "openai":
        model = f"openai {model_name()}, no zero-retention guarantee"
    else:
        model = f"{model_name()} via OpenRouter, zero data retention"
    return f"visq {m.visq.PROMPT_VERSION} {model} ({VENDOR_COMMIT})"


def _quiet(*_args, **_kwargs) -> None:
    pass


# ------------------------------------------------------------------ main questions


@dataclass
class VisqQuestion:
    text: str
    qtype: str
    reviewer_note: str
    passage: str | None
    passage_start: int | None
    passage_end: int | None
    source: str = "visq"


def draft(essay: str, assignment: str | None, count: int) -> list[VisqQuestion]:
    """Visq 1: extract the argument structure, plan targets, write and check `count` questions."""
    m = modules()
    norm = m.text.normalise(essay)
    try:
        m.text.check_length(norm)
    except m.text.EssayError as e:
        raise VisqUnavailable(str(e)) from e
    c = client()
    try:
        graph = m.aroa.extract(c, norm, cache=False, log=_quiet)       # cache off: nothing written to disk
        out = m.questions.generate_questions(c, graph, norm, n=count, assignment=assignment or "", log=_quiet)
    except (m.llm.CallFailed, m.llm.BudgetExceeded, m.llm.InsufficientCredits) as e:
        raise VisqUnavailable(f"Visq could not write questions ({type(e).__name__})") from e
    result = []
    for q in out:
        span = ai.locate(essay, q.get("passage") or "")
        result.append(VisqQuestion(
            text=q["question"].strip(), qtype=q["qtype"], reviewer_note=q.get("good_answer_covers", "").strip(),
            passage=essay[span[0]:span[1]] if span else None,
            passage_start=span[0] if span else None, passage_end=span[1] if span else None))
    return result


# ------------------------------------------------------------------ follow-ups


def is_empty_answer(answer: str) -> bool:
    return modules().followups.is_empty_answer(answer)


def decide_followup(question: str, answer: str, passage: str | None, earlier_followups: list[str],
                    session_asked: list[str]) -> dict:
    """Visq's follow-up decision for one main question: {"action", "question", "why"}. At most one per question."""
    m = modules()
    try:
        return m.followups.decide(client(), question, answer, passage or "", [], list(earlier_followups),
                                  session_asked=list(session_asked))
    except (m.llm.CallFailed, m.llm.BudgetExceeded, m.llm.InsufficientCredits) as e:
        raise VisqUnavailable(f"Visq could not decide a follow-up ({type(e).__name__})") from e
