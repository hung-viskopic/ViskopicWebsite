# Viskopic application

Local development foundation: React/TypeScript, NestJS, PostgreSQL, private S3-compatible storage (MinIO), and a Python worker.

Production deployment is defined from the parent repository's `render.yaml`. Render runs the web gateway, private API, and background worker in Frankfurt. Supabase supplies PostgreSQL and private S3-compatible storage in the same region. Copy no secrets into source control; use `.env.production.example` only as a field reference and enter values in Render's secret prompts.

## Run

With Docker Desktop running, from this directory:

```sh
docker compose up --build -d
```

Open http://localhost:4301 and enter `local-viskopic-development-key`. You can override credentials using `.env.example` as the reference. Browser credentials are held in memory and cleared on reload. All published ports are bound to loopback.

Upload a `.pdf`, `.docx`, or UTF-8 `.txt` file of up to 10 MiB. The API validates its extension and file signature, stores the original in a private bucket, records its checksum, and queues extraction. The worker verifies the checksum, extracts readable text, counts whitespace-delimited words, and stores the result. The app polls processing status and shows the event history. Image-only PDFs require OCR and currently fail with a clear no-readable-text result. Legacy `.doc` files are not supported. No authorship/detection model is implemented; word counts are not detection scores.

## Student workspaces

Create a course or cohort, then student records with a unique reference within that course and an optional display name. Upload documents inside a student record to associate them automatically. Existing uploads remain under Unassigned; use Move document to assign them to a student or return them to Unassigned. Course and student lists support search and an archived-record filter.

Courses and student records can be edited, archived, and restored. Archived courses block new students and document intake; archived students block document intake. Only empty records can be deleted. Moving documents preserves their extraction results and provenance. Archiving does not delete stored files.

Activity history records record creation, changes, archive/restore, deletion, document assignment, student-detail reads, and document-detail reads. List polling is not audited. Events identify the shared development credential, not a named individual. They are not a tamper-proof compliance log. Authorship baselines and comparison models are not implemented.

The API applies the additive, idempotent `database/002_records.sql` migration at startup under a database lock, including on existing database volumes. No database reset is needed.

Run `node scripts/records-smoke.mjs` against the running app to check the record lifecycle and student-linked extraction. This creates or reuses a clearly marked synthetic demo course, student, and document.

## Components

- `apps/web`: reviewer intake application
- `apps/api`: authenticated local API and storage orchestration
- `services/worker`: Python extraction worker
- `database/001_initial.sql` and `database/002_records.sql`: additive startup migrations for local and hosted PostgreSQL

PostgreSQL jobs use `FOR UPDATE SKIP LOCKED`, a two-minute lease and a maximum of three attempts. Attempt numbers prevent stale workers from overwriting newer results. Research training data ingestion is not enabled. Queue payloads reference private objects instead of duplicating file bytes.

## Checks

```sh
npm ci
npm run build
npm test
python -m unittest discover -s services/worker
node scripts/smoke.mjs
docker compose exec -T worker python integration_smoke.py
```

The smoke checks require the Docker services and create synthetic text and DOCX submissions which remain in the local database. They check authentication, format rejection, upload, extraction, checksums, and event history.

## Development boundaries

This is a local prototype, not an institution-ready deployment. It has a single development organisation and shared access key, not user authentication or verified tenant isolation. Local service traffic is HTTP and volumes are not application-encrypted. Do not load real student records yet.

Before an institutional pilot: replace development access with OIDC and organisation roles; introduce versioned schema migrations, retention/deletion and backup restore tests; use TLS and managed storage credentials; add upload rate limiting and document scanning for additional file formats; add case notes and decision records; agree a versioned inference contract with the research founder. Processing events are operational history, not a tamper-proof compliance audit log. A crash between object upload and database insertion may leave an orphan requiring reconciliation.

The public marketing website remains a separate project. This directory has not been pushed or deployed externally.
