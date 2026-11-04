"""The combined report for Δ (V1.3 consistency) and Pipeline 2 (interchangeability).

The rule and the wording are the model team's (`services/viskopic_combined/viskopic_combined/combined.py` in
VISKOPIC_PROJECT, `feat/combined-p1-p2`, commit e99417a). We re-implement it here instead of calling their
`CombinedScorer`, because that calls the V1.3 scorer directly and would skip the website adapter and its
quality-review gate. Our Δ result already went through the adapter; this only combines finished runs.

Rule ("either flags"): review is recommended when at least one pipeline that ran flags the essay. Scores are
never averaged: they measure different things on different scales. A recommendation routes a case to a person;
it is not a finding and nothing acts on it automatically.
"""
from __future__ import annotations

from .base import PipelineResult

SCHEMA = "viskopic-combined.v1"
VERSION = "combined-either-flags-v1+e99417a"
WARNING = ("Evidence for a human reviewer only. Not a probability and not proof of AI use, authorship, "
           "plagiarism or misconduct. A flag means: look at this essay more closely.")
ALWAYS_NOTE = "Neither pipeline detects a student's own draft that was only polished or edited by AI."


def _p1(res: PipelineResult | None) -> dict:
    if res is None or res.status != "completed":
        return {"status": "unavailable",
                "reason": (res.reason if res and res.reason else
                           "No writing profile for this student, or the texts are not ready to compare.")}
    if res.details.get("website_status") == "scored_demo_unreviewed" or res.details.get("demonstration"):
        return {"status": "unavailable", "reason": "Compared with a demonstration profile, not this student's writing."}
    flagged = res.outcome != "same-author-compatible"
    explanation = (f"Writing style differs from this student's earlier essays (score {res.score:.3f}, "
                   f"below {res.threshold:.3f})." if flagged
                   else "Writing style is consistent with this student's earlier essays.")
    return {"status": "flagged" if flagged else "not_flagged", "score": res.score, "threshold": res.threshold,
            "explanation": explanation}


def _p2(res: PipelineResult | None) -> dict:
    if res is None or res.status != "completed":
        return {"status": "unavailable", "reason": (res.reason if res and res.reason else
                                                    "No AI comparison essays for this assignment.")}
    flagged = res.outcome == "interchangeable"
    explanation = ("The essay's opening is no more tied to its own continuation than AI-written openings on this "
                   "assignment are, a pattern typical of AI essays." if flagged else
                   "The essay's parts depend on each other more than AI essays on this assignment do.")
    return {"status": "flagged" if flagged else "not_flagged", "score": res.score, "threshold": res.threshold,
            "explanation": explanation, "families": res.details.get("families", [])}


def _coverage_notes(p1: dict, p2: dict, all_families: bool) -> list[str]:
    notes = []
    if p1["status"] == "unavailable" and p2["status"] != "unavailable":
        notes.append("Pipeline 1 did not run: AI from a model not in the comparison set, or AI rewritten to sound "
                     "human, can pass Pipeline 2 alone.")
    if p2["status"] == "unavailable" and p1["status"] != "unavailable":
        notes.append("Pipeline 2 did not run: AI prompted to imitate the student's style can pass Pipeline 1 alone.")
    if p2["status"] != "unavailable" and not all_families:
        notes.append(f"Pipeline 2 used only {', '.join(p2['families'])} comparison essays; the evaluated "
                     "configuration uses all of gpt54mini, qwen3_14b, gemma3_27b and is stronger against other "
                     "AI models.")
    notes.append(ALWAYS_NOTE)
    return notes


def combine(consistency: PipelineResult | None, interchangeability: PipelineResult | None,
            demonstration_profile: bool = False) -> PipelineResult:
    p1 = _p1(consistency)
    if demonstration_profile and p1["status"] != "unavailable":
        p1 = {"status": "unavailable", "reason": "Compared with a demonstration profile, not this student's writing."}
    p2 = _p2(interchangeability)
    ran = [p for p in (p1, p2) if p["status"] != "unavailable"]
    flags = [name for name, p in (("pipeline1_consistency", p1), ("pipeline2_interchangeability", p2))
             if p["status"] == "flagged"]
    if flags:
        overall = "review_recommended"
        summary = " ".join(p["explanation"] for p in (p1, p2) if p["status"] == "flagged")
    elif ran:
        overall, summary = "no_flag", "No pipeline that ran raised a concern."
    else:
        overall, summary = "insufficient_evidence", "Neither pipeline could run on this submission."
    all_families = bool(interchangeability and interchangeability.details.get("all_families"))
    return PipelineResult(
        status="completed", outcome=overall,
        details={"schema": SCHEMA, "summary": summary, "flagged_by": flags, "pipelines_run": len(ran),
                 "pipeline1_consistency": p1, "pipeline2_interchangeability": p2,
                 "coverage_notes": _coverage_notes(p1, p2, all_families), "interpretation_warning": WARNING})
