"""Pipeline 2 (interchangeability) scoring, frozen release v1.1. Python standard library only.

Idea: in a human essay the opening sets up what follows, so its specific (rare) words return in its
own continuation. AI essays written to the same assignment are near-interchangeable: their openings
fit any of them. The score measures how much more the essay's opening shares with its own
continuation than AI-written openings on the same assignment do. Higher = more human-like.

  family score = idf_overlap(A, B+C) - mean_k idf_overlap(A_k, B+C)
  P2 score     = minimum over comparison families of the family score
A, B, C are three equal word thirds of the essay's first 250 words; A_k is the opening third of the
k-th AI comparison essay (first K = 4 per family, same cut). idf is computed over the sections of the
candidate and of the comparison essays used.

v1.1 replaces v1's sentence-aligned thirds (which needed >= 9 sentences in 250 words and rejected 44%
of university essays and 20% of school essays, but ~2% of AI essays) with word thirds. Validated on a
fresh, pre-registered PERSUADE sample (p2_v11_run_001): coverage 100%, AUROC 0.786 vs 0.780 for v1,
paired difference +0.001 (95% CI -0.009 to 0.010). The stop list is copied verbatim from the research
code (cross_essay_v02/stages/word_overlap.py).
"""
from __future__ import annotations

import math
import re
from collections import Counter

VERSION = "p2-interchangeability-v1.1"
L_WORDS = 250
MIN_WORDS = 150
K_PER_FAMILY = 4
FAMILIES = ("gpt54mini", "qwen3_14b", "gemma3_27b")
# Decision point: an opening no more tied to its own continuation than AI openings are. On the fresh
# PERSUADE sample (school essays, all three families) it flags 9.7% of human essays (95% CI 7.9-11.8%)
# and 40% of AI essays. Not validated for university essays: in a small BAWE check (24 essays, GPT
# comparisons only) 6/24 human essays fell below 0, and on Ghostbuster 19% did. Treat as experimental there.
THRESHOLD = 0.0

STOP = set("""a about above after again against all also although am an and any are as at be because been before
being below between both but by can could did do does doing down during each either few for from further had has
have having he her here hers him his how however i if in into is it its itself just may might more most must my no
nor not of off on once only or other our ours out over own same she should so some such than that the their theirs
them then there these they this those through thus to too under until up upon very was we were what when where
which while who whom why will with within without would you your yours one two first second also therefore
whether often many much rather""".split())


def word_thirds(text, L=L_WORDS):
    """First L whitespace-separated words (all of them if fewer), split into three equal word thirds."""
    words = text.split()[:L]
    n = len(words)
    if n < MIN_WORDS:
        raise ValueError(f"{n} words; at least {MIN_WORDS} required")
    a, b = round(n / 3), round(2 * n / 3)
    return [" ".join(words[:a]), " ".join(words[a:b]), " ".join(words[b:])]


def content_words(text):
    return {w for w in re.findall(r"[a-z][a-z'-]+", text.lower()) if w not in STOP and len(w) > 2}


def build_idf(sections):
    df = Counter(w for s in sections for w in content_words(s))
    return {w: math.log(len(sections) / c) for w, c in df.items()}


def overlap(a, b, idf):
    return sum(idf.get(w, 0.0) for w in content_words(a) & content_words(b))


def family_margin(sections, openings, idf):
    cont = sections[1] + " " + sections[2]
    return overlap(sections[0], cont, idf) - sum(overlap(o, cont, idf) for o in openings) / len(openings)


def comparison_ok(text, L=L_WORDS):
    """Acceptance rule for a comparison essay: at least L words."""
    n = len(text.split())
    return None if n >= L else f"{n} words < {L}"


def score_essay(text, comparisons, L=L_WORDS, k=K_PER_FAMILY):
    """Score one essay against comparison essays for its assignment.

    comparisons: {family: [comparison essay texts in a fixed order]}; the first k of each are used.
    Returns a dict with "score" (None when unscorable), per-family scores, the families used,
    whether the configuration is one that was evaluated, and "flag" (score < THRESHOLD).
    """
    try:
        sections = word_thirds(text, L)
    except ValueError as exc:
        return {"version": VERSION, "status": "unscorable", "reason": f"candidate: {exc}", "score": None, "flag": None}
    used = {}
    for family, texts in sorted(comparisons.items()):
        chosen = list(texts)[:k]
        if len(chosen) < k:
            return {"version": VERSION, "status": "unscorable", "score": None, "flag": None,
                    "reason": f"family {family}: {len(chosen)} comparison essays, {k} required"}
        try:
            used[family] = [word_thirds(t, L) for t in chosen]
        except ValueError as exc:
            return {"version": VERSION, "status": "unscorable", "score": None, "flag": None,
                    "reason": f"family {family}: comparison essay not usable ({exc})"}
    if not used:
        return {"version": VERSION, "status": "unscorable", "reason": "no comparison essays", "score": None, "flag": None}
    idf = build_idf(list(sections) + [x for v in used.values() for s in v for x in s])
    per_family = {f: family_margin(sections, [s[0] for s in v], idf) for f, v in used.items()}
    score = min(per_family.values())
    families = tuple(sorted(per_family))
    return {"version": VERSION, "status": "scored", "score": score, "flag": score < THRESHOLD, "threshold": THRESHOLD,
            "family_scores": per_family, "families": list(families),
            "evaluated_configuration": families in (tuple(sorted(FAMILIES)), ("gpt54mini",)),
            "candidate_words_used": sum(len(s.split()) for s in sections)}
