"""The case list behind the reviewer's home page, the texts-to-check queue, and the one baseline rule."""
from viskopic.analysis import consistency

from .test_access import setup_course
from .test_analysis import essay, review
from .test_intake import drain, reviewer_setup, upload


def _cases(client, headers, **params):
    r = client.get("/api/cases", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_case_list_and_texts_to_check(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, viewer = reviewer_setup(client, t)
    baseline = [upload(client, reviewer, course, student, f"b{i}.txt", essay(300 + i).encode()).json()["id"]
                for i in range(consistency.MIN_REFERENCE_DOCUMENTS)]
    short = upload(client, reviewer, course, student, "short.txt", " ".join(["word"] * 400).encode()).json()["id"]
    sub = upload(client, reviewer, course, student, "sub.txt", essay(777, words=2400).encode(),
                 role="submission").json()["id"]
    drain()

    # Nothing reviewed yet: the case waits for checks, and every usable text is counted.
    data = _cases(client, reviewer)
    assert len(data["cases"]) == 1
    c = data["cases"][0]
    assert c["document_id"] == sub and c["status"] == "awaiting_review"
    assert c["next_step"] in ("check_texts", "review_evidence")
    assert c["texts_to_check"] == consistency.MIN_REFERENCE_DOCUMENTS + 1
    assert c["consistency"]["website_status"] == "quality_review_required"
    assert c["viva"] is None
    assert data["counts"][c["next_step"]] == 1
    # No essay text anywhere in the list.
    assert not {"text", "text_enc", "statement"} & set(c)

    texts = client.get(f"/api/cases/{c['id']}/texts", headers=reviewer).json()
    ids = [d["id"] for d in texts["documents"]]
    assert ids[0] == sub, "the submission is checked first"
    assert set(ids[1:]) == set(baseline) and short not in ids, "only past essays the model can use are listed"
    assert texts["remaining"] == consistency.MIN_REFERENCE_DOCUMENTS + 1 and texts["can_manage"] is True

    # Checking texts moves the case on; the queue shrinks as reviews are recorded.
    for d in [sub, *baseline]:
        assert review(client, reviewer, d).status_code == 201
    drain()
    texts = client.get(f"/api/cases/{c['id']}/texts", headers=reviewer).json()
    assert texts["remaining"] == 0 and all(d["review"]["current"] for d in texts["documents"])
    c = _cases(client, reviewer)["cases"][0]
    assert c["status"] == "evidence_ready" and c["texts_to_check"] == 0
    assert c["next_step"] in ("review_evidence", "no_flag")
    assert c["consistency"]["status"] == "completed" and c["consistency"]["reference_count"] == 8
    assert c["combined"]["status"] in ("review_recommended", "no_flag")

    # Viewers see the same list read-only; filtering by course works.
    assert _cases(client, viewer)["cases"][0]["id"] == c["id"]
    assert client.get(f"/api/cases/{c['id']}/texts", headers=viewer).json()["can_manage"] is False
    assert len(_cases(client, reviewer, course_id=course)["cases"]) == 1


def test_case_list_is_scoped(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, _ = reviewer_setup(client, t)
    upload(client, reviewer, course, student, "sub.txt", essay(5).encode(), role="submission")
    drain()
    case_id = _cases(client, reviewer)["cases"][0]["id"]

    # A reviewer on another course of the same university sees nothing of this one.
    _, other_course = setup_course(client, t, dept="History", code="HIS101")
    oid, other = t.add_user(client, "other-reviewer")
    assert client.post("/api/role-assignments", headers=t.owner, json={
        "user_id": oid, "role": "reviewer", "scope_type": "course", "scope_id": other_course}).status_code == 201
    assert _cases(client, other)["cases"] == []
    assert client.get(f"/api/cases/{case_id}/texts", headers=other).status_code == 404
    assert client.get("/api/cases", headers=other, params={"course_id": course}).status_code in (403, 404)

    # Another university sees nothing at all.
    stranger = tenant_factory("Other")
    assert _cases(client, stranger.owner)["cases"] == []
    assert client.get(f"/api/cases/{case_id}/texts", headers=stranger.owner).status_code == 404


def test_baseline_summary_uses_the_models_rule(client, tenant_factory):
    t = tenant_factory()
    course, student, reviewer, _ = reviewer_setup(client, t)
    for i in range(3):   # the old product rule (3 essays, 1,500 words) would call this ready
        upload(client, reviewer, course, student, f"short{i}.txt", " ".join(["word"] * 600).encode())
    drain()
    b = client.get(f"/api/students/{student}", headers=reviewer).json()["baseline"]
    assert b["min_essays"] == consistency.MIN_REFERENCE_DOCUMENTS and b["min_words"] == consistency.MIN_WORDS
    assert b["essays"] == 0 and b["too_short"] == 3 and b["eligible"] is False and b["confidence"] is None

    for i in range(consistency.MIN_REFERENCE_DOCUMENTS):
        upload(client, reviewer, course, student, f"long{i}.txt", essay(400 + i).encode(),
               provenance="supervised" if i < 2 else "unsupervised")
    drain()
    b = client.get(f"/api/students/{student}", headers=reviewer).json()["baseline"]
    assert b["essays"] == consistency.MIN_REFERENCE_DOCUMENTS and b["supervised"] == 2
    assert b["eligible"] is True and b["confidence"] == "low"     # under 10 documents
