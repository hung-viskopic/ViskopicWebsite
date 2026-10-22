"""Tenancy, operator isolation, delegated roles and the audit log."""
import psycopg
import pytest

from .conftest import OPERATOR, app_conn, auth, owner_conn


def setup_course(client, t, dept="Law", code="LAW101"):
    d = client.post("/api/departments", headers=t.owner, json={"name": dept})
    assert d.status_code == 201, d.text
    c = client.post("/api/courses", headers=t.owner,
                    json={"department_id": d.json()["id"], "code": code, "title": f"{dept} intro"})
    assert c.status_code == 201, c.text
    return d.json()["id"], c.json()["id"]


def test_owner_invited_by_operator_can_log_in(client, tenant_factory):
    t = tenant_factory("Bristol")
    me = client.get("/api/me", headers=t.owner).json()
    assert me["kind"] == "user"
    assert me["permissions"]["is_owner"] is True
    assert me["roles"][0]["scope_name"] == "Bristol"


def test_unknown_login_is_rejected(client, tenant_factory):
    tenant_factory()
    r = client.get("/api/me", headers=auth("stranger", "stranger@example.com"))
    assert r.status_code == 403


def test_invitation_needs_verified_email(client, tenant_factory):
    t = tenant_factory()
    client.post("/api/users", headers=t.owner, json={"email": f"unverified@{t.slug}.ac.uk"})
    r = client.get("/api/me", headers=auth("sub-unverified", f"unverified@{t.slug}.ac.uk", verified=False))
    assert r.status_code == 403


def test_bad_token_is_rejected(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_operator_has_no_access_to_institution_data(client, tenant_factory):
    t = tenant_factory()
    setup_course(client, t)
    for path in ("/api/students", "/api/courses", "/api/documents", "/api/users", "/api/audit", "/api/departments"):
        assert client.get(path, headers=OPERATOR).status_code == 403, path
    # The operator sees only metadata about institutions.
    rows = client.get("/api/platform/institutions", headers=OPERATOR).json()
    row = next(r for r in rows if r["id"] == t.id)
    assert set(row) == {"id", "name", "slug", "status", "created_at", "user_count", "document_count",
                        "failed_jobs", "owner_count"}


def test_operator_cannot_keep_adding_owners(client, tenant_factory):
    t = tenant_factory()
    r = client.post(f"/api/platform/institutions/{t.id}/owners", headers=OPERATOR, json={"email": f"second@{t.slug}.ac.uk"})
    assert r.status_code == 201
    r = client.post(f"/api/platform/institutions/{t.id}/owners", headers=OPERATOR, json={"email": f"third@{t.slug}.ac.uk"})
    assert r.status_code == 409


def test_cross_tenant_isolation_api(client, tenant_factory):
    a, b = tenant_factory("Uni-A"), tenant_factory("Uni-B")
    _, course_a = setup_course(client, a)
    s = client.post("/api/students", headers=a.owner, json={"external_ref": "A123", "enrol_course_id": course_a})
    assert s.status_code == 201
    assert client.get("/api/courses", headers=b.owner).json() == []
    assert client.get("/api/students", headers=b.owner).json() == []
    assert client.get(f"/api/courses/{course_a}", headers=b.owner).status_code == 404
    assert client.get(f"/api/students/{s.json()['id']}", headers=b.owner).status_code == 404
    # B can't enrol A's student into anything, or look them up by reference.
    assert client.get("/api/students/lookup", params={"ref": "A123"}, headers=b.owner).status_code == 404


def test_cross_tenant_isolation_database(client, tenant_factory):
    """Even raw SQL as the application role sees nothing outside the tenant context."""
    a, b = tenant_factory("Uni-A"), tenant_factory("Uni-B")
    setup_course(client, a)
    with app_conn() as c:
        assert c.execute("SELECT count(*) FROM courses").fetchone()[0] == 0  # no context at all
        c.execute("SELECT set_config('app.institution_id', %s, false)", (b.id,))
        assert c.execute("SELECT count(*) FROM courses WHERE institution_id = %s", (a.id,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM institutions").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO departments (institution_id, name) VALUES (%s, 'Sneaky')", (a.id,))


def test_delegated_administration(client, tenant_factory):
    t = tenant_factory()
    law, law101 = setup_course(client, t, "Law", "LAW101")
    eng, eng101 = setup_course(client, t, "Engineering", "ENG101")
    head_id, head = t.add_user(client, "head-of-law")
    prof_id, prof = t.add_user(client, "prof")

    assert client.post("/api/role-assignments", headers=t.owner, json={
        "user_id": head_id, "role": "department_admin", "scope_type": "department", "scope_id": law}).status_code == 201

    # Department admin can make a reviewer in their own department...
    r = client.post("/api/role-assignments", headers=head, json={
        "user_id": prof_id, "role": "reviewer", "scope_type": "course", "scope_id": law101})
    assert r.status_code == 201, r.text
    # ...but not in another department, and cannot create owners.
    assert client.post("/api/role-assignments", headers=head, json={
        "user_id": prof_id, "role": "reviewer", "scope_type": "course", "scope_id": eng101}).status_code == 403
    assert client.post("/api/role-assignments", headers=head, json={
        "user_id": prof_id, "role": "institution_owner", "scope_type": "institution", "scope_id": t.id}).status_code == 403
    # Role/scope mismatch is rejected.
    assert client.post("/api/role-assignments", headers=t.owner, json={
        "user_id": prof_id, "role": "reviewer", "scope_type": "department", "scope_id": law}).status_code == 422

    # The reviewer only sees their own course.
    codes = [c["code"] for c in client.get("/api/courses", headers=prof).json()]
    assert codes == ["LAW101"]
    assert client.get(f"/api/courses/{eng101}", headers=prof).status_code == 404
    # Reviewers cannot manage people.
    assert client.get("/api/users", headers=prof).status_code == 403


def test_last_owner_cannot_be_removed(client, tenant_factory):
    t = tenant_factory()
    me = client.get("/api/me", headers=t.owner).json()
    owner_ra = me["roles"][0]["id"]
    assert client.delete(f"/api/role-assignments/{owner_ra}", headers=t.owner).status_code == 422


def test_audit_log_records_and_is_append_only(client, tenant_factory):
    t = tenant_factory()
    setup_course(client, t)
    actions = [e["action"] for e in client.get("/api/audit", headers=t.owner).json()]
    assert "course.created" in actions and "department.created" in actions
    assert "institution.created" in actions  # the operator's own actions are visible to the institution
    with app_conn() as c:
        c.execute("SELECT set_config('app.institution_id', %s, false)", (t.id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE audit_log SET action = 'x'")
    with owner_conn() as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("DELETE FROM audit_log")


def test_support_access_requires_customer_grant(client, tenant_factory):
    t = tenant_factory()
    url = f"/api/platform/institutions/{t.id}/support/jobs"
    assert client.get(url, headers=OPERATOR).status_code == 403
    g = client.post("/api/support-grants", headers=t.owner, json={"reason": "upload failures", "hours": 2})
    assert g.status_code == 201
    assert client.get(url, headers=OPERATOR).status_code == 200
    assert client.delete(f"/api/support-grants/{g.json()['id']}", headers=t.owner).status_code == 200
    assert client.get(url, headers=OPERATOR).status_code == 403


def test_suspended_institution_is_locked_out(client, tenant_factory):
    t = tenant_factory()
    client.post(f"/api/platform/institutions/{t.id}/status", headers=OPERATOR, json={"status": "suspended"})
    assert client.get("/api/me", headers=t.owner).status_code == 403
    client.post(f"/api/platform/institutions/{t.id}/status", headers=OPERATOR, json={"status": "active"})
    assert client.get("/api/me", headers=t.owner).status_code == 200


def test_student_identity_is_encrypted_at_rest(client, tenant_factory):
    t = tenant_factory()
    _, course = setup_course(client, t)
    s = client.post("/api/students", headers=t.owner,
                    json={"external_ref": "SECRET-998877", "display_name": "Ada Lovelace", "enrol_course_id": course}).json()
    assert s["external_ref"] == "SECRET-998877" and s["pseudonym"].startswith("S-")
    with owner_conn() as c:
        raw = c.execute("SELECT external_ref_enc, display_name_enc FROM students WHERE id = %s", (s["id"],)).fetchone()
    assert b"SECRET-998877" not in bytes(raw[0]) and b"Ada" not in bytes(raw[1])
    # Duplicate reference is detected through the keyed hash.
    r = client.post("/api/students", headers=t.owner, json={"external_ref": "secret-998877 ", "enrol_course_id": course})
    assert r.status_code == 409
