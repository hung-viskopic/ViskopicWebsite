"""Consistency analysis: one person's essay, typed extension and viva, read together, with a report a human can read and cite.

Two model steps: (1) list the person's commitments, each with an exact quote and the label of where it came from
(E essay sentence, X extension answer, V viva answer); (2) find commitments that contradict each other, pull against each
other, or show clearly different depth. CODE then checks every quote against the source text and drops any commitment whose
quote is not really there, so every citation in the report is verbatim and can be found by its label. Findings are notes
for a human reviewer, never a verdict about the person (a contradiction can be innocent: a misread question, a changed
mind). Style is deliberately not analysed: the Alpha research found no usable signal in it."""
import json
import re

from . import config
from .llm import CallFailed, ask_json
from .prompts import COMMIT_SYSTEM, COMPARE_SYSTEM
from .text import normalise, sentences

DISCLAIMER = ("This analysis was produced by a language model and then checked by code only for one thing: every quotation below "
              "really appears in the source text. It can miss inconsistencies and can flag things that are fine. It is NOT evidence of "
              "misconduct, and no decision may be based on it alone. A person must read the sources, ask the student, and decide.")
ANALYSIS_MAX_TOKENS = 4500
KINDS = ("contradiction", "tension", "depth_gap")
SEVERITIES = ("low", "medium", "high")


def _norm(t: str) -> str:
    t = (t or "").casefold().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return " ".join(re.sub(r"[^a-z0-9']+", " ", t).split())


def build_sources(essay: str, extension: list[dict], session: dict) -> dict:
    """label -> {"kind", "context", "text"}; `text` is the quotable part. E{paragraph}.{sentence}, X{n}, V{n}."""
    src = {}
    for pi, para in enumerate([p for p in normalise(essay).split("\n\n") if p.strip()], start=1):
        for si, sent in enumerate(sentences(para), start=1):
            src[f"E{pi}.{si}"] = {"kind": "essay", "context": "", "text": sent}
    for a in extension or []:
        if (a.get("answer") or "").strip():
            src[f"X{a['index'] + 1}"] = {"kind": "extension", "context": a["question"], "text": a["answer"].strip()}
    for t in session.get("turns", []):
        if t.get("skipped") or t.get("clarification_requested") or not (t.get("answer") or "").strip():
            continue
        src[f"V{t['index'] + 1}"] = {"kind": "viva", "context": t["question"], "text": t["answer"].strip()}
    return src


def _render(src: dict) -> str:
    out = []
    for lab, s in src.items():
        out.append(f"[{lab}] " + (f"PROMPT: {s['context']} ANSWER: " if s["kind"] == "extension"
                                   else f"QUESTION: {s['context']} ANSWER: " if s["kind"] == "viva" else "") + s["text"])
    return "\n".join(out)


def verify_commitments(commitments: list[dict], src: dict) -> tuple[list[dict], int]:
    """Keep a commitment only if its label exists and its quote is really inside that item's quotable text."""
    kept, dropped, seen = [], 0, set()
    for c in commitments:
        lab, q = str(c.get("label", "")).strip(), str(c.get("quote", "")).strip()
        cid = str(c.get("id", "")).strip()
        if lab not in src or not q or not cid or cid in seen or _norm(q) not in _norm(src[lab]["text"]) or len(_norm(q).split()) < 3:
            dropped += 1
            continue
        seen.add(cid)
        kept.append({"id": cid, "label": lab, "quote": q, "position": str(c.get("position", "")).strip(),
                     "condition": str(c.get("condition", "") or "").strip()})
    return kept, dropped


def verify_findings(findings: list[dict], commitments: list[dict]) -> tuple[list[dict], int]:
    by_id = {c["id"]: c for c in commitments}
    kept, dropped = [], 0
    for f in findings:
        ids = [str(i) for i in f.get("commitments", [])]
        cs = [by_id[i] for i in ids if i in by_id]
        if f.get("kind") not in KINDS or len(cs) < 2 or len(cs) != len(ids) or len({c["label"] for c in cs}) < 2:
            dropped += 1
            continue
        if any(c["label"][0] == "E" for c in cs) and any(c.get("condition") for c in cs):
            dropped += 1                                    # a conditional answer cannot contradict the essay's own case
            continue
        labels = [c["label"][0] for c in cs]
        if "V" not in labels and labels.count("X") < 2:     # essay vs extension is a change of mind under a new situation, never a finding
            dropped += 1
            continue
        kept.append({"kind": f["kind"], "severity": f.get("severity") if f.get("severity") in SEVERITIES else "medium",
                     "commitments": cs, "explanation": str(f.get("explanation", "")).strip(),
                     "innocent": [str(x).strip() for x in (f.get("innocent") or [])][:3], "ask": str(f.get("ask", "")).strip()})
    return kept, dropped


def analyse(client, essay: str, extension: list[dict], session: dict, log=print) -> dict:
    """Returns {"sources", "commitments", "findings", "dropped_commitments", "dropped_findings", "labels_used"}."""
    src = build_sources(essay, extension, session)
    if not any(s["kind"] == "extension" for s in src.values()) or not any(s["kind"] == "viva" for s in src.values()):
        raise CallFailed("needs at least one extension answer and one viva answer")
    rendered = _render(src)

    def ok_commit(d):
        return None if isinstance(d.get("commitments"), list) and d["commitments"] else "commitments must be a non-empty list"
    d1 = ask_json(client, COMMIT_SYSTEM, f"<material>\n{rendered}\n</material>", ok_commit, temperature=0,
                  max_tokens=ANALYSIS_MAX_TOKENS)
    commits, dropped_c = verify_commitments(d1["commitments"], src)
    if len(commits) < 2:
        return {"sources": src, "commitments": commits, "findings": [], "dropped_commitments": dropped_c, "dropped_findings": 0}
    lines = "\n".join(f"{c['id']} [{c['label']}] \"{c['quote']}\"  =>  {c['position']}" + (f"  (condition: {c['condition']})" if c["condition"] else "")
                      for c in commits)

    def ok_find(d):
        return None if isinstance(d.get("findings"), list) else "findings must be a list (empty if there are none)"
    d2 = ask_json(client, COMPARE_SYSTEM, f"<commitments>\n{lines}\n</commitments>", ok_find, temperature=0,
                  max_tokens=ANALYSIS_MAX_TOKENS)
    finds, dropped_f = verify_findings(d2["findings"], commits)
    order = {"high": 0, "medium": 1, "low": 2}
    finds.sort(key=lambda f: (order[f["severity"]], f["kind"]))
    return {"sources": src, "commitments": commits, "findings": finds, "dropped_commitments": dropped_c, "dropped_findings": dropped_f}


def summary(result: dict) -> dict:
    """Counts and a simple score for one analysis: high 3, medium 2, low 1 per finding (a screening number for comparing groups
    in a pilot, not a measure of anything about a person)."""
    finds = result.get("findings", [])
    w = {"high": 3, "medium": 2, "low": 1}
    return {"findings": len(finds), "contradiction": sum(1 for f in finds if f["kind"] == "contradiction"),
            "tension": sum(1 for f in finds if f["kind"] == "tension"), "depth_gap": sum(1 for f in finds if f["kind"] == "depth_gap"),
            "high": sum(1 for f in finds if f["severity"] == "high"), "score": sum(w.get(f["severity"], 1) for f in finds)}


def _sort_label(lab):
    m = re.match(r"([EXV])(\d+)(?:\.(\d+))?", lab)
    return ({"E": 0, "X": 1, "V": 2}[m.group(1)], int(m.group(2)), int(m.group(3) or 0))


def markdown(result: dict, session: dict) -> str:
    src, finds = result["sources"], result["findings"]
    n = {k: sum(1 for c in result["commitments"] if c["label"][0] == k) for k in "EXV"}
    out = [f"# Consistency analysis: {session['session_id']}", "", f"> {DISCLAIMER}", "",
           f"- Participant code: `{session['participant']}`   Essay: `{session['essay_id']}`   Level: {session.get('level') or '-'}"
           + ("   (audit: a low-level essay given the full set)" if session.get("audit") else ""),
           f"- Commitments found and verified: {len(result['commitments'])} (essay {n['E']}, extension {n['X']}, viva {n['V']}); "
           f"{result['dropped_commitments']} dropped because their quote was not in the source",
           f"- Findings: {len(finds)} ({', '.join(f'{k} {sum(1 for f in finds if f['kind'] == k)}' for k in KINDS)})"
           + (f"; {result['dropped_findings']} dropped (cited a missing commitment or a single source)" if result["dropped_findings"] else ""),
           "- Cite by label, e.g. [X1] or [V3]: all labels are listed with their full text in the appendix.", ""]
    if not finds:
        out += ["## No inconsistencies found", "",
                "The model found nothing that a careful reader would want to look at. That does not prove the sources agree: it "
                "only means this check found nothing.", ""]
    for i, f in enumerate(finds, start=1):
        out += [f"## F{i}. {f['kind'].replace('_', ' ')} (severity: {f['severity']})", ""]
        for c in sorted(f["commitments"], key=lambda c: _sort_label(c["label"])):
            out += [f"- **[{c['label']}]** \u201c{c['quote']}\u201d"]
        out += ["", f"**How they do not fit:** {f['explanation']}", ""]
        if f["innocent"]:
            out += ["**Ordinary reasons this could be innocent:** " + "; ".join(f["innocent"]), ""]
        if f["ask"]:
            out += [f"**A neutral question a person could ask the student:** {f['ask']}", ""]
        out += ["Reviewer judgement (agree / disagree / unsure): ____   Note: ____", ""]
    out += ["## Appendix: the sources, by label", ""]
    for lab in sorted(src, key=_sort_label):
        s = src[lab]
        head = {"essay": "", "extension": f"_(extension prompt: {s['context']})_ ", "viva": f"_(question: {s['context']})_ "}[s["kind"]]
        out += [f"**[{lab}]** {head}{s['text']}", ""]
    return "\n".join(out)


def save(result: dict, session: dict, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{session['session_id']}.md").write_text(markdown(result, session), encoding="utf-8")
    (out_dir / f"{session['session_id']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")