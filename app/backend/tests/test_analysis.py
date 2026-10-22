"""Baseline profiles, cases, the Δ consistency pipeline and the evidence report.

These run the real vendored V1.3 model (no mocks), on synthetic text: they check the plumbing,
security and evidence format, not the model's accuracy.
"""
import random
import re

import pytest

from viskopic.analysis import consistency, service
from viskopic.config import get_settings
from viskopic.storage import get_storage

from .conftest import app_conn, owner_conn
from .test_intake import drain, reviewer_setup, upload

VOCAB = ["the", "analysis", "would", "because", "however", "evidence", "carefully", "we", "and", "comparison",
         "it", "was", "a", "complex", "institutional", "relationship", "public", "observation", "is", "this",
         "important", "students", "policy", "argue", "therefore", "of", "in", "that", "suggests", "data"]


def essay(seed: int, words: int = 2300, sentence_len: int = 18, semicolons: bool = False) -> str:
    rng = random.Random(seed)
    sentences, total = [], 0
    while total < words:
        n = sentence_len + rng.randrange(6)
        tokens = rng.choices(VOCAB, weights=[1 + (i + seed) % 7 for i in range(len(VOCAB))], k=n)
        if semicolons:
            tokens.insert(n // 2, tokens.pop(n // 2) + ";")
        sentences.append(" ".join(tokens).capitalize() + ("?" if len(sentences) % 9 == 0 else "."))
        total += n
    return "\n\n".join(" ".join(sentences[i:i + 8]) for i in range(0, len(sentences), 8))


def report(client, headers, doc_id):
    r = client.get(f"/api/documents/{doc_id}/analysis", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def run(rep, pipeline):
    return next(r for r in rep["runs"] if r["pipeline"] == pipeline)


def review(client, headers, doc_id, language="english", extraction="pass"):
    return client.post(f"/api/documents/{doc_id}/quality-review", headers=headers,
                       json={"language": language, "extraction": extraction})


@pytest.fixture
def unreviewed_demo(monkeypatch):
    """Local-development mode: texts nobody has quality-reviewed are scored, labelled as such."""
    monkeypatch.setattr(get_settings(), "allow_unreviewed_consistency", True)


def test_enrolled_profile_and_evidence_report(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)
    baseline_ids = []
    for i in range(8):
        r = upload(client, reviewer, course, student, f"baseline{i}.txt", essay(100 + i).encode(),
                   provenance="supervised" if i < 2 else "unsupervised")
        assert r.status_code == 201, r.text
        baseline_ids.append(r.json()["id"])
    submission_text = essay(999, words=2400, sentence_len=34, semicolons=True)
    sub = upload(client, reviewer, course, student, "submission.txt", submission_text.encode(), role="submission")
    doc_id = sub.json()["id"]
    drain()

    # Without quality reviews the model's website contract refuses to score: no verdict, no number.
    assert get_settings().allow_unreviewed_consistency is False
    rep = report(client, reviewer, doc_id)
    assert rep["case"]["status"] == "awaiting_review"
    delta = run(rep, "consistency")
    assert delta["status"] == "not_eligible" and delta["score"] is None and delta["outcome"] is None
    assert delta["details"]["website_status"] == "quality_review_required"
    assert delta["details"]["quality"] == {"candidate": "unreviewed", "references": "unreviewed",
                                           "comparison_bank": "not_fully_language_audited"}
    assert "quality" in delta["reason"] and not delta["evidence"]

    # Reviews are for reviewers only, within the institution, and are bound to the stored text.
    assert review(client, viewer, doc_id).status_code == 403
    assert review(client, tenant_factory("Other").owner, doc_id).status_code == 404
    assert client.post(f"/api/documents/{doc_id}/quality-review", headers=reviewer,
                       json={"language": "english", "extraction": "unreviewed"}).status_code == 422
    for b in baseline_ids[:5]:
        assert review(client, reviewer, b).status_code == 201
    drain()   # (a queued case reads the latest reviews when it runs, so it isn't queued twice)
    progress = client.get(f"/api/students/{student}/profiles", headers=reviewer).json()["consistency"]
    assert progress["quality_passed_documents"] == 5 and progress["quality_problem_documents"] == 0
    r = review(client, reviewer, doc_id)
    assert r.status_code == 201 and r.json()["cases_requeued"] == 1
    drain()
    delta = run(report(client, reviewer, doc_id), "consistency")
    assert delta["details"]["quality"]["candidate"] == "passed"
    assert delta["details"]["quality"]["references"] == "unreviewed"   # 3 baseline documents still to go
    assert delta["details"]["website_status"] == "quality_review_required"

    for b in baseline_ids[5:]:
        assert review(client, reviewer, b).status_code == 201
    drain()
    detail = client.get(f"/api/documents/{doc_id}", headers=reviewer).json()
    assert detail["quality_review"]["current"] is True and detail["quality_review"]["language"] == "english"

    profiles = client.get(f"/api/students/{student}/profiles", headers=reviewer).json()
    ready = [p for p in profiles["profiles"] if p["status"] == "ready"]
    assert len(ready) == 1 and ready[0]["source"] == "enrolled", profiles
    assert ready[0]["reference_count"] == 8 and ready[0]["supervised_count"] == 2
    assert ready[0]["summary"]["background_documents"] == 635
    assert profiles["consistency"]["eligible_documents"] == 8

    # The profile artifact is encrypted at rest and its hash is recorded.
    with owner_conn() as c:
        key, sha = c.execute("SELECT artifact_storage_key, artifact_sha256 FROM baseline_profiles WHERE id = %s",
                             (ready[0]["id"],)).fetchone()
    assert len(sha) == 64
    stored = get_storage().objects[key]
    assert b"feature_frame" not in stored and b"reference_documents" not in stored

    rep = report(client, reviewer, doc_id)
    assert rep["case"]["status"] == "evidence_ready"
    delta = run(rep, "consistency")
    assert delta["status"] == "completed", delta
    assert delta["pipeline_version"] == consistency.PIPELINE_VERSION
    assert delta["threshold"] == pytest.approx(15.698170436926013)
    assert delta["outcome"] in ("same-author-compatible", "stylistically-unusual")
    assert delta["confidence"] == "low"          # 8 documents: the model's minimum
    assert delta["profile"]["source"] == "enrolled"
    d = delta["details"]
    assert d["website_status"] == "scored" and d["deployment_status"] == "experimental"
    assert d["schema"] == "viskopic.website-consistency.v1"
    assert d["quality"]["candidate"] == "passed" and d["quality"]["references"] == "passed"
    assert len(d["windows"]) == 3

    # The adapter's descriptive diagnostics, reported separately from the score and unchanged.
    m = d["measurements"]
    assert m["v1_3_centered_similarity"] == pytest.approx(delta["score"])
    assert m["relative_distance"] == pytest.approx(m["raw_distance"] / m["shrunk_typical_genuine_distance"])
    assert m["margin_to_threshold"] == pytest.approx(delta["score"] - delta["threshold"])
    assert (delta["outcome"] == "same-author-compatible") == (m["margin_to_threshold"] >= 0)

    kinds = [e["kind"] for e in delta["evidence"]]
    assert kinds[:2] == ["overall_score", "style_distance"] and "feature_group" in kinds
    overall = delta["evidence"][0]
    assert overall["statement"].startswith("Experimental comparison.")
    assert "not a probability" in overall["statement"] and "unreviewed" not in overall["statement"]
    assert delta["evidence"][1]["observed"] == pytest.approx(m["relative_distance"])
    assert overall["spans"], "the sampled passages should be located in the extracted text"
    text = client.get(f"/api/documents/{doc_id}/text", headers=reviewer).json()["text"]
    # Nothing stored for the run carries essay text (the adapter's raw result includes sampled passages).
    stored = str(delta["details"])
    words = text.split()
    for i in range(0, len(words) - 6, 40):
        assert " ".join(words[i:i + 6]) not in stored
    for item in delta["evidence"]:
        for s in item["spans"]:
            assert 0 <= s["start"] < s["end"] <= len(text)
            assert s["status"] in ("exact", "approximate")
        # Statements are templates: they never quote the essay.
        words = text.split()
        for i in range(0, len(words) - 6, 50):
            assert " ".join(words[i:i + 6]) not in item["statement"]
    for item in delta["evidence"]:
        if item["kind"] == "feature_deviation":
            assert abs(item["z_score"]) >= 2 and item["baseline_sd"] is not None

    assert run(rep, "document_signals")["status"] == "completed"
    assert run(rep, "language")["status"] == "unavailable"
    assert run(rep, "authenticity")["status"] == "unavailable"

    # Viewers can read the report; another institution can't see it at all.
    assert report(client, viewer, doc_id)["case"]["id"] == rep["case"]["id"]
    other = tenant_factory("Other")
    assert client.get(f"/api/documents/{doc_id}/analysis", headers=other.owner).status_code == 404
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (other.id,))
        for table in ("baseline_profiles", "cases", "analysis_runs", "evidence_items"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        c.execute("SELECT set_config('app.institution_id', '', false)")
        assert c.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 0

    # Results are append-only for the app role.
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (t.id,))
        with pytest.raises(Exception):
            c.execute("UPDATE analysis_runs SET score = 0")

    audit = client.get("/api/audit", headers=t.owner)
    if audit.status_code == 200:
        actions = {a["action"] for a in audit.json()}
        assert {"profile.queued", "profile.built", "case.opened", "analysis.completed",
                "document.quality_reviewed"} <= actions

    # A review that finds non-English text blocks scoring, with no verdict, and is append-only.
    assert review(client, reviewer, doc_id, language="non_english").status_code == 201
    drain()
    rep = report(client, reviewer, doc_id)
    delta = run(rep, "consistency")
    assert delta["status"] == "not_eligible" and delta["details"]["website_status"] == "unsupported_text"
    assert delta["score"] is None and delta["outcome"] is None
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (t.id,))
        assert c.execute("SELECT count(*) FROM document_quality_reviews").fetchone()[0] == 10
        with pytest.raises(Exception):
            c.execute("UPDATE document_quality_reviews SET language = 'english'")


def test_awaiting_baseline_then_demonstration_profile(client, tenant_factory, unreviewed_demo):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)
    sub = upload(client, reviewer, course, student, "sub.txt", essay(5).encode(), role="submission")
    doc_id = sub.json()["id"]
    drain()
    rep = report(client, reviewer, doc_id)
    assert rep["case"]["status"] == "awaiting_baseline"
    assert run(rep, "consistency")["status"] == "not_eligible"
    assert "baseline" in run(rep, "consistency")["reason"]

    demos = client.get("/api/analysis/demo-profiles", headers=reviewer).json()
    # Only the model's built-in research profiles: never another institution's enrolled students,
    # even though they share the worker's in-memory model.
    assert len(demos) == 35 and all(d["profile_id"].startswith("author_") for d in demos)
    author = demos[0]["profile_id"]
    assert client.post(f"/api/students/{student}/profiles/demonstration", headers=viewer,
                       json={"author_id": author}).status_code == 403
    assert client.post(f"/api/students/{student}/profiles/demonstration", headers=reviewer,
                       json={"author_id": "author_0"}).status_code == 422
    r = client.post(f"/api/students/{student}/profiles/demonstration", headers=reviewer, json={"author_id": author})
    assert r.status_code == 201, r.text
    drain()

    rep = report(client, reviewer, doc_id)
    assert rep["case"]["status"] == "evidence_ready"
    delta = run(rep, "consistency")
    assert delta["status"] == "completed" and delta["confidence"] == "low"
    assert delta["profile"]["source"] == "demonstration"
    assert delta["details"]["website_status"] == "scored_demo_unreviewed"
    assert "demonstration reference profile" in delta["evidence"][0]["statement"]
    assert "unreviewed demonstration result" in delta["evidence"][0]["statement"]
    assert rep["total_runs"] == 8   # first attempt kept alongside the new one

    # A short submission is explained, not scored.
    short = upload(client, reviewer, course, student, "short.txt", essay(6, words=400).encode(), role="submission")
    drain()
    d2 = run(report(client, reviewer, short.json()["id"]), "consistency")
    assert d2["status"] == "not_eligible" and d2["details"]["website_status"] == "insufficient_evidence"
    assert f"{consistency.MIN_WORDS:,}" in d2["reason"] and d2["score"] is None

    # Re-analysis on request; viewers can't trigger it.
    case_id = rep["case"]["id"]
    assert client.post(f"/api/cases/{case_id}/reanalyse", headers=viewer).status_code == 403
    assert client.post(f"/api/cases/{case_id}/reanalyse", headers=reviewer).status_code == 202
    drain()
    assert report(client, reviewer, doc_id)["total_runs"] == 12

    # Outside local development the research papers behind a demonstration profile can't be quality
    # reviewed, so the same case is no longer scored. It is not "awaiting review" either: no review
    # could make it scoreable, so the reason says so instead of asking for one.
    get_settings().allow_unreviewed_consistency = False
    assert client.post(f"/api/cases/{case_id}/reanalyse", headers=reviewer).status_code == 202
    drain()
    rep = report(client, reviewer, doc_id)
    assert rep["case"]["status"] == "evidence_ready"
    d3 = run(rep, "consistency")
    assert d3["status"] == "not_eligible" and d3["score"] is None
    assert d3["details"]["website_status"] == "demo_requires_unreviewed_mode"
    assert "can't be quality-reviewed" in d3["reason"]


def test_baseline_the_model_cannot_use_fails_clearly(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, _ = reviewer_setup(client, t)
    # Enough words by count, but every sentence is under four words, which the model discards.
    choppy = " ".join(f"Point {i} here." for i in range(700))
    for i in range(8):
        upload(client, reviewer, course, student, f"b{i}.txt", (choppy + f" End {i} now.").encode())
    drain()
    profiles = client.get(f"/api/students/{student}/profiles", headers=reviewer).json()["profiles"]
    assert profiles[0]["status"] == "failed"
    assert "baseline not usable by the model" in profiles[0]["error"]
    assert not re.search(r"Point \d+ here", profiles[0]["error"])


def test_locate_tolerates_whitespace_and_dropped_sentences():
    # The model drops very short sentences ("Ok."), so the whole passage won't match; its ends still do.
    text = ("Intro line.\n\nThe first   sentence is here and long enough. " + " ".join(f"w{i}" for i in range(20))
            + " Ok. " + " ".join(f"w{i}" for i in range(20, 40)) + " end.")
    sampled = "The first sentence is here and long enough. " + " ".join(f"w{i}" for i in range(40)) + " end."
    start, end, status = consistency.locate(text, sampled)
    assert status == "approximate" and text[start:].startswith("The first") and text[:end].endswith("end.")
    exact = consistency.locate("a  b\nc d", "a b c d")
    assert exact == (0, 8, "exact")
    assert consistency.locate("nothing here", "missing words entirely") is None


def test_combined_baseline_review():
    ok = {"id": "r1", "text_sha256": "a" * 64, "language": "english", "extraction": "pass"}
    q = service.combine_reference_quality([("a" * 64, ok), ("a" * 64, {**ok, "id": "r2"})])
    assert (q.language, q.extraction, q.stale) == ("english", "pass", False) and q.review_id.startswith("baseline:")
    q = service.combine_reference_quality([("a" * 64, ok), ("b" * 64, None)])
    assert (q.language, q.extraction) == ("unreviewed", "unreviewed")
    # A known failure anywhere wins over a missing review; mixed beats uncertain.
    q = service.combine_reference_quality([("a" * 64, {**ok, "language": "uncertain"}), ("b" * 64, None),
                                           ("a" * 64, {**ok, "language": "mixed", "extraction": "fail"})])
    assert (q.language, q.extraction) == ("mixed", "fail")
    # A review of a different text than the one stored now makes the baseline stale.
    assert service.combine_reference_quality([("c" * 64, ok)]).stale is True


def test_vendored_adapter_is_unchanged():
    # The model hash-checks its own package but not website_adapter.py; we pin it. A CRLF checkout on
    # Windows would fail here (the repo's .gitattributes marks the file -text to prevent that).
    import hashlib
    data = (consistency.VENDOR_DIR / "website_adapter.py").read_bytes()
    assert b"\r\n" not in data
    assert hashlib.sha256(data).hexdigest() == consistency.ADAPTER_SHA256


def test_near_zero_wording():
    assert consistency.fmt_measure(0.0) == "near zero" and consistency.fmt_measure(-0.04) == "near zero"
    assert consistency.fmt_measure(-17.419) == "-17.4" and consistency.fmt_measure(0.004, 2) == "near zero"


# ------------------------------------------------------------------ removing wrongly uploaded documents


def test_owner_removes_wrong_documents(client, tenant_factory, unreviewed_demo):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)
    ids = []
    for i in range(9):
        r = upload(client, reviewer, course, student, f"b{i}.txt", essay(300 + i).encode())
        ids.append(r.json()["id"])
    sub = upload(client, reviewer, course, student, "sub.txt", essay(777).encode(), role="submission")
    sub_id = sub.json()["id"]
    drain()
    first = [p for p in client.get(f"/api/students/{student}/profiles", headers=reviewer).json()["profiles"]
             if p["status"] == "ready"][0]
    assert first["reference_count"] == 9

    wrong = ids[3]
    with owner_conn() as c:
        file_key = c.execute("SELECT storage_key FROM documents WHERE id = %s", (wrong,)).fetchone()[0]
        artifact_key = c.execute("SELECT artifact_storage_key FROM baseline_profiles WHERE id = %s",
                                 (first["id"],)).fetchone()[0]

    # Only institution owners can remove, and a reason is required.
    assert client.post(f"/api/documents/{wrong}/remove", headers=reviewer,
                       json={"reason": "uploaded for the wrong student"}).status_code == 403
    assert client.post(f"/api/documents/{wrong}/remove", headers=t.owner, json={"reason": "x"}).status_code == 422
    other = tenant_factory("Other")
    assert client.post(f"/api/documents/{wrong}/remove", headers=other.owner,
                       json={"reason": "not my institution"}).status_code == 404

    r = client.post(f"/api/documents/{wrong}/remove", headers=t.owner, json={"reason": "uploaded for the wrong student"})
    assert r.status_code == 200, r.text
    # Every profile version that used it is retired, including older superseded ones.
    assert first["id"] in r.json()["profiles_retired"] and r.json()["profile_queued"]
    assert client.post(f"/api/documents/{wrong}/remove", headers=t.owner,
                       json={"reason": "uploaded for the wrong student"}).status_code == 409

    # File, text, signals and the profile built from it are gone; a tombstone stays.
    assert file_key not in get_storage().objects and artifact_key not in get_storage().objects
    with owner_conn() as c:
        assert c.execute("SELECT count(*) FROM document_texts WHERE document_id = %s", (wrong,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM document_signals WHERE document_id = %s", (wrong,)).fetchone()[0] == 0
    doc = client.get(f"/api/documents/{wrong}", headers=reviewer).json()
    assert doc["status"] == "removed" and doc["removal_reason"] == "uploaded for the wrong student"
    assert doc["removed_by_email"] == t.owner_email and doc["can_remove"] is False
    assert client.get(f"/api/documents/{wrong}/text", headers=reviewer).status_code == 409

    # A new profile is built from the 8 remaining documents and the submission is analysed again.
    drain()
    profiles = client.get(f"/api/students/{student}/profiles", headers=reviewer).json()["profiles"]
    ready = [p for p in profiles if p["status"] == "ready"]
    assert len(ready) == 1 and ready[0]["reference_count"] == 8 and ready[0]["version"] > first["version"]
    with owner_conn() as c:
        refs = c.execute("SELECT reference_document_ids FROM baseline_profiles WHERE id = %s", (ready[0]["id"],)).fetchone()[0]
    assert wrong not in [str(x) for x in refs]
    assert next(p for p in profiles if p["id"] == first["id"])["status"] == "removed"
    rep = report(client, reviewer, sub_id)
    assert rep["case"]["status"] == "evidence_ready"
    assert run(rep, "consistency")["profile"]["id"] == ready[0]["id"]

    # Removing a submission withdraws its case; it can't be re-analysed.
    r = client.post(f"/api/documents/{sub_id}/remove", headers=t.owner, json={"reason": "submitted the wrong file"})
    assert r.status_code == 200 and len(r.json()["cases_withdrawn"]) == 1
    rep = report(client, reviewer, sub_id)
    assert rep["case"]["status"] == "withdrawn"
    assert client.post(f"/api/cases/{rep['case']['id']}/reanalyse", headers=reviewer).status_code == 409

    # Removing one more baseline leaves 7: no profile, and nothing is queued.
    r = client.post(f"/api/documents/{ids[0]}/remove", headers=t.owner, json={"reason": "duplicate upload"})
    assert r.json()["profile_queued"] is None
    drain()
    data = client.get(f"/api/students/{student}/profiles", headers=reviewer).json()
    assert data["consistency"]["eligible_documents"] == 7
    assert not [p for p in data["profiles"] if p["status"] in ("ready", "queued", "building")]

    actions = [a for a in client.get("/api/audit", headers=t.owner).json() if a["action"] == "document.removed"]
    assert len(actions) == 3 and actions[-1]["details"]["reason"] in (
        "uploaded for the wrong student", "submitted the wrong file", "duplicate upload")


def test_remove_while_queued(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, _ = reviewer_setup(client, t)
    doc_id = upload(client, reviewer, course, student, "b.txt", essay(1).encode()).json()["id"]
    assert client.post(f"/api/documents/{doc_id}/remove", headers=t.owner,
                       json={"reason": "wrong file, caught early"}).status_code == 200
    drain()
    doc = client.get(f"/api/documents/{doc_id}", headers=reviewer).json()
    assert doc["status"] == "removed" and doc["word_count"] is None
