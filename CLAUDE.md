# Viskopic — context for AI-assisted development

Read this first, then `docs/design.md` (full design) and `docs/decisions/` (why things are the way they are).

## What Viskopic is

B2B SaaS for universities. It checks whether a student's submission is consistent with that student's **own**
past writing, explains the result with evidence, and supports a human-led viva and decision. It is not a generic
"AI detector" and never makes the final call.

## Non-negotiable product rules

1. **Evidence, not bare scores.** Every result has: why (template-generated, checkable statements with the
   observed value vs the student's baseline mean/spread), where (character spans to highlight), how sure
   (confidence from baseline size/supervision). No free LLM text in evidence statements.
2. **Scores route, never convict.** The evaluator suggests: no action / style viva / reasoning viva. Only a human
   records an outcome, with a rationale. Appeals go to a different reviewer.
3. **Compare the student with themselves.** Baseline needs ≥3 completed essays and ≥1,500 words; supervised
   essays raise confidence. A submission joins the baseline only after its case is cleared.
4. **Viskopic must not be able to access university data.** Enforced technically (RLS, per-tenant keys), not by
   policy. Never add a code path that gives the platform operator tenant data.
5. **Reproducible and audited.** Store pipeline + threshold (θ) versions with results; append-only audit log.

## Roles (delegated administration)

Platform operator (Viskopic staff) → creates institution, invites first owner(s), sees only counts and
(with a customer-granted, time-limited support grant) job health.
Institution owner → department admin → reviewer / viewer (course scope); appeals reviewer (institution scope).
Roles are `(user, role, scope)` rows in `role_assignments`. You can only grant within your own scope. Last
owner can't be removed. Users sign in via OIDC; invitations match a **verified** email on first login.

## Architecture (localhost, Docker Compose in `app/`)

- `app/web` — Next.js 16 + React 19 + TypeScript, keycloak-js (Auth Code + PKCE). Port 3000.
- `app/backend` — Python 3.13, FastAPI (API, port 8000) and `viskopic.worker` (same codebase).
- `app/backend/viskopic/analysis/` — pipeline contract (`base.py`), Δ adapter (`consistency.py`), document signals
  and α/β placeholders (`signals.py`), profiles/cases/runs orchestration (`service.py`).
- `app/backend/vendor/consistency_v13/` — the teammates' frozen V1.3 style model and their `website_adapter.py`,
  vendored **byte-for-byte**. Never edit, reformat or re-save them (the model hash-checks itself; we check the
  adapter's SHA-256, `ADAPTER_SHA256`). Update by re-copying both and bumping `VENDOR_COMMIT` (+ `ADAPTER_SHA256`)
  in `consistency.py` and `VENDORED.md`. Scoring goes through `website_adapter.score_for_website`, never
  `scorer.score_text` directly.
- PostgreSQL 16 + pgvector. Host port 15432. Job queue = `jobs` table with `FOR UPDATE SKIP LOCKED`.
- SeaweedFS as local S3 (port 8333). MinIO is NOT used (images withdrawn in 2026). AWS S3 in production.
- Keycloak as local stand-in for each university's SSO. Host port 18080 (8080 clashed on the dev PC).
  Branded login theme in `app/keycloak/themes/viskopic` (CSS over keycloak.v2, bundled fonts); realm in `app/keycloak/import`.
- Host ports are configurable in `app/.env` (`POSTGRES_HOST_PORT`, `KEYCLOAK_HOST_PORT`, `API_HOST_PORT`, `S3_HOST_PORT`).

## Security model — keep these intact

- Every tenant table has `institution_id` + RLS policy `institution_id = app_institution_id()`.
  `db.tenant_tx(institution_id)` sets it per transaction. The app connects as `viskopic_app` (not owner, no
  BYPASSRLS). Operators use `db.system_tx()` and only the `platform_*` SECURITY DEFINER functions.
- New tenant tables: add `institution_id`, add them to the RLS loop/policies and grants in a new migration.
- Envelope encryption (`viskopic/crypto.py`): per-institution data key wrapped by a `KeyProvider`
  (local master key now, university's own KMS later). Student ref/name, original files and extracted text are
  encrypted, with the record id as associated data. Student lookup uses a keyed HMAC.
- Students are pseudonymous (`S-XXXXXXXX`); analysis code must only see pseudonyms/ids.
- Uploads: type from magic bytes, 10 MiB, zip-bomb/macro checks, sandboxed extraction (rlimits), SHA-256
  verified before extraction. Hidden text (white/tiny/hidden/invisible PDF) is excluded from text and recorded
  as a document signal (also a prompt-injection defence).
- Audit log is append-only (no UPDATE/DELETE grant + trigger). Log role changes, uploads and text views.
- Baseline profiles are encrypted artifacts in object storage (AAD `baseline_profile:<id>`), hash recorded in
  `baseline_profiles.artifact_sha256` and checked before loading. Only `baseline` documents go into a profile.
- Evidence items and analysis runs hold numbers, template statements and offsets only — never essay text. The Δ
  model's diagnostics include sampled text: use it to locate spans, then drop it.
- Removing a document (owners only, `POST /documents/{id}/remove`, reason required) deletes file, text and
  signals but keeps the row as a tombstone; anything built from it (profiles, cases) is retired via
  `analysis.service.on_document_removed`. Worker jobs must re-check `status <> 'removed'` / case `withdrawn`
  before saving results.
- Δ only scores quality-reviewed texts: `document_quality_reviews` rows (append-only, bound to the SHA-256 of the
  exact extracted text, computed on the server). Never derive a review from a browser flag or create a "pass"
  just to get a result. States without a verdict are `not_eligible` runs with `details.website_status` and no
  score. Every scored result is labelled experimental; show raw/relative distance separately; never present the
  score as a percentage or probability.
- The Δ model keeps every loaded profile in one in-process registry (all tenants). Never expose
  `scorer.list_profiles()` directly; `consistency.demo_profiles()` filters to the built-in research profiles.

## Status

- ✅ Milestones 1–3: foundations (tenancy, RLS, SSO, delegated roles, audit, support grants), academic records,
  intake (upload, encrypted storage, queue, extraction with retries, document signals).
- ✅ Milestones 4–5: cases, versioned baseline profiles, pipeline contract, **real Δ (V1.3 consistency)**,
  document-signals pipeline, α/β "unavailable" placeholders, evidence report UI, demonstration profiles for local
  demos. Owners can remove wrong uploads. Δ runs through the model team's website contract (commit `d102392`)
  with quality reviews. 32 backend tests (the analysis tests run the real model).
- Δ V1.3 needs ≥8 baseline documents of ≥1,650 usable words each and ≥1,650-word submissions — stricter than the
  product rule (3 essays / 1,500 words). Don't lower these constants: a student configuration must come from the
  model team with its own validation.
- 🔜 Next: evaluator with versioned θ → viva questions linked to evidence → decisions with evidence snapshot →
  appeals → real α and β pipelines.

## Working conventions

- Run backend tests against a real Postgres (RLS must actually be exercised):
  `VISKOPIC_TEST_ADMIN_URL=postgresql://viskopic:viskopic@localhost:15432/postgres pytest` in `app/backend`.
- Migrations: add a new numbered file in `app/backend/migrations/`; never edit an applied one.
- Keep `docs/design.md` and this file updated when the design changes; record notable choices in `docs/decisions/`.
- Owner develops on Windows (D:\VISKOPIC\ViskopicWebsite) with Docker Desktop. Keep `.sh` files LF.
- Never load real student work until a DPIA, DPA and KMS-backed keys are in place.
- `VISKOPIC_ALLOW_DEMO_PROFILES` and `VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY` must be false anywhere outside local
  development.
