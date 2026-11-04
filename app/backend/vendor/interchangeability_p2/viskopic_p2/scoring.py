"""Pipeline 2 (interchangeability) scoring, frozen release v1. Python standard library only.

Idea: in a human essay the opening sets up what follows, so its specific (rare) words return in its
own continuation. AI essays written to the same assignment are near-interchangeable: their openings
fit any of them. The score measures how much more the essay's opening shares with its own
continuation than AI-written openings on the same assignment do. Higher = more human-like.

  family score = idf_overlap(A, B+C) - mean_k idf_overlap(A_k, B+C)
  P2 score     = minimum over comparison families of the family score
A, B, C are the essay's sentence-aligned thirds after cutting it to its first 250 words; A_k is the
opening third of the k-th AI comparison essay (first K = 4 per family, same cut). idf is computed over
the sections of the candidate and of the comparison essays used.

The splitter, length cut and stop list are copied verbatim from the evaluated research code
(cross_essay_v02/cross_essay.py, ghostbuster_eval/common_gb.py, cross_essay_v02/stages/word_overlap.py).
"""
from __future__ import annotations

import math
import re
from collections import Counter

VERSION = "p2-interchangeability-v1"
L_WORDS = 250
K_PER_FAMILY = 4
FAMILIES = ("gpt54mini", "qwen3_14b", "gemma3_27b")
# Provisional decision point: an opening no more tied to its own continuation than AI openings are.
# Measured one essay at a time with all three families (PERSUADE: 11% of human essays flagged, 48% of AI;
# Ghostbuster: 9% and 48%). Chosen after seeing those data; confirm on fresh data before relying on it.
THRESHOLD = 0.0

STOP = set("""a about above after again against all also although am an and any are as at be because been before
being below between both but by can could did do does doing down during each either few for from further had has
have having he her here hers him his how however i if in into is it its itself just may might more most must my no
nor not of off on once only or other our ours out over own same she should so some such than that the their theirs
them then there these they this those through thus to too under until up upon very was we were what when where
which while who whom why will with within without would you your yours one two first second also therefore
whether often many much rather""".split())


def sentence_units(text):
    # No partial sentence trimming. This is heuristic English segmentation.
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"“‘(])', text.strip()) if s.strip()]


def split_three(text):
    units = sentence_units(text)
    if len(units) < 9:
        raise ValueError('Fewer than nine sentence-like spans')
    lengths = [len(s.split()) for s in units]
    cumulative = [0]
    for n in lengths:
        cumulative.append(cumulative[-1] + n)
    total = cumulative[-1]
    i = min(range(1, len(units) - 1), key=lambda k: (abs(cumulative[k] - total / 3), k))
    j = min(range(i + 1, len(units)), key=lambda k: (abs(cumulative[k] - 2 * total / 3), k))
    chunks = [' '.join(units[a:b]) for a, b in ((0, i), (i, j), (j, len(units)))]
    if any(not .22 <= len(c.split()) / total <= .45 for c in chunks):
        raise ValueError('A sentence-aligned third is outside 22–45% of document length')
    return chunks


def truncate(text, L=L_WORDS):
    """First L words, cut back to the last complete sentence."""
    out, count = [], 0
    for sentence in sentence_units(" ".join(text.split())):
        n = len(sentence.split())
        if count + n > L:
            break
        out.append(sentence)
        count += n
    return " ".join(out)


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
    """Acceptance rule for a comparison essay: at least L words and splittable after the cut."""
    if len(text.split()) < L:
        return f"{len(text.split())} words < {L}"
    try:
        split_three(truncate(text, L))
    except ValueError as exc:
        return str(exc)
    return None


def score_essay(text, comparisons, L=L_WORDS, k=K_PER_FAMILY):
    """Score one essay against comparison essays for its assignment.

    comparisons: {family: [comparison essay texts in a fixed order]}; the first k of each are used.
    Returns a dict with "score" (None when unscorable), per-family scores, the families used,
    whether the configuration is one that was evaluated, and "flag" (score < THRESHOLD).
    """
    try:
        sections = split_three(truncate(text, L))
    except ValueError as exc:
        return {"version": VERSION, "status": "unscorable", "reason": f"candidate: {exc}", "score": None, "flag": None}
    used = {}
    for family, texts in sorted(comparisons.items()):
        chosen = list(texts)[:k]
        if len(chosen) < k:
            return {"version": VERSION, "status": "unscorable", "score": None, "flag": None,
                    "reason": f"family {family}: {len(chosen)} comparison essays, {k} required"}
        try:
            used[family] = [split_three(truncate(t, L)) for t in chosen]
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
