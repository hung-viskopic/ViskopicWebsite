"""Viva MVP: invitations, the student's timed session, recordings, transcripts, the report and isolation."""
from datetime import timedelta

import psycopg
import pytest

from viskopic import mailer
from viskopic.routers import viva_public
from viskopic.storage import get_storage
from viskopic.viva import ai, visq
from viskopic.viva import service as viva_service

from .conftest import OPERATOR, app_conn, owner_conn
from .test_intake import drain, reviewer_setup, upload

ESSAY = ("Urban transport policy shapes who can reach jobs. "
         "Congestion charging in London reduced traffic in the centre, but the gains for bus users were smaller "
         "than expected because road works absorbed much of the freed space. "
         "I argue that charging works best when the revenue is ring-fenced for buses. "
         "Critics reply that charges fall hardest on low-income drivers who have no alternative. ") * 3

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 2000   # magic bytes are enough for the server's sniffing
WEBM_VIDEO = b"\x1a\x45\xdf\xa3" + b"\x00" * 200 + b"V_VP8" + b"\x00" * 200 + b"A_OPUS" + b"\x00" * 2000


class FakeAI:
    name = "fake"

    def __init__(self, text: str = "Well, I chose ring-fencing because..."):
        self.text = text

    def transcribe(self, audio, mime_type):
        return {"text": self.text, "duration": 20.0,
                "segments": [{"start": 2.5, "end": 9.0, "text": "Well, I chose"},
                             {"start": 13.0, "end": 19.0, "text": "ring-fencing because"}]}


@pytest.fixture
def mail():
    m = mailer.MemoryMailer()
    mailer.set_mailer(m)
    yield m
    mailer.set_mailer(None)


@pytest.fixture
def fake_ai(monkeypatch):
    f = FakeAI()
    monkeypatch.setenv("VISKOPIC_ALLOW_EXTERNAL_AI", "true")
    monkeypatch.setenv("VISKOPIC_VIVA_AI_PROVIDER", "fake")
    from viskopic.config import get_settings
    get_settings.cache_clear()
    ai.set_provider(f)
    yield f
    ai.set_provider(None)
    monkeypatch.delenv("VISKOPIC_ALLOW_EXTERNAL_AI")
    monkeypatch.delenv("VISKOPIC_VIVA_AI_PROVIDER")
    get_settings.cache_clear()


@pytest.fixture
def fake_visq(monkeypatch):
    """Visq with its own offline stand-in model (vendor/visq_v2/visq/fake.py): no network, no spend."""
    monkeypatch.setenv("VISKOPIC_ALLOW_EXTERNAL_AI", "true")
    from viskopic.config import get_settings
    get_settings.cache_clear()
    clients = []

    def factory():
        c = visq.modules().fake.FakeClient()
        clients.append(c)
        return c
    visq.set_client_factory(factory)
    yield clients
    visq.set_client_factory(None)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _limits():
    viva_public.reset_rate_limits()
    yield


def make_case(client, t):
    course, student, reviewer, viewer = reviewer_setup(client, t)
    r = upload(client, reviewer, course, student, "essay.txt", ESSAY.encode(), role="submission",
               provenance="unsupervised")
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    drain()
    v = client.get(f"/api/documents/{doc_id}/vivas", headers=reviewer).json()
    assert v["case"] is not None
    return doc_id, v["case"]["id"], reviewer, viewer


def invite(client, headers, case_id, fmt="spoken", questions=None, **kw):
    body = {"kind": "reasoning", "answer_format": fmt, "email": "student@example.ac.uk", "prep_seconds": 0,
            "answer_seconds": 60, "questions": questions or [
                {"text": "In two or three sentences, what is the main argument of your essay?", "source": "template"},
                {"text": "Why is ring-fencing the key condition for you?",
                 "passage": "I argue that charging works best when   the revenue is ring-fenced for buses.",
                 "source": "reviewer"}], **kw}
    r = client.post(f"/api/cases/{case_id}/viva-invitations", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[1]


def pub(client, method, token, path="", session=None, **kw):
    headers = kw.pop("headers", {})
    if session:
        headers["X-Viva-Session"] = session
    return client.request(method, f"/api/public/viva/{token}{path}", headers=headers, **kw)


# ------------------------------------------------------------------ staff side


def test_drafts_use_templates_when_ai_is_off(client, tenant_factory):
    t = tenant_factory()
    _, case_id, reviewer, viewer = make_case(client, t)
    r = client.post(f"/api/cases/{case_id}/viva-drafts", headers=reviewer, json={"kind": "style", "count": 3})
    assert r.status_code == 200
    assert r.json()["provider"] == "templates" and len(r.json()["questions"]) == 3
    assert all(q["passage"] is None for q in r.json()["questions"])
    assert client.post(f"/api/cases/{case_id}/viva-drafts", headers=viewer, json={"kind": "style"}).status_code == 403


def test_visq_drafts_questions_with_passages_and_reviewer_notes(client, tenant_factory, fake_visq):
    t = tenant_factory()
    _, case_id, reviewer, viewer = make_case(client, t)
    r = client.post(f"/api/cases/{case_id}/viva-drafts", headers=reviewer, json={"kind": "reasoning", "count": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "visq" and body["engine"].startswith("visq q1.20 offline stand-in (3d631d8)")
    qs = body["questions"]
    assert 1 <= len(qs) <= 4 and all(q["source"] == "visq" for q in qs)
    assert {q["qtype"] for q in qs} <= {"tradeoff", "limit", "case"}
    assert all(q["reviewer_note"] for q in qs)
    for q in qs:                         # passages are the submission's own text, found again in it
        if q["passage"]:
            assert ESSAY[q["passage_start"]:q["passage_end"]] == q["passage"]
    # Visq saw the essay as data inside tags, and nothing was cached to disk.
    assert any("<essay>" in u for _, u in fake_visq[0].inputs)
    assert not (visq.VENDOR_DIR / ".visq_cache").exists()
    assert client.post(f"/api/cases/{case_id}/viva-drafts", headers=viewer, json={"kind": "style"}).status_code == 403


def test_vendored_visq_is_unchanged():
    for name, want in visq.FILE_SHA256.items():
        import hashlib
        assert hashlib.sha256((visq.VENDOR_DIR / "visq" / name).read_bytes()).hexdigest() == want
    m = visq.modules()
    assert m.config.REQUIRE_ZDR and m.config.PROVIDER_PREFS == {"zdr": True}


def test_invitation_rejects_passage_not_in_submission(client, tenant_factory):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    r = client.post(f"/api/cases/{case_id}/viva-invitations", headers=reviewer, json={
        "kind": "style", "email": "s@example.ac.uk",
        "questions": [{"text": "Explain how you wrote this part.", "passage": "Something the student never wrote."}]})
    assert r.status_code == 422


def test_email_is_minimal_and_token_is_not_kept(client, tenant_factory, mail):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    inv = invite(client, reviewer, case_id)
    drain()
    assert len(mail.sent) == 1
    m = mail.sent[0]
    assert m.to == "student@example.ac.uk" and m.subject.startswith("[TEST] ")
    assert inv["test_link"] in m.text
    for word in ("AI", "score", "essay.txt", "case", "consistency"):
        assert word not in m.text
    with owner_conn() as c:
        row = c.execute("SELECT status, token_ciphertext, email_ciphertext FROM viva_invitations WHERE id = %s",
                        (inv["id"],)).fetchone()
    assert row[0] == "sent" and row[1] is None
    assert b"student@" not in bytes(row[2])


def test_email_failure_marks_invitation_failed(client, tenant_factory, mail):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    inv = invite(client, reviewer, case_id)
    mail.fail_with = "connection refused"
    with owner_conn() as c:   # skip the retry backoff
        for _ in range(3):
            drain()
            c.execute("UPDATE jobs SET run_after = now() WHERE viva_invitation_id = %s", (inv["id"],))
        drain()
    with owner_conn() as c:
        status, err = c.execute("SELECT status, last_error FROM viva_invitations WHERE id = %s", (inv["id"],)).fetchone()
    assert status == "failed" and "connection refused" in err
    assert pub(client, "GET", token_of(inv["test_link"])).json()["status"] == "failed"


# ------------------------------------------------------------------ student side


def test_spoken_viva_end_to_end(client, tenant_factory, mail, fake_ai):
    t = tenant_factory()
    doc_id, case_id, reviewer, viewer = make_case(client, t)
    inv = invite(client, reviewer, case_id)
    tok = token_of(inv["test_link"])

    intro = pub(client, "GET", tok).json()
    assert intro["status"] == "opened" and intro["question_count"] == 2 and intro["answer_format"] == "spoken"
    assert intro["transcribed"] is True
    assert set(intro) == {"institution_name", "status", "message", "kind", "answer_format", "question_count",
                          "prep_seconds", "answer_seconds", "deadline_at", "is_test", "transcribed", "camera_required",
                          "followups"}

    # Nothing can be written before starting.
    assert pub(client, "POST", tok, "/next").status_code == 409
    session = pub(client, "POST", tok, "/start").json()["session"]

    q1 = pub(client, "POST", tok, "/next", session).json()
    assert q1["question"]["position"] == 1 and q1["question"]["passage"] is None
    # Asking again before answering returns the same question: no reading ahead.
    assert pub(client, "POST", tok, "/next", session).json()["question"]["id"] == q1["question"]["id"]
    # Wrong format and junk audio are refused.
    assert pub(client, "PUT", tok, f"/answers/{q1['question']['id']}/text", session, json={"text": "hi"}).status_code == 422
    assert pub(client, "PUT", tok, f"/answers/{q1['question']['id']}/audio", session, content=b"not audio").status_code == 415
    assert pub(client, "PUT", tok, f"/answers/{q1['question']['id']}/audio", session, content=WEBM).status_code == 200
    # No going back.
    assert pub(client, "PUT", tok, f"/answers/{q1['question']['id']}/audio", session, content=WEBM).status_code == 409

    q2 = pub(client, "POST", tok, "/next", session).json()
    assert q2["question"]["position"] == 2 and "ring-fenced" in q2["question"]["passage"]
    # Browser signals are recorded (self-reported).
    assert pub(client, "POST", tok, "/events", session, json={"events": [
        {"kind": "focus_lost", "question_id": q2["question"]["id"]},
        {"kind": "focus_back", "question_id": q2["question"]["id"]},
        {"kind": "paste_blocked", "question_id": q2["question"]["id"], "detail": {"chars": 812}}]}).status_code == 200
    assert pub(client, "POST", tok, "/events", session, json={"events": [{"kind": "submit"}]}).status_code == 422
    # Submitting early is refused.
    assert pub(client, "POST", tok, "/submit", session).status_code == 409
    assert pub(client, "PUT", tok, f"/answers/{q2['question']['id']}/audio", session, content=WEBM).status_code == 200
    assert pub(client, "POST", tok, "/next", session).json() == {"done": True, "total": 2}
    assert pub(client, "POST", tok, "/submit", session).status_code == 200
    # Locked after submit.
    assert pub(client, "POST", tok, "/start").status_code == 409
    assert pub(client, "GET", tok).json()["status"] == "submitted"

    drain()   # transcription jobs
    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=viewer).json()
    assert rep["invitation"]["status"] == "submitted" and rep["invitation"]["supervision_mode"] == "oral_recorded"
    a, b = rep["questions"]
    assert a["answer_kind"] == "spoken" and a["recording"]["status"] == "stored"
    assert a["transcript"]["text"].startswith("Well, I chose") and a["transcript"]["provider"] == "fake"
    tl = a["transcript"]["timeline"]
    assert tl["first_speech_ms"] == 2500 and tl["longest_pause_ms"] == 4000 and tl["pauses_over_3s"] == 1
    assert b["signals"]["focus_lost"] == 1 and b["signals"]["paste_blocked"] == 1

    # Audio is encrypted at rest and only reachable through the API.
    with owner_conn() as c:
        key = c.execute("SELECT storage_key FROM viva_recordings WHERE id = %s", (a["recording"]["id"],)).fetchone()[0]
    assert get_storage().get(key)[:4] != WEBM[:4]
    r = client.get(f"/api/viva-recordings/{a['recording']['id']}/audio", headers=viewer)
    assert r.status_code == 200 and r.content == WEBM and r.headers["content-type"] == "audio/webm"

    # Viewers can read but not assess; reviewers can, newest wins.
    assert client.post(f"/api/viva-invitations/{inv['id']}/assessments", headers=viewer,
                       json={"rating": "concern"}).status_code == 403
    for rating in ("unclear", "consistent"):
        assert client.post(f"/api/viva-invitations/{inv['id']}/assessments", headers=reviewer,
                           json={"question_id": a["id"], "rating": rating, "note": "Explained it well."}).status_code == 201
    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=reviewer).json()
    assert rep["questions"][0]["assessment"]["rating"] == "consistent"
    assert rep["questions"][0]["assessment"]["note"] == "Explained it well."

    with owner_conn() as c:
        actions = {r[0] for r in c.execute("SELECT action FROM audit_log WHERE institution_id = %s", (t.id,)).fetchall()}
        notes = [bytes(r[0]) for r in c.execute("SELECT note_ciphertext FROM viva_assessments").fetchall()]
    # (no email: the viva was started through the test link before the worker ran)
    assert {"viva.invitation_created", "viva.opened", "viva.started", "viva.submitted",
            "viva.transcribed", "viva.report_viewed", "viva.audio_played", "viva.assessment_recorded"} <= actions
    assert all(b"Explained" not in n for n in notes)


def test_timing_is_enforced_by_the_server(client, tenant_factory, monkeypatch):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    inv = invite(client, reviewer, case_id, fmt="typed")
    tok = token_of(inv["test_link"])
    session = pub(client, "POST", tok, "/start").json()["session"]
    q1 = pub(client, "POST", tok, "/next", session).json()["question"]
    real_now = viva_service.now

    # 75 seconds later (60 s answer + 10 s grace exceeded): the answer is refused and recorded as late.
    monkeypatch.setattr(viva_service, "now", lambda: real_now() + timedelta(seconds=75))
    r = pub(client, "PUT", tok, f"/answers/{q1['id']}/text", session, json={"text": "Late answer"})
    assert r.status_code == 409
    # Asking for the next question records the timeout and moves on.
    q2 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert q2["position"] == 2
    monkeypatch.setattr(viva_service, "now", real_now)
    # Answers over the length cap are refused.
    assert pub(client, "PUT", tok, f"/answers/{q2['id']}/text", session, json={"text": "x" * 5001}).status_code == 413
    assert pub(client, "PUT", tok, f"/answers/{q2['id']}/text", session, json={"text": "On time."}).status_code == 200
    assert pub(client, "POST", tok, "/next", session).json()["done"] is True
    assert pub(client, "POST", tok, "/submit", session).status_code == 200

    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=reviewer).json()
    assert rep["invitation"]["supervision_mode"] == "restricted"
    assert rep["questions"][0]["answer_kind"] == "skipped"
    assert rep["questions"][0]["signals"]["late_write"] == 1 and rep["questions"][0]["signals"]["timed_out"]
    assert rep["questions"][1]["typed_answer"] == "On time."


def test_second_device_takes_over_the_session(client, tenant_factory):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    inv = invite(client, reviewer, case_id, fmt="typed")
    tok = token_of(inv["test_link"])
    first = pub(client, "POST", tok, "/start").json()["session"]
    q1 = pub(client, "POST", tok, "/next", first).json()["question"]
    second = pub(client, "POST", tok, "/start").json()["session"]
    assert pub(client, "PUT", tok, f"/answers/{q1['id']}/text", first, json={"text": "from the laptop"}).status_code == 409
    assert pub(client, "PUT", tok, f"/answers/{q1['id']}/text", "made-up", json={"text": "guess"}).status_code == 409
    # The second device continues the same question, with the same server deadline.
    resumed = pub(client, "POST", tok, "/next", second).json()
    assert resumed["question"]["id"] == q1["id"]
    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=reviewer).json()
    assert rep["session"]["second_sessions"] == 1


def test_revoke_resend_and_expiry(client, tenant_factory, mail):
    t = tenant_factory()
    _, case_id, reviewer, viewer = make_case(client, t)
    inv = invite(client, reviewer, case_id)
    tok = token_of(inv["test_link"])
    assert client.post(f"/api/viva-invitations/{inv['id']}/revoke", headers=viewer, json={"reason": "not needed"}).status_code == 403
    new = client.post(f"/api/viva-invitations/{inv['id']}/resend", headers=reviewer)
    assert new.status_code == 201, new.text
    assert pub(client, "GET", tok).json()["status"] == "revoked"
    assert pub(client, "POST", tok, "/start").status_code == 409
    new_tok = token_of(new.json()["test_link"])
    assert new_tok != tok and pub(client, "GET", new_tok).json()["question_count"] == 2
    assert client.post(f"/api/viva-invitations/{new.json()['id']}/revoke", headers=reviewer,
                       json={"reason": "sent to the wrong address"}).status_code == 200
    drain()
    assert mail.sent == []          # revoked before the worker got to them

    late = invite(client, reviewer, case_id)
    with owner_conn() as c:
        c.execute("UPDATE viva_invitations SET deadline_at = now() - interval '1 minute' WHERE id = %s", (late["id"],))
    assert pub(client, "GET", token_of(late["test_link"])).json()["status"] == "expired"
    assert pub(client, "POST", token_of(late["test_link"]), "/start").status_code == 409
    assert pub(client, "GET", "x" * 43).status_code == 404


def test_removed_submission_revokes_open_vivas(client, tenant_factory):
    t = tenant_factory()
    doc_id, case_id, reviewer, _ = make_case(client, t)
    inv = invite(client, reviewer, case_id)
    r = client.post(f"/api/documents/{doc_id}/remove", headers=t.owner, json={"reason": "uploaded for the wrong student"})
    assert r.status_code == 200, r.text
    assert pub(client, "GET", token_of(inv["test_link"])).json()["status"] == "revoked"
    assert client.post(f"/api/cases/{case_id}/viva-invitations", headers=reviewer, json={
        "kind": "style", "email": "s@example.ac.uk",
        "questions": [{"text": "Explain how you wrote this essay."}]}).status_code == 409


def test_rate_limit(client, tenant_factory, monkeypatch):
    monkeypatch.setenv("VISKOPIC_VIVA_RATE_LIMIT_PER_MINUTE", "5")
    from viskopic.config import get_settings
    get_settings.cache_clear()
    try:
        codes = [pub(client, "GET", "y" * 43).status_code for _ in range(7)]
    finally:
        monkeypatch.delenv("VISKOPIC_VIVA_RATE_LIMIT_PER_MINUTE")
        get_settings.cache_clear()
    assert codes[:5] == [404] * 5 and codes[5:] == [429, 429]


# ------------------------------------------------------------------ isolation


def test_other_institution_and_operator_see_nothing(client, tenant_factory):
    a, b = tenant_factory("Uni-A"), tenant_factory("Uni-B")
    doc_id, case_id, reviewer, _ = make_case(client, a)
    inv = invite(client, reviewer, case_id, fmt="typed")
    tok = token_of(inv["test_link"])
    s = pub(client, "POST", tok, "/start").json()["session"]
    q = pub(client, "POST", tok, "/next", s).json()["question"]
    pub(client, "PUT", tok, f"/answers/{q['id']}/text", s, json={"text": "secret answer"})

    assert client.get(f"/api/viva-invitations/{inv['id']}/report", headers=b.owner).status_code == 404
    assert client.get(f"/api/documents/{doc_id}/vivas", headers=b.owner).status_code == 404
    assert client.post(f"/api/viva-invitations/{inv['id']}/revoke", headers=b.owner, json={"reason": "x" * 5}).status_code == 404
    assert client.get(f"/api/viva-invitations/{inv['id']}/report", headers=OPERATOR).status_code == 403

    tables = ("viva_invitations", "viva_questions", "viva_question_releases", "viva_responses", "viva_recordings",
              "viva_transcripts", "viva_session_events", "viva_assessments")
    with app_conn() as c:
        for table in tables:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table   # no tenant context
        c.execute("SELECT set_config('app.institution_id', %s, false)", (b.id,))
        for table in tables:
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        # Answers are encrypted at rest.
        c.execute("SELECT set_config('app.institution_id', %s, false)", (a.id,))
        blob = bytes(c.execute("SELECT answer_ciphertext FROM viva_responses WHERE kind = 'typed'").fetchone()[0])
        assert b"secret" not in blob
        # Append-only tables reject edits, even for the institution itself.
        for sql in ("UPDATE viva_responses SET kind = 'skipped'", "DELETE FROM viva_session_events",
                    "UPDATE viva_questions SET position = 9"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
    with owner_conn() as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE viva_responses SET kind = 'skipped'")
        # The token resolver gives nothing for unknown tokens.
        assert c.execute("SELECT * FROM viva_resolve_token(%s)", (b"\x00" * 32,)).fetchone() is None
    # Operator functions expose no viva data.
    row = next(r for r in client.get("/api/platform/institutions", headers=OPERATOR).json() if r["id"] == a.id)
    assert not any("viva" in k for k in row)


def test_camera_viva_records_video(client, tenant_factory):
    t = tenant_factory()
    _, case_id, reviewer, viewer = make_case(client, t)
    # The camera only goes with spoken answers.
    r = client.post(f"/api/cases/{case_id}/viva-invitations", headers=reviewer, json={
        "kind": "style", "answer_format": "typed", "camera": True, "email": "s@example.ac.uk",
        "questions": [{"text": "Explain how you wrote this essay."}]})
    assert r.status_code == 422
    inv = invite(client, reviewer, case_id, camera=True)
    tok = token_of(inv["test_link"])
    assert pub(client, "GET", tok).json()["camera_required"] is True
    s = pub(client, "POST", tok, "/start").json()["session"]
    q1 = pub(client, "POST", tok, "/next", s).json()["question"]
    assert pub(client, "PUT", tok, f"/answers/{q1['id']}/audio", s, content=WEBM_VIDEO).status_code == 200
    # An answer that arrives without a video track (camera failed) is kept, and the gap is recorded.
    q2 = pub(client, "POST", tok, "/next", s).json()["question"]
    assert pub(client, "PUT", tok, f"/answers/{q2['id']}/audio", s, content=WEBM).status_code == 200
    assert pub(client, "POST", tok, "/next", s).json()["done"] is True
    assert pub(client, "POST", tok, "/submit", s).status_code == 200

    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=viewer).json()
    assert rep["invitation"]["supervision_mode"] == "oral_video" and rep["invitation"]["camera_required"] is True
    a, b = rep["questions"]
    assert a["recording"]["has_video"] is True and a["recording"]["mime_type"] == "video/webm"
    assert b["recording"]["has_video"] is False and b["signals"]["camera_unavailable"] is True
    r = client.get(f"/api/viva-recordings/{a['recording']['id']}/audio", headers=viewer)
    assert r.status_code == 200 and r.headers["content-type"] == "video/webm" and r.content == WEBM_VIDEO
    # Without the camera option, video tracks are not marked as video.
    plain = invite(client, reviewer, case_id)
    assert pub(client, "GET", token_of(plain["test_link"])).json()["camera_required"] is False


def test_video_track_detection():
    assert viva_service.has_video_track(WEBM_VIDEO, "audio/webm")
    assert not viva_service.has_video_track(WEBM, "audio/webm")
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 40 + b"hdlr" + b"\x00" * 8 + b"vide" + b"\x00" * 50
    assert viva_service.has_video_track(mp4, "audio/mp4")
    assert viva_service.media_type("audio/mp4", True) == "video/mp4"


# ------------------------------------------------------------------ Visq follow-ups


def visq_questions(client, reviewer, case_id, n=2):
    r = client.post(f"/api/cases/{case_id}/viva-drafts", headers=reviewer, json={"kind": "reasoning", "count": n})
    assert r.status_code == 200, r.text
    return [{k: q[k] for k in ("text", "passage", "source", "qtype", "reviewer_note")} for q in r.json()["questions"]]


SHORT = "It depends."
LONG = ("I think the conditions matter most here because the revenue only helps buses if the council protects it "
        "for a long time and the road space freed by the charge is not taken back by other uses later on.")


def test_typed_viva_with_visq_followups(client, tenant_factory, fake_visq):
    t = tenant_factory()
    doc_id, case_id, reviewer, viewer = make_case(client, t)
    qs = visq_questions(client, reviewer, case_id)
    inv = invite(client, reviewer, case_id, fmt="typed", questions=qs, followups=True)
    tok = token_of(inv["test_link"])
    intro = pub(client, "GET", tok).json()
    assert intro["followups"] is True and intro["question_count"] == 2
    session = pub(client, "POST", tok, "/start").json()["session"]

    q1 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert q1["position"] == 1 and q1["followup"] is False and q1["total"] == 2
    assert "reviewer_note" not in q1 and "qtype" not in q1          # the student never sees the answer key
    assert pub(client, "PUT", tok, f"/answers/{q1['id']}/text", session, json={"text": SHORT}).status_code == 200

    # The decision isn't made yet: the page is told to wait, and cannot skip ahead.
    w = pub(client, "POST", tok, "/next", session).json()
    assert w["waiting"] is True and w["done"] is False and "question" not in w
    drain()                                                           # Visq decides (the stand-in asks after a short answer)
    f1 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert f1["followup"] is True and f1["position"] == 1 and f1["passage"] is None
    assert pub(client, "PUT", tok, f"/answers/{f1['id']}/text", session, json={"text": LONG}).status_code == 200

    # A follow-up gets no follow-up of its own: straight on to question 2.
    q2 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert q2["position"] == 2 and q2["followup"] is False
    assert pub(client, "PUT", tok, f"/answers/{q2['id']}/text", session, json={"text": LONG}).status_code == 200
    drain()                                                           # detailed answer: Visq moves on
    assert pub(client, "POST", tok, "/next", session).json() == {"done": True, "total": 2}
    assert pub(client, "POST", tok, "/submit", session).status_code == 200

    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=viewer).json()
    assert rep["invitation"]["followups_enabled"] is True
    assert rep["invitation"]["question_engine"].startswith("visq q1.20")
    a, fa, b = rep["questions"]
    assert a["source"] == "visq" and a["reviewer_note"] and a["qtype"] in ("tradeoff", "limit", "case")
    assert a["followup_decision"]["action"] == "follow_up" and a["followup_decision"]["reason"] == "model"
    assert a["followup_decision"]["why"]                              # Visq's reason, for the reviewer only
    assert fa["source"] == "visq_followup" and fa["parent_id"] == a["id"] and fa["typed_answer"] == LONG
    assert b["followup_decision"]["action"] == "move_on" and b["followup_decision"]["reason"] == "model"
    assert rep["session"]["second_sessions"] == 0

    with owner_conn() as c:
        actions = [r[0] for r in c.execute("SELECT action FROM audit_log WHERE institution_id = %s "
                                           "AND action = 'viva.followup_decided'", (t.id,)).fetchall()]
        why = bytes(c.execute("SELECT why_ciphertext FROM viva_followup_decisions WHERE action = 'follow_up' "
                              "AND institution_id = %s", (t.id,)).fetchone()[0])
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE viva_followup_decisions SET action = 'move_on'")
    assert len(actions) == 2 and b"fake" not in why                   # the reason is encrypted at rest

    # Another institution sees none of it.
    other = tenant_factory("Other")
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (other.id,))
        assert c.execute("SELECT count(*) FROM viva_followup_decisions").fetchone()[0] == 0
        c.execute("SELECT set_config('app.institution_id', '', false)")


def test_followup_wait_times_out_and_skips_need_no_model(client, tenant_factory, fake_visq, monkeypatch):
    t = tenant_factory()
    _, case_id, reviewer, viewer = make_case(client, t)
    inv = invite(client, reviewer, case_id, fmt="typed", questions=visq_questions(client, reviewer, case_id),
                 followups=True)
    tok = token_of(inv["test_link"])
    session = pub(client, "POST", tok, "/start").json()["session"]
    q1 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert pub(client, "PUT", tok, f"/answers/{q1['id']}/text", session, json={"text": SHORT}).status_code == 200
    monkeypatch.setenv("VISKOPIC_VIVA_FOLLOWUP_WAIT_SECONDS", "0")
    from viskopic.config import get_settings
    get_settings.cache_clear()
    # The worker never ran: the student is not kept waiting past the limit.
    q2 = pub(client, "POST", tok, "/next", session).json()["question"]
    assert q2["position"] == 2 and q2["followup"] is False
    drain()                                   # the late job finds a decision already made and adds nothing
    with owner_conn() as c:
        assert c.execute("SELECT count(*) FROM viva_questions WHERE invitation_id = %s AND parent_id IS NOT NULL",
                         (inv["id"],)).fetchone()[0] == 0
    # Timed out without an answer: "no answer", no model call.
    real_now = viva_service.now
    monkeypatch.setattr(viva_service, "now", lambda: real_now() + timedelta(minutes=10))
    assert pub(client, "POST", tok, "/next", session).json()["done"] is True
    monkeypatch.setattr(viva_service, "now", real_now)
    rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=viewer).json()
    assert [q["followup_decision"]["reason"] for q in rep["questions"]] == ["timeout", "no_answer"]


def test_spoken_followup_waits_for_the_transcript(client, tenant_factory, fake_visq, monkeypatch):
    f = FakeAI(text=SHORT)
    monkeypatch.setenv("VISKOPIC_VIVA_AI_PROVIDER", "fake")
    from viskopic.config import get_settings
    get_settings.cache_clear()
    ai.set_provider(f)
    try:
        t = tenant_factory()
        _, case_id, reviewer, viewer = make_case(client, t)
        inv = invite(client, reviewer, case_id, questions=visq_questions(client, reviewer, case_id, n=1),
                     followups=True)
        tok = token_of(inv["test_link"])
        session = pub(client, "POST", tok, "/start").json()["session"]
        q1 = pub(client, "POST", tok, "/next", session).json()["question"]
        assert pub(client, "PUT", tok, f"/answers/{q1['id']}/audio", session, content=WEBM).status_code == 200
        assert pub(client, "POST", tok, "/next", session).json()["waiting"] is True
        drain()                                # transcription, then the follow-up decision from the transcript
        f1 = pub(client, "POST", tok, "/next", session).json()["question"]
        assert f1["followup"] is True
        assert pub(client, "PUT", tok, f"/answers/{f1['id']}/audio", session, content=WEBM).status_code == 200
        assert pub(client, "POST", tok, "/next", session).json()["done"] is True
        drain()
        rep = client.get(f"/api/viva-invitations/{inv['id']}/report", headers=viewer).json()
        assert [q["source"] for q in rep["questions"]] == ["visq", "visq_followup"]
        assert all(q["transcript"]["text"] == SHORT for q in rep["questions"])
    finally:
        ai.set_provider(None)
        get_settings.cache_clear()


def test_followups_need_visq_and_transcription(client, tenant_factory):
    t = tenant_factory()
    _, case_id, reviewer, _ = make_case(client, t)
    r = client.post(f"/api/cases/{case_id}/viva-invitations", headers=reviewer, json={
        "kind": "reasoning", "answer_format": "typed", "email": "s@example.ac.uk", "followups": True,
        "questions": [{"text": "What is the main argument of your essay?", "source": "template"}]})
    assert r.status_code == 422 and "Visq" in r.json()["detail"]


def test_visq_can_run_on_openai(monkeypatch):
    """VISKOPIC_VISQ_PROVIDER=openai: Visq's calls go to OpenAI's Responses API, not stored, no temperature."""
    from viskopic.config import get_settings
    monkeypatch.setenv("VISKOPIC_ALLOW_EXTERNAL_AI", "true")
    monkeypatch.setenv("VISKOPIC_VISQ_PROVIDER", "openai")
    monkeypatch.setenv("VISKOPIC_OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    sent = []

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "completed", "model": "gpt-5.4-mini-2026-03-17", "output": [
                {"type": "reasoning"},
                {"type": "message", "content": [{"type": "output_text", "text": '{"question": "Why?"}'}]}]}

    def fake_post(url, json, headers, timeout):
        sent.append((url, json, headers))
        return Resp()

    monkeypatch.setattr(visq.httpx, "post", fake_post)
    try:
        assert visq.enabled() and "openai gpt-5.4-mini-2026-03-17" in visq.engine_label()
        c = visq.client()
        assert isinstance(c, visq.OpenAIClient)
        res = c.chat("SYSTEM", "USER", temperature=0.4, max_tokens=100)
        assert res.ok and res.text == '{"question": "Why?"}'
        url, body, headers = sent[0]
        assert url.endswith("/responses") and headers["Authorization"] == "Bearer sk-test"
        assert body["store"] is False and "temperature" not in body
        assert body["instructions"] == "SYSTEM" and body["input"] == "USER"
        monkeypatch.setenv("VISKOPIC_OPENAI_API_KEY", "")
        get_settings.cache_clear()
        assert not visq.enabled()                    # no key, no Visq
    finally:
        get_settings.cache_clear()
