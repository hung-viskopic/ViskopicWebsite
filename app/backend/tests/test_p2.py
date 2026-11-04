"""Pipeline 2 (interchangeability) and the combined either-flags report.

The comparison essays come from offline writers here: no network. The scorer itself is the vendored, frozen
release, so these check the plumbing (comparison sets per assignment, re-analysis, evidence, isolation) and the
combined rule, not the model's accuracy.
"""
import hashlib
import random

import pytest

from viskopic.analysis import combined, interchangeability
from viskopic.analysis.base import PipelineResult

from .conftest import app_conn, owner_conn
from .test_intake import drain, reviewer_setup, upload

PROMPT = "Discuss whether cities should ban private cars from their centres. Use evidence and consider objections."

TOPIC = ["cities", "cars", "centres", "traffic", "buses", "pollution", "residents", "shops", "cycling", "deliveries",
         "councils", "commuters", "parking", "streets", "emissions", "fares", "pedestrians", "funding", "zones"]


def ai_like(seed: int, words: int = 360) -> str:
    """Generic, interchangeable prose: every opening fits every essay."""
    rng = random.Random(seed)
    out, n = [], 0
    while n < words:
        a, b = rng.sample(TOPIC, 2)
        s = f"Many experts agree that {a} and {b} are important factors for modern urban policy today."
        out.append(s)
        n += len(s.split())
    return " ".join(out)


def human_like() -> str:
    """An opening whose specific words (Ghent, loop, circulation plan) return in its own continuation."""
    s = ["Ghent introduced its circulation plan in 2017, splitting the centre into sectors joined only by a ring road.",
         "Drivers could no longer cross the old town directly, so short trips by car became slower than cycling.",
         "Shopkeepers in the Korenmarkt feared ruin, and the local press predicted empty streets within a year.",
         "That fear deserves to be taken seriously, because a ban that empties a centre has failed by its own terms.",
         "Yet the sector design in Ghent kept every street reachable, it only removed the through route.",
         "Footfall in the Korenmarkt rose in the first year, and the ring road carried the through traffic instead.",
         "The circulation plan therefore suggests that the shape of a restriction matters more than its strictness.",
         "A blunt ban on private cars, applied without sectors or a ring road, would not repeat the Ghent result.",
         "Critics of the plan point to longer journeys for disabled residents who rely on door to door trips.",
         "Ghent answered with permits for those residents, which kept the sectors closed to everyone else.",
         "The lesson I draw is that cities should copy the sectors and the permits, not just the headline ban.",
         "Without the ring road and the permits, the Korenmarkt shopkeepers would have been right to be afraid."]
    return " ".join(s)


def disjoint_opening() -> str:
    """An opening that shares nothing with its continuation, while the AI openings do: scores below 0."""
    opening = ["Mountain weather changes quickly above the tree line in early spring.",
               "Walkers carry maps, compasses and spare gloves for that reason.",
               "Guides check forecasts twice before leaving the valley huts."]
    rest = ai_like(7, 220)
    return " ".join(opening) + " " + rest


@pytest.fixture
def writers():
    interchangeability.set_writer_override({"gpt54mini": lambda prompt, seed: ai_like(seed)})
    yield
    interchangeability.set_writer_override(None)


def make_assignment(client, headers, course, prompt=PROMPT):
    r = client.post(f"/api/courses/{course}/assignments", headers=headers, json={"title": "Car-free centres",
                                                                                 "prompt": prompt})
    assert r.status_code == 201, r.text
    return r.json()


def test_vendored_p2_is_unchanged():
    for name, want in interchangeability.FILE_SHA256.items():
        path = interchangeability.VENDOR_DIR / "viskopic_p2" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == want
    assert interchangeability.p2().VERSION == "p2-interchangeability-v1" and interchangeability.p2().THRESHOLD == 0.0


def test_comparison_set_scoring_and_combined_report(client, tenant_factory, writers):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)

    # A submission uploaded before the comparison essays exist waits for them, then is analysed again.
    a = make_assignment(client, reviewer, course)
    assert a["comparison_status"] == "queued"
    r = upload(client, reviewer, course, student, "essay.txt", human_like().encode(), role="submission",
               provenance="unsupervised", assignment_id=a["id"])
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    drain()

    rep = client.get(f"/api/documents/{doc_id}/analysis", headers=viewer).json()
    p2 = next(x for x in rep["runs"] if x["pipeline"] == "interchangeability")
    assert p2["status"] == "completed", p2
    assert p2["outcome"] == "not_interchangeable" and p2["score"] > 0 and p2["threshold"] == 0.0
    assert p2["confidence"] == "low" and p2["details"]["families"] == ["gpt54mini"]
    assert p2["pipeline_version"] == "p2-interchangeability-v1+e99417a"
    kinds = [e["kind"] for e in p2["evidence"]]
    assert kinds == ["interchangeability", "comparison_coverage"]
    span = p2["evidence"][0]["spans"][0]
    text = client.get(f"/api/documents/{doc_id}/text", headers=reviewer).json()["text"]
    assert text[span["start"]:span["end"]].startswith("Ghent introduced its circulation plan")
    assert "Ghent" not in p2["evidence"][0]["statement"]          # statements are templates

    c = rep["combined"]
    assert c["status"] == "no_flag" and c["flagged_by"] == [] and c["pipelines_run"] == 1
    assert c["pipeline1_consistency"]["status"] == "unavailable"
    assert any("Pipeline 1 did not run" in n for n in c["coverage_notes"])
    assert any("used only gpt54mini" in n for n in c["coverage_notes"])
    assert c["interpretation_warning"].startswith("Evidence for a human reviewer only")

    # An interchangeable essay is flagged and review is recommended.
    r = upload(client, reviewer, course, student, "essay2.txt", disjoint_opening().encode(), role="submission",
               provenance="unsupervised", assignment_id=a["id"])
    drain()
    rep = client.get(f"/api/documents/{r.json()['id']}/analysis", headers=viewer).json()
    p2 = next(x for x in rep["runs"] if x["pipeline"] == "interchangeability")
    assert p2["outcome"] == "interchangeable" and p2["score"] < 0
    assert rep["combined"]["status"] == "review_recommended"
    assert rep["combined"]["flagged_by"] == ["pipeline2_interchangeability"]

    # The assignment list shows the set; only managers can rebuild it; the comparison essays never contain
    # student text, and another institution sees none of it.
    listed = client.get(f"/api/courses/{course}/assignments", headers=viewer).json()
    assert listed[0]["comparison_status"] == "ready" and listed[0]["comparison_families"] == ["gpt54mini"]
    assert client.post(f"/api/assignments/{a['id']}/comparison-set", headers=viewer).status_code == 403
    assert client.post(f"/api/assignments/{a['id']}/comparison-set", headers=reviewer).status_code == 202
    drain()
    with owner_conn() as c:
        statuses = sorted(r[0] for r in c.execute(
            "SELECT status FROM assignment_comparison_sets WHERE assignment_id = %s", (a["id"],)).fetchall())
        data = str(c.execute("SELECT data FROM assignment_comparison_sets WHERE status = 'ready' AND assignment_id = %s",
                             (a["id"],)).fetchone()[0])
    assert statuses == ["ready", "superseded"]
    assert "Ghent" not in data and "Korenmarkt" not in data
    other = tenant_factory("Other")
    assert client.post(f"/api/assignments/{a['id']}/comparison-set", headers=other.owner).status_code == 404
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (other.id,))
        assert c.execute("SELECT count(*) FROM assignment_comparison_sets").fetchone()[0] == 0
        c.execute("SELECT set_config('app.institution_id', '', false)")


def test_generation_off_and_unscorable(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)
    a = make_assignment(client, reviewer, course)
    assert a["comparison_status"] is None                       # generation switched off: nothing queued
    assert client.post(f"/api/assignments/{a['id']}/comparison-set", headers=reviewer).status_code == 409
    r = upload(client, reviewer, course, student, "essay.txt", human_like().encode(), role="submission",
               provenance="unsupervised", assignment_id=a["id"])
    drain()
    rep = client.get(f"/api/documents/{r.json()['id']}/analysis", headers=viewer).json()
    p2 = next(x for x in rep["runs"] if x["pipeline"] == "interchangeability")
    assert p2["status"] == "not_eligible" and "No AI comparison essays" in p2["reason"]
    assert rep["combined"]["status"] == "insufficient_evidence"


def test_failed_generation_is_recorded(client, tenant_factory):
    def broken(prompt, seed):
        raise RuntimeError("provider down")
    interchangeability.set_writer_override({"gpt54mini": broken})
    try:
        t = tenant_factory()
        course, _, reviewer, _ = reviewer_setup(client, t)
        a = make_assignment(client, reviewer, course)
        with owner_conn() as c:
            for _ in range(4):
                drain()
                c.execute("UPDATE jobs SET run_after = now() WHERE kind = 'build_comparison_set'")
        listed = client.get(f"/api/courses/{course}/assignments", headers=reviewer).json()
        assert listed[0]["comparison_status"] == "failed" and "provider down" in listed[0]["comparison_error"]
    finally:
        interchangeability.set_writer_override(None)


def _res(status, outcome=None, score=None, threshold=None, details=None):
    return PipelineResult(status=status, outcome=outcome, score=score, threshold=threshold, details=details or {})


def test_combined_rule_matches_the_model_team():
    p1_flag = _res("completed", "stylistically-unusual", 10.0, 15.698)
    p1_ok = _res("completed", "same-author-compatible", 20.0, 15.698)
    p2_flag = _res("completed", "interchangeable", -1.3, 0.0, {"families": ["gpt54mini"]})
    p2_ok = _res("completed", "not_interchangeable", 2.0, 0.0, {"families": ["gpt54mini", "qwen3_14b", "gemma3_27b"],
                                                                "all_families": True})
    off = _res("not_eligible")
    cases = [((p1_flag, p2_ok), "review_recommended", ["pipeline1_consistency"]),
             ((p1_ok, p2_flag), "review_recommended", ["pipeline2_interchangeability"]),
             ((p1_flag, p2_flag), "review_recommended", ["pipeline1_consistency", "pipeline2_interchangeability"]),
             ((p1_ok, p2_ok), "no_flag", []),
             ((off, p2_ok), "no_flag", []),
             ((off, off), "insufficient_evidence", [])]
    for (a, b), status, flags in cases:
        out = combined.combine(a, b)
        assert out.outcome == status and out.details["flagged_by"] == flags
        assert out.score is None                                # never an averaged score
        assert out.details["coverage_notes"][-1] == combined.ALWAYS_NOTE
    # Full three-family set: no "weaker set" note. A demonstration profile never counts as the student's style.
    assert not any("used only" in n for n in combined.combine(p1_ok, p2_ok).details["coverage_notes"])
    demo = combined.combine(p1_flag, p2_ok, demonstration_profile=True)
    assert demo.outcome == "no_flag" and demo.details["pipeline1_consistency"]["status"] == "unavailable"
