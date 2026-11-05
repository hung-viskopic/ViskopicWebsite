"""Visq 2: the model team's viva engine, run as the world-tree flow.

Visq is vendored unchanged in `vendor/visq_v2/visq/` (the modules the Visq 2 world-tree flow needs). The flow:
the essay is read as a tree of points and what it takes for granted; clarifying questions where the essay is
silent; a timed, typed extension (a what-if from outside the essay); then a short viva (3 questions, or 7 with
the full set) whose questions depend on the earlier answers, each with a worked example on request. A reviewer
note compares the wording of the answers with the essay's structure: notes, never scores.

This module is our side of the integration:

- checks the vendored files' SHA-256 before importing them,
- talks to OpenRouter through our own small HTTP client, with Visq's zero-data-retention provider setting
  on every call (Visq's own client reads a .env file and needs the openai package; ours reads our settings),
  or, with VISKOPIC_VISQ_PROVIDER=openai, to OpenAI's Responses API (store=false; zero retention is then up to
  the OpenAI organisation's own agreement, so the engine label says so),
- never lets Visq write to disk: Visq keeps its state in one JSON file per person; we subclass its `WorldViva`
  so the state lives in memory for one call and is stored encrypted by the caller (`viva_visq2_states`),
- runs every step that may call a model in the worker; the API only runs Visq's code-only steps.

The student never sees the reviewer note. Visq never rates an answer or decides anything about a person.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import get_settings
log = logging.getLogger("viskopic.viva.visq")

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "visq_v2"
VENDOR_COMMIT = "3d631d8"           # keep in step with vendor/visq_v2/VENDORED.md
FILE_SHA256 = {
    "__init__.py": "0e678bd88d8f0c598548716a779aed7e3c9b9225936b6fca3f9efb078cf678c3",
    "analysis.py": "7a0d23f41f3cca97aaea267f842e9b19223d0bdf1da475a9b6b36cf876edf773",
    "config.py": "1e6fd169c40895d32e401944ab6b75547adb614180396fb30347a18d702b9ef1",
    "llm.py": "7a337c7e7d944fc3b7637a3355579560aa885d61aca21fe2ac346b8fe66dedf2",
    "prompts.py": "220c21feeea200e09727566d181033b14a2a5424bd4991edff38a1f3cce4ddeb",
    "text.py": "8d06b38825a497f4dd8802cf613788a44d3436637b14dc2669daf0d276650d5f",
    "treealgo.py": "00231dd012832fd69f84806235f0ea0add5c476f078b264a3551985afda04647",
    "vivaplan.py": "0e4fbd202fa96f9272d882fd26e092b7234ff4a76e748027348b18edf257cfc5",
    "vivatext.py": "f8cba81f44817d11a0b40a422e0f4da9a138778e73f445a0e05cb4e26bdd3ed6",
    "worldalg.py": "ce7d68160bf5de7b936cd5b92395f7ee7f8f659ba545ea8b193d58b1acde2fb4",
    "worldviva.py": "e47fe7cde27d5293bbde945767434ac8f91b54930b56b43239f20183effd8421",
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
            from visq import config, llm, text, vivatext, worldviva

            _mods = type("VisqModules", (), dict(visq=visq, config=config, llm=llm, text=text, vivatext=vivatext,
                                                  worldviva=worldviva))
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
    """Tests install a client factory here (None, or a client; Visq 2 treats no client as offline)."""
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
    if _client_factory:
        model = "test client"
    elif _provider() == "openai":
        model = f"openai {model_name()}, no zero-retention guarantee"
    else:
        model = f"{model_name()} via OpenRouter, zero data retention"
    return f"visq {modules().visq.VERSION} world-tree, {model} ({VENDOR_COMMIT})"


def _quiet(*_args, **_kwargs) -> None:
    pass


# ------------------------------------------------------------------ Visq 2 world-tree flow

CODE = "viva"   # Visq keys its state by participant code; we keep one state per invitation, so any fixed code works


class VisqStateError(Exception):
    """Visq refused a step (e.g. an answer when no question is waiting)."""


_wv_class = None


def _worldviva(state: dict | None, offline: bool):
    """Visq's WorldViva with its file storage replaced by an in-memory state for this one call."""
    global _wv_class
    m = modules()
    if _wv_class is None:
        class DBWorldViva(m.worldviva.WorldViva):
            def __init__(self, st, factory):
                super().__init__(Path("/nonexistent-visq-state"), factory, VisqStateError, background=False)
                self._st = copy.deepcopy(st)

            def has(self, code):
                return self._st is not None

            def load(self, code):
                if self._st is None:
                    raise self.error("no Visq state")
                return copy.deepcopy(self._st)

            def save(self, code, st):
                self._st = json.loads(json.dumps(st))      # the same JSON round trip Visq's file storage does

            def delete(self, code):
                self._st = None

            def state(self):
                return copy.deepcopy(self._st)
        _wv_class = DBWorldViva
    return _wv_class(state, (lambda: None) if offline else _model_client)


def _model_client():
    """A model client for Visq 2, or None (Visq then works offline with its heuristics)."""
    return client() if enabled() else None


def prepare(essay: str, topic: str, extension_seconds: int, full: bool, offline: bool = False) -> dict:
    """Build the world-tree and the first step. May call the model; run it in the worker."""
    m = modules()
    try:
        m.text.check_length(m.text.normalise(essay))
    except m.text.EssayError as e:
        raise VisqUnavailable(str(e)) from e
    wv = _worldviva(None, offline)
    wv.start(CODE, essay, topic or "", extension_seconds, use_llm=not offline, full=full)
    st = wv.state()
    if st.get("status") == "failed":
        raise VisqUnavailable(st.get("error") or "Visq could not read the essay")
    return st


def draft(state: dict, essay: str, offline: bool = False) -> dict:
    """What the reviewer previews before sending: the exact clarifying questions, and a draft of the extension and
    the viva questions worked out on a copy of the state as if the student gave no clarifying answers. In the viva
    Visq writes those two again from the student's own answers, so they may change. May call the model (the what-if
    and the paraphrases); run it in the worker. The prepared state itself is not changed."""
    wv = _worldviva(state, offline)
    st = wv.state()
    clarify = [{"text": c["prompt"]} for c in st.get("clarify", [])]
    if st.get("status") == "clarify":
        wv._make_extension(st, essay)            # Visq's own step, on our copy only
    if st.get("status") == "ext_intro":
        wv._make_plan(st)
    ext = st.get("ext") or {}
    plan = st.get("plan") or {}
    return {
        "clarify": clarify,
        "extension": {"prompt": ext.get("prompt") or ext.get("none") or "", "scenario": bool(ext.get("scenario")),
                      "available": bool(ext.get("prompt"))},
        "viva": [{"text": i["prompt"], "type": i["type"]} for i in plan.get("items", [])],
        "insufficient": bool(plan.get("insufficient")),
        "insufficient_reason": plan.get("reason") or "",
        "model_used": bool(st.get("llm")),
    }


def view(state: dict) -> dict:
    """What the student should see now (Visq's own view of its state)."""
    return _worldviva(state, offline=True).view(CODE)


def start_extension(state: dict) -> dict:
    wv = _worldviva(state, offline=True)
    wv.start_extension(CODE)
    return wv.state()


def take_answer(state: dict, stage: str, text: str, essay: str, offline: bool = False) -> dict:
    """Feed one answer into the flow. After the last clarifying answer and after the extension Visq may call the
    model (the what-if and the viva questions), so this runs in the worker; offline=True is the quick fallback."""
    wv = _worldviva(state, offline)
    if stage == "extension":
        wv.submit_extension(CODE, text, essay)
    else:
        wv.answer(CODE, text, essay)
    return wv.state()


def example(state: dict, n: int) -> tuple[dict, dict]:
    """The worked example for the current question (a different topic), and the state with the press counted."""
    wv = _worldviva(state, offline=True)
    ex = wv.example(CODE, n)
    return ex, wv.state()


def note(state: dict, essay: str) -> str:
    """Visq's reviewer note (markdown). Reviewer only; computed when read, never stored."""
    return _worldviva(state, offline=True).note(CODE, essay)


def texts() -> dict:
    vt = modules().vivatext
    return {"warning": vt.WARNING, "ext_note": vt.EXT_NOTE, "viva_start": vt.VIVA_START}
