"""Pipeline 2 — interchangeability, using the frozen release from VISKOPIC_PROJECT (`feat/combined-p1-p2`).

The scorer is vendored unchanged in `vendor/interchangeability_p2/viskopic_p2/` (hash-checked before import).

What it measures: in a human essay the opening sets up what follows, so its rarer words come back later. AI
essays written to the same assignment are close to interchangeable: their openings would fit any of them. The
score is how much more the essay's opening (first third of its first 250 words) shares with its own
continuation than the openings of AI essays on the same assignment do. Below 0 the essay is "interchangeable".

What it needs: a comparison set per assignment, generated once from the assignment prompt only. No student
writing is sent anywhere; scoring is local. It does not need the student's baseline, so it gives a result
for students who have no profile yet.

How it is presented (the model team's rules): evidence for a reviewer, never a probability, never acted on
automatically. The threshold of 0 is provisional. GPT-only comparison sets are weaker than the evaluated
three-family set and are labelled so. Statements below come from templates.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import threading
from pathlib import Path
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..audit import record
from ..config import get_settings
from ..db import tenant_tx
from .base import AnalysisContext, EvidenceItem, PipelineResult, Span

log = logging.getLogger("viskopic.analysis.p2")

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "interchangeability_p2"
VENDOR_COMMIT = "e99417a"           # keep in step with vendor/interchangeability_p2/VENDORED.md
FILE_SHA256 = {
    "__init__.py": "b5a637b77931c9255d77c4f692d0873753fc1605d90bede9fa392cd0fde276d5",
    "comparisons.py": "db74fa308b98c8229318b33b88ed74f0a14bd2bc5fb897d955853ff1a2a0210c",
    "scoring.py": "14b0a8cc1cd6a652b99e395389b9f34daccd9c66336934233514585df4db4e5e",
}
OLLAMA_FAMILIES = ("qwen3_14b", "gemma3_27b")   # need p2_ollama_url; the evaluated set is these plus gpt54mini

_lock = threading.Lock()
_p2 = None
_writer_override = None


def p2():
    global _p2
    with _lock:
        if _p2 is None:
            pkg = VENDOR_DIR / "viskopic_p2"
            for name, want in FILE_SHA256.items():
                got = hashlib.sha256((pkg / name).read_bytes()).hexdigest()
                if got != want:
                    raise RuntimeError(f"vendored viskopic_p2/{name} has SHA-256 {got}, expected {want}; "
                                       "re-copy it unchanged from the model repository")
            if str(VENDOR_DIR) not in sys.path:
                sys.path.insert(0, str(VENDOR_DIR))
            import viskopic_p2

            _p2 = viskopic_p2
        return _p2


def pipeline_version() -> str:
    return f"{p2().VERSION}+{VENDOR_COMMIT}"


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ comparison sets


def set_writer_override(writers: dict | None) -> None:
    """Tests install offline writers ({family: write(prompt, seed) -> text})."""
    global _writer_override
    _writer_override = writers


def generation_enabled() -> bool:
    s = get_settings()
    return _writer_override is not None or (s.p2_generate_comparisons and bool(s.openai_api_key or s.p2_ollama_url))


def _writers() -> dict:
    """The model team's frozen writers for each family we can reach: GPT with the OpenAI key, Qwen and Gemma
    with an Ollama server. Whatever is missing is simply absent, and the report says the set is weaker."""
    if _writer_override is not None:
        return dict(_writer_override)
    s = get_settings()
    writers = {}
    if s.openai_api_key:
        writers["gpt54mini"] = p2().openai_writer(s.openai_api_key)
    if s.p2_ollama_url:
        for family in OLLAMA_FAMILIES:
            writers[family] = p2().ollama_writer(family, s.p2_ollama_url)
    return writers


def queue_comparison_set(conn: Connection, institution_id: UUID, assignment_id: UUID, user_id: UUID | None,
                         replace: bool = False) -> UUID | None:
    """Queue a comparison set for the assignment's current prompt. Returns its id, or None when there is
    nothing to do (no prompt, generation off, or an up-to-date set already exists)."""
    a = conn.execute("SELECT prompt FROM assignments WHERE id = %s", (assignment_id,)).fetchone()
    if a is None or not (a["prompt"] or "").strip() or not generation_enabled():
        return None
    digest = prompt_sha256(a["prompt"])
    active = conn.execute("SELECT id, status, prompt_sha256 FROM assignment_comparison_sets WHERE assignment_id = %s "
                          "AND status IN ('queued','building','ready') FOR UPDATE", (assignment_id,)).fetchone()
    if active and active["prompt_sha256"] == digest and not replace:
        return None
    if active:
        if active["status"] == "building":
            return None
        conn.execute("UPDATE assignment_comparison_sets SET status = 'superseded' WHERE id = %s", (active["id"],))
    families = sorted(_writers())
    sid = conn.execute(
        "INSERT INTO assignment_comparison_sets (institution_id, assignment_id, prompt_sha256, families, created_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id", (institution_id, assignment_id, digest, families, user_id)).fetchone()["id"]
    conn.execute("INSERT INTO jobs (institution_id, kind, comparison_set_id) VALUES (%s, 'build_comparison_set', %s)",
                 (institution_id, sid))
    return sid


def run_build_comparison_set(job: dict) -> None:
    from .service import JobError, queue_analysis   # service imports this module

    iid, sid = job["institution_id"], job["comparison_set_id"]
    with tenant_tx(iid) as conn:
        row = conn.execute(
            "UPDATE assignment_comparison_sets s SET status = 'building' FROM assignments a "
            "WHERE s.id = %s AND a.id = s.assignment_id AND s.status IN ('queued','building') "
            "RETURNING s.assignment_id, s.prompt_sha256, a.prompt", (sid,)).fetchone()
        if row is None:
            raise JobError("comparison set no longer needed", permanent=True)
    if prompt_sha256(row["prompt"] or "") != row["prompt_sha256"]:
        raise JobError("the assignment prompt changed; a new set is needed", permanent=True)
    if not generation_enabled():
        raise JobError("comparison essay generation is switched off (VISKOPIC_P2_GENERATE_COMPARISONS)",
                       permanent=True)
    try:
        data = p2().build_comparison_set(row["prompt"], _writers())
    except Exception as e:  # provider errors, or no acceptable essay after the frozen two attempts
        raise JobError(f"could not generate comparison essays: {type(e).__name__}: {e}"[:300]) from e
    with tenant_tx(iid) as conn:
        current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
        if current is None or current["attempts"] != job["attempts"]:
            return
        ok = conn.execute(
            "UPDATE assignment_comparison_sets SET status = 'ready', data = %s, generator = %s, ready_at = now(), "
            "error = NULL WHERE id = %s AND status = 'building' RETURNING id",
            (Jsonb(data), pipeline_version(), sid)).fetchone()
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                     (job["id"],))
        if not ok:
            return
        cases = conn.execute(
            "SELECT c.id FROM cases c JOIN documents d ON d.id = c.document_id WHERE d.assignment_id = %s "
            "AND c.status IN ('awaiting_baseline','awaiting_review','evidence_ready','failed')",
            (row["assignment_id"],)).fetchall()
        for c in cases:
            queue_analysis(conn, iid, c["id"])
        record(conn, None, "comparison_set.built", "assignment", row["assignment_id"],
               {"comparison_set_id": str(sid), "families": sorted(data["families"]), "generator": pipeline_version(),
                "cases_requeued": len(cases)}, institution_id=iid, actor_label="worker")


def on_build_failed(conn: Connection, job: dict, message: str) -> None:
    row = conn.execute("UPDATE assignment_comparison_sets SET status = 'failed', error = %s WHERE id = %s "
                       "AND status IN ('queued','building') RETURNING assignment_id",
                       (message, job["comparison_set_id"])).fetchone()
    record(conn, None, "comparison_set.failed", "assignment", row["assignment_id"] if row else None,
           {"comparison_set_id": str(job["comparison_set_id"]), "error": message},
           institution_id=job["institution_id"], actor_label="worker")


def load_ready_set(conn: Connection, document_id: UUID) -> tuple[dict | None, str | None, str]:
    """(comparison set, assignment prompt, why missing) for the submission's assignment."""
    a = conn.execute("SELECT a.id, a.prompt FROM documents d LEFT JOIN assignments a ON a.id = d.assignment_id "
                     "WHERE d.id = %s", (document_id,)).fetchone()
    if a is None or a["id"] is None:
        return None, None, "The submission is not linked to an assignment, so there are no AI comparison essays."
    if not (a["prompt"] or "").strip():
        return None, None, "The assignment has no prompt text, so no AI comparison essays could be made."
    s = conn.execute("SELECT status, data, error FROM assignment_comparison_sets WHERE assignment_id = %s "
                     "ORDER BY created_at DESC LIMIT 1", (a["id"],)).fetchone()
    if s is None:
        return None, a["prompt"], "No AI comparison essays have been generated for this assignment yet."
    if s["status"] in ("queued", "building"):
        return None, a["prompt"], "AI comparison essays for this assignment are being generated."
    if s["status"] != "ready":
        return None, a["prompt"], "Generating AI comparison essays for this assignment failed."
    return s["data"], a["prompt"], ""


# ------------------------------------------------------------------ pipeline


def _locate(text: str, passage: str) -> Span | None:
    words = passage.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return Span(m.start(), m.end(), "exact") if m else None


class InterchangeabilityPipeline:
    name = "interchangeability"
    category = "regeneration"

    @property
    def version(self) -> str:
        return pipeline_version()

    def run(self, ctx: AnalysisContext) -> PipelineResult:
        m = p2()
        if ctx.comparison_set is None:
            return PipelineResult(status="not_eligible", reason=ctx.comparison_missing_reason
                                  or "No AI comparison essays for this assignment.")
        try:
            comparisons = m.load_comparison_set(ctx.comparison_set, ctx.assignment_prompt)
        except (ValueError, KeyError) as e:
            return PipelineResult(status="not_eligible",
                                  reason=f"The stored AI comparison essays can't be used: {e}"[:300])
        res = m.score_essay(ctx.text, comparisons)
        families = sorted(comparisons)
        base = {"threshold": m.THRESHOLD, "families": families, "evaluated_families": list(m.FAMILIES),
                "provisional_threshold": True}
        if res["status"] != "scored":
            # About 1 in 5 essays by young writers are unscorable (too few sentences, run-on writing).
            return PipelineResult(status="not_eligible", details={**base, "p2_status": res["status"]},
                                  reason="This essay can't be scored for interchangeability: "
                                         f"{res['reason']}."[:300])
        flagged = bool(res["flag"])
        all_families = set(families) == set(m.FAMILIES)
        details = {**base, "p2_status": "scored", "family_scores": res["family_scores"],
                   "evaluated_configuration": res["evaluated_configuration"],
                   "candidate_words_used": res["candidate_words_used"], "all_families": all_families}
        evidence = self._evidence(ctx, res, flagged, all_families)
        return PipelineResult(
            status="completed", score=res["score"], threshold=m.THRESHOLD,
            outcome="interchangeable" if flagged else "not_interchangeable",
            confidence="medium" if all_families else "low", details=details, evidence=evidence)

    def _evidence(self, ctx: AnalysisContext, res: dict, flagged: bool, all_families: bool) -> list[EvidenceItem]:
        m = p2()
        spans: list[Span] = []
        try:
            opening = m.scoring.split_three(m.scoring.truncate(ctx.text))[0]
            span = _locate(ctx.text, opening)
            if span:
                spans.append(span)
        except ValueError:
            pass
        score = res["score"]
        if flagged:
            statement = ("The opening of the essay is no more tied to its own continuation than the openings of "
                         "AI-written essays on this assignment are (score {:.2f}, below 0). This pattern is typical "
                         "of AI essays, but some human essays show it too.").format(score)
        else:
            statement = ("The opening of the essay shares more of its specific words with its own continuation "
                         "than the openings of AI-written essays on this assignment do (score {:.2f}, above 0)."
                         ).format(score)
        items = [EvidenceItem(category="regeneration", kind="interchangeability", statement=statement,
                              observed=score, spans=spans,
                              details={"threshold": m.THRESHOLD, "family_scores": res["family_scores"]})]
        if not all_families:
            items.append(EvidenceItem(
                category="regeneration", kind="comparison_coverage",
                statement=(f"Only {', '.join(sorted(res['family_scores']))} comparison essays were used. The "
                           "evaluated setup uses GPT, Qwen and Gemma essays and is stronger against AI from other "
                           "models."),
                details={"families": sorted(res["family_scores"])}))
        return items
