"""Generate and store the AI comparison essays Pipeline 2 needs, once per assignment.

Frozen generation settings (as evaluated): the prompt below with a 350-word target, 4 accepted essays
per family, up to 2 attempts each, acceptance = at least 250 words and splittable after the 250-word cut.
  gpt54mini  OpenAI Responses API, gpt-5.4-mini-2026-03-17, reasoning none, store false (OPENAI_API_KEY)
  qwen3_14b  Ollama qwen3:14b, thinking off, temperature 0.8 (OLLAMA_URL, default http://127.0.0.1:11434)
  gemma3_27b Ollama gemma3:27b, temperature 0.8
A comparison set is plain JSON; store it with the assignment and reuse it for every submission.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

from .scoring import FAMILIES, K_PER_FAMILY, L_WORDS, comparison_ok

SCHEMA = "viskopic-p2-comparison-set.v1"
PROMPT = ("Write an essay responding to the assignment below, about {n} words. Return only continuous essay prose, "
          "without a title, headings, lists, bibliography or commentary.\n\nASSIGNMENT:\n{assignment}")
TARGET_WORDS = 350
OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
OLLAMA_MODELS = {"qwen3_14b": "qwen3:14b", "gemma3_27b": "gemma3:27b"}


def _post(url, body, headers, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def openai_writer(api_key=None):
    key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    def write(prompt, seed):
        raw = _post("https://api.openai.com/v1/responses",
                    {"model": OPENAI_MODEL, "instructions": "Produce an independent essay for a controlled research comparison.",
                     "input": prompt, "reasoning": {"effort": "none"}, "max_output_tokens": 2500, "store": False},
                    {"Authorization": "Bearer " + key}, 240)
        if raw.get("model") != OPENAI_MODEL or raw.get("status") != "completed":
            raise ValueError("Unexpected model or incomplete response")
        return "\n".join(c.get("text", "") for item in raw.get("output", []) if item.get("type") == "message"
                         for c in item.get("content", []) if c.get("type") == "output_text")
    return write


def ollama_writer(family, base_url=None):
    url = (base_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    model = OLLAMA_MODELS[family]

    def write(prompt, seed):
        body = {"model": model, "stream": False, "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.8, "seed": seed, "num_ctx": 4096, "num_predict": 1200}}
        if family == "qwen3_14b":
            body["think"] = False
        raw = _post(url + "/api/chat", body, {}, 900)
        return re.sub(r"<think>.*?</think>", "", raw["message"]["content"], flags=re.S).strip()
    return write


def generate_family(assignment, family, write, k=K_PER_FAMILY, seed_base=0):
    """Return k accepted essays for one family; raises if any slot fails both attempts."""
    prompt = PROMPT.format(n=TARGET_WORDS, assignment=assignment)
    essays = []
    for slot in range(k):
        reasons = []
        for attempt in range(2):
            seed = seed_base + slot + 7919 * attempt
            try:
                text = write(prompt, seed)
                reason = comparison_ok(text, L_WORDS)
            except Exception as exc:  # network or provider errors count as a failed attempt
                text, reason = "", f"error: {exc}"
            if reason is None:
                essays.append({"text": text, "slot": slot, "attempt": attempt + 1, "seed": seed})
                break
            reasons.append(reason)
        else:
            raise RuntimeError(f"{family} slot {slot}: no acceptable essay after 2 attempts ({'; '.join(reasons)})")
    return essays


def build_comparison_set(assignment, writers, k=K_PER_FAMILY):
    """writers: {family: write(prompt, seed) -> text}. Families not given are simply absent."""
    unknown = set(writers) - set(FAMILIES)
    if unknown:
        raise ValueError(f"Unknown families: {sorted(unknown)}")
    return {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
            "assignment_sha256": hashlib.sha256(assignment.encode("utf-8")).hexdigest(),
            "prompt_template": PROMPT, "target_words": TARGET_WORDS, "k": k,
            "families": {f: generate_family(assignment, f, w, k) for f, w in writers.items()}}


def load_comparison_set(data, assignment=None):
    """Validate a stored set (dict or JSON string); returns {family: [texts]} for scoring."""
    if isinstance(data, str):
        data = json.loads(data)
    if data.get("schema") != SCHEMA:
        raise ValueError("Not a Pipeline 2 comparison set")
    if assignment is not None and hashlib.sha256(assignment.encode("utf-8")).hexdigest() != data["assignment_sha256"]:
        raise ValueError("Comparison set was generated for a different assignment text")
    out = {}
    for family, essays in data["families"].items():
        if family not in FAMILIES:
            raise ValueError(f"Unknown family {family}")
        out[family] = [e["text"] for e in sorted(essays, key=lambda e: e["slot"])]
    return out
