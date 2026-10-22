# Viskopic app (MVP)

Web application for Viskopic: universities manage staff access, courses and students, and collect student
writing (baseline essays and submissions) for authorship analysis. This milestone covers **foundations and
intake**. Analysis pipelines, evidence reports, vivas and decisions build on top of it — see
[`docs/design.md`](../docs/design.md).

| Part | Tech | Folder |
|---|---|---|
| Web app | Next.js 16, React 19, TypeScript | `web/` |
| API and worker | Python 3.13, FastAPI, psycopg 3 | `backend/` |
| Database | PostgreSQL 16 + pgvector, row-level security | `backend/migrations/` |
| File storage | SeaweedFS (S3-compatible; AWS S3 later) | `seaweedfs/` |
| Sign-in | Keycloak (stands in for each university's SSO), Viskopic-branded theme | `keycloak/` |

## Run it

Needs Docker Desktop.

```sh
cd app
cp .env.example .env
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"   # paste into VISKOPIC_LOCAL_MASTER_KEY_B64
docker compose up --build
```

First start takes a few minutes (Keycloak is slow to boot). Then open http://localhost:3000.

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Keycloak admin | http://localhost:18080 (admin / admin) |
| S3 endpoint | http://localhost:8333 (key `viskopic`, secret in `seaweedfs/s3.json`) |

All ports are bound to `127.0.0.1` only. Postgres is on 15432 and Keycloak on 18080 to avoid ports Windows commonly reserves; all host ports except the web app's can be changed in `.env`.

**"ports are not available ... forbidden by its access permissions" on Windows** means something else is using that
port or Windows has reserved it. Check reserved ranges with `netsh interface ipv4 show excludedportrange protocol=tcp`.

## Try the whole flow

Demo sign-ins (password `viskopic` for all) are created in Keycloak. Having a Keycloak login is **not** enough to
get in: each person must also be invited inside Viskopic, exactly as it will work with a real university.

1. **Platform operator** — sign in as `ops@viskopic.local`. Create an institution, e.g. *Demo University*, slug
   `demo-uni`, owner email `owner@demo-uni.ac.uk`. Sign out. (Note you can see counts, but nothing inside.)
2. **Institution owner** — sign in as `owner@demo-uni.ac.uk`.
   - Departments: add *School of Law*.
   - Courses: add `LAW1001`.
   - People & roles: invite `head.law@demo-uni.ac.uk` and make them *Department admin* of School of Law.
3. **Department admin** — sign in as `head.law@demo-uni.ac.uk`. Invite `prof.smith@demo-uni.ac.uk` and
   `viewer@demo-uni.ac.uk`; make them *Reviewer* and *Viewer* on `LAW1001`. Try granting a role on a course in
   another department: it's refused.
4. **Reviewer** — sign in as `prof.smith@demo-uni.ac.uk`. Open `LAW1001`, add a student, upload a few `.docx`,
   `.pdf` or `.txt` essays as baseline, then one as a submission. Watch them go *queued → completed*. Open a
   document to see word count, file metadata and any hidden text that was found.
5. **Isolation check** — as the operator, create a second institution with owner `owner@other-uni.ac.uk`, sign in
   as that owner, and confirm none of Demo University's data is visible.
6. **Audit** — as `owner@demo-uni.ac.uk`, open *Settings & audit* to see every action, including the operator's.

Test hidden-text detection: in Word, add a line of white text (or 1pt text) to an essay before uploading.

## Try the evidence report

Every submission opens a **case** once its text is extracted, and the pipelines run automatically. Open the
submission to see the evidence report: the Δ consistency result with its evidence items, document signals, and
α/β marked as not available yet. "Show in text" highlights the passages an item is about.

The Δ model (V1.3, from the model team) compares the submission with a **profile of the student's own writing**.
It currently needs **8 baseline documents of at least 1,650 words each**, and submissions of at least 1,650 words.
The profile builds itself once those are uploaded (see the student's page; it takes a minute or so) and any
waiting cases are analysed again.

Before the model compares anything, a reviewer **quality-reviews** each text: open the document, read the
extracted text and record *English prose / clean extraction* under *Quality review*. Every baseline document and
the submission need one; the case is analysed again after each review. Every scored result is labelled
experimental. For quick local trials, `VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY=true` in `app/.env` scores
unreviewed texts too, clearly labelled as an unreviewed demonstration (demonstration profiles need it, since
their research papers can't be reviewed here). Keep it off anywhere outside local development.

No essays that long to hand? On the student's page, under *Consistency profile*, choose **Use a demonstration
profile**. That compares the student's submissions with one of the model's research authors instead, so you can
see the whole report. It is clearly labelled as a demonstration and is switched off with
`VISKOPIC_ALLOW_DEMO_PROFILES=false`, which must be the setting anywhere outside local development.

## Removing a wrong upload

Signed in as the institution owner, open the document and choose **Remove document…**, then give a reason. The
file and its text are deleted; the page keeps a note of who removed it and why, and the audit log records it. If it
was a baseline essay, the student's profile is rebuilt without it and their submissions are analysed again; if it
was a submission, its case is withdrawn.

## Can't sign in after a restart?

If Keycloak's data was wiped (e.g. `docker compose down -v`) while Viskopic's database was kept, invited users
are still linked to their old Keycloak IDs and are refused. Demo users now have fixed IDs so this shouldn't recur;
to recover, unlink them so the next sign-in re-links by verified email:

```sh
docker compose exec postgres psql -U viskopic -d viskopic -c "UPDATE users SET sso_subject = NULL, status = 'invited' WHERE status = 'active';"
```

## Sign-in page theme

The Keycloak sign-in page uses a custom theme in `keycloak/themes/viskopic/login/` (CSS over Keycloak's v2 theme,
bundled OFL fonts, no third-party requests). Keycloak runs in dev mode, so CSS edits show after a browser refresh.
In production the same theme is used with Keycloak brokering each university's own login.

## Run the tests

The backend tests run against a real PostgreSQL so row-level security is genuinely exercised.

```sh
cd app/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
# with the compose stack running, use its Postgres as the test server:
VISKOPIC_TEST_ADMIN_URL=postgresql://viskopic:viskopic@localhost:15432/postgres pytest
```

They cover: operator isolation, cross-tenant isolation at API and raw-SQL level, delegated role rules, last-owner
protection, the append-only audit log, support-access grants, encryption at rest, upload validation, the intake
gate, extraction of DOCX/PDF/TXT with hidden-text and metadata signals, retries and checksum verification, and
the analysis stage: profile enrolment with the real Δ model, encrypted profile artifacts, cases, evidence
items and spans, quality reviews and the model's not-scored states, demonstration profiles, re-analysis, and
tenant isolation of all analysis tables.

Frontend: `cd app/web && npm ci && npm run typecheck && npm run build`.

## How access works (short version)

- Viskopic creates an institution and invites its first owner. From then on the university manages its own
  people; Viskopic has no account inside it.
- Roles are `(role, scope)` assignments: owner (institution), department admin (department), reviewer and
  viewer (course), appeals reviewer (institution). You can only grant roles within your own scope.
- Every query runs with the caller's institution set on the database transaction; PostgreSQL row-level security
  hides everything else. The operator's connection has no institution set, so it sees nothing.
- Student numbers, names, original files and extracted text are encrypted with a per-institution key.

Full detail: [`docs/design.md`](../docs/design.md) and [`docs/decisions/`](../docs/decisions/).

## Not production-ready yet

Local development only. Before a pilot: real university SSO, a KMS-backed key provider in the university's
account, TLS, malware scanning (ClamAV), upload rate limiting, backups and restore tests, retention and deletion
jobs, a DPIA and a penetration test. Do not load real student work.
