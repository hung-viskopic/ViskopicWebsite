"""Small OpenRouter client with a spend limit, plus a JSON helper that retries on bad answers."""
import json
import os
import re
from dataclasses import dataclass

from . import config


class CallFailed(Exception):
    """The call failed after retries, or the answer was unusable."""


class InsufficientCredits(Exception):
    """HTTP 402 from OpenRouter: top up before continuing."""


class BudgetExceeded(Exception):
    """The spend limit for this command was reached."""


@dataclass
class LLMResult:
    text: str
    finish_reason: str | None
    cost_usd: float | None = None
    model_served: str | None = None
    provider: str | None = None

    @property
    def ok(self) -> bool:
        return self.finish_reason == "stop" and bool(self.text and self.text.strip())


class Budget:
    def __init__(self, limit=None):
        self.limit = config.MAX_SPEND_PER_SESSION_USD if limit is None else limit
        self.spent = 0.0

    def check(self):
        if self.spent >= self.limit:
            raise BudgetExceeded(f"spend limit ${self.limit:.2f} reached (spent ${self.spent:.4f})")

    def add(self, cost):
        self.spent += float(cost or 0)


def _extra(obj, name):
    if obj is None:
        return None
    v = getattr(obj, name, None)
    if v is None and getattr(obj, "model_extra", None):
        v = obj.model_extra.get(name)
    return v


class OpenRouterClient:
    def __init__(self, api_key=None, budget=None):
        from dotenv import load_dotenv
        import httpx
        from openai import OpenAI
        load_dotenv(config.ROOT / ".env")
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set. Put it in .env in the project folder.")
        if config.REQUIRE_ZDR and config.PROVIDER_PREFS.get("zdr") is not True:
            raise SystemExit("Refusing to run: config.REQUIRE_ZDR is on but PROVIDER_PREFS has no {'zdr': True}.")
        self.budget = budget or Budget()
        self.client = OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=key, max_retries=3,
                             timeout=httpx.Timeout(config.CALL_TIMEOUT_S, connect=30))

    def chat(self, system, user, *, temperature, max_tokens, model=None) -> LLMResult:
        import openai
        self.budget.check()
        extra = {"provider": config.PROVIDER_PREFS}
        if config.REASONING_EFFORT:
            extra["reasoning"] = {"effort": config.REASONING_EFFORT, "exclude": True}
        try:
            r = self.client.chat.completions.create(
                model=model or config.MODEL, temperature=temperature, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                extra_body=extra)
        except openai.APIStatusError as e:
            if e.status_code == 402:
                raise InsufficientCredits(str(e)) from e
            raise CallFailed(f"HTTP {e.status_code}: {e}") from e
        except openai.APIError as e:
            raise CallFailed(f"{type(e).__name__}: {e}") from e
        ch = r.choices[0]
        cost = _extra(getattr(r, "usage", None), "cost")
        self.budget.add(cost)
        return LLMResult(ch.message.content or "", ch.finish_reason, cost, getattr(r, "model", None),
                         _extra(r, "provider"))


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in the answer")
    d = json.loads(t[a:b + 1])
    if not isinstance(d, dict):
        raise ValueError("JSON is not an object")
    return d


def ask_json(client, system, user, validate, *, temperature, max_tokens, tries=None, on_try=None):
    """Ask, parse, validate. `validate(d)` returns None when fine or a short reason string; the reason is
    fed back to the model on the next try. Raises CallFailed when every try fails."""
    tries = tries or config.WRITE_TRIES
    note, last = "", "no attempt"
    for i in range(tries):
        if on_try:
            on_try(i, tries)
        res = client.chat(system, user + note, temperature=temperature, max_tokens=max_tokens)
        if not res.ok:
            last = f"incomplete answer (finish_reason={res.finish_reason})"
            continue
        try:
            d = parse_json(res.text)
        except (ValueError, json.JSONDecodeError) as e:
            last = f"bad JSON: {e}"
            note = f"\n\nYour previous answer was not valid JSON ({e}). Return ONLY the JSON object."
            continue
        problem = validate(d)
        if problem is None:
            return d
        last = problem
        note = f"\n\nYour previous answer was rejected: {problem}. Fix that and return ONLY the JSON object."
    raise CallFailed(last)