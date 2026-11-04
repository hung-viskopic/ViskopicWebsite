# Viskopic MVP design

Viskopic helps universities check whether a piece of student writing is consistent with that student's own past
work, and supports a fair human review when it isn't. This document is the agreed design for the MVP. The code
for the first milestone (foundations and intake) lives in [`app/`](../app/).

Status key: ✅ built · 🔜 next milestones

Reports use plain names: **Writing style** (Pipeline 1, Δ, the V1.3 consistency model) and **AI comparison**
(Pipeline 2, interchangeability), plus **Document signals**. The α (authenticity) and β (language) placeholders
were dropped: the team no longer plans them. Old placeholder runs stay in the database (append-only) but are hidden.
Vivas are sent by a reviewer; a flag never sends one automatically.

## 1. Principles

1. **Evidence, not bare scores.** Every result explains *why* (checkable observations), *where* (highlighted
   passages) and *how sure* (confidence from the size of the student's baseline). A reviewer should understand
   the case from the evidence alone.
2. **Scores route, they never convict.** The system suggests a next step. Only a human records an outcome.
3. **Compare students with themselves.** The baseline is the student's own writing, which also reduces bias
   against non-native writers compared with generic AI detectors.
4. **Viskopic cannot see university data.** Enforced by the database and encryption keys, not just policy.
5. **Everything is reproducible and audited.**

## 2. Case lifecycle

```mermaid
flowchart TD
    A[Submission uploaded] --> B[Text extracted ✅]
    B --> CS[Case opened ✅]
    CS --> C{Baseline profile ready? ✅}
    C -- no --> W[Awaiting baseline ✅]
    W -- profile built --> D
    C -- yes --> QR{Texts quality-reviewed? ✅}
    QR -- no --> RV[Awaiting review ✅]
    RV -- reviews recorded --> D
    QR -- yes --> D[Analysis: writing style ✅ · AI comparison ✅]
    D --> E[Evidence report ✅]
    E -- no concern --> X[Closed]
    E -- concern --> F[Viva: style or reasoning ✅ MVP]
    F --> G[Human decision with rationale 🔜]
    G --> H[Appeal, different reviewer 🔜]
    G --> I[Outcome recorded]
    I -- cleared --> J[Added to baseline]
```

### Business rules

| Rule | Detail | Status |
|---|---|---|
| Intake gate | Upload only when student, course and enrolment are all active | ✅ |
| File checks | PDF, DOCX or UTF-8 TXT up to 10 MiB; type from content not name; no macros; zip-bomb limits | ✅ |
| Integrity | SHA-256 of the original stored; re-verified before extraction | ✅ |
| Retries | Up to 3 attempts with backoff; unreadable files fail at once with a clear reason | ✅ |
| Baseline eligibility | Product rule: ≥3 completed baseline essays and ≥1,500 words (summary shown). The Δ V1.3 model currently needs more: ≥8 baseline documents of ≥1,650 usable words each, and ≥1,650 words per submission. A validated student configuration is research work for the model team | ✅ |
| Confidence | low / medium / high from baseline size and how much is supervised. Δ: 8–9 documents low, 10+ medium, 10+ with ≥2 supervised high; demonstration profiles always low | ✅ |
| Profile versions | A profile is an immutable, versioned, encrypted artifact built only from `baseline` documents; a new version is built when the eligible set changes; cases waiting for a baseline are re-analysed when it is ready | ✅ |
| Baseline protection | Submissions are never used in a profile; a submission joins the baseline only after its case is cleared | ✅ / 🔜 (clearing) |
| Quality review (Δ) | The model only scores when a reviewer has checked the submission and **every** baseline document: English prose, clean extraction. A review is stored server-side, bound to the SHA-256 of the exact extracted text, append-only (a new review supersedes). Non-English / mixed / failed extraction blocks scoring; a review of an older text is out of date and blocks too. `VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY` (local development only) scores unreviewed texts as a labelled "unreviewed demonstration" | ✅ |
| Reproducibility | Each analysis run stores the pipeline version (Δ: model 1.3 + vendored commit), the frozen threshold and the profile version; re-runs add rows, nothing is overwritten | ✅ (evaluator θ 🔜) |
| Frozen evidence | A decision stores a snapshot of the evidence the reviewer saw | 🔜 |
| Separation of duties | Whoever decided a case cannot hear its appeal | 🔜 |
| Removing a wrong upload | Institution owners only, with a reason. Deletes the original file, extracted text and file signals; the document row stays as a tombstone (who, when, why) and the audit log records it. A removed baseline document retires every profile version built from it (artifacts deleted), queues a new profile from what's left and re-analyses the student's cases; a removed submission's case is withdrawn. Earlier analysis runs are kept (numbers and template text only). Once decisions exist, a document with a recorded decision will not be removable | ✅ |

### Evidence model ✅

Each pipeline returns a score plus **evidence items**: category (style, semantic, regeneration, document),
a template-generated statement, the observed value against the student's baseline mean and spread, the character
ranges to highlight, and its contribution to the overall result. Statements come from templates, never free LLM
text, so they are accurate and repeatable.

Evidence items store numbers, template statements and character offsets only, never essay text. Spans are offsets
into our extracted text (the Δ model samples from its own normalised copy, so passages are located again; status
`exact` or `approximate`).

| Pipeline | Status | Evidence it produces |
|---|---|---|
| **Writing style** — Pipeline 1, Δ consistency | ✅ real model: frozen V1.3 from `VISKOPIC_PROJECT` (`model/v1-3-mvp-handoff`, commit `d102392`), vendored unchanged in `app/backend/vendor/consistency_v13`, called through the model team's website adapter (`viskopic.website-consistency.v1`) | every scored result labelled **experimental**; overall centred score vs frozen threshold with the margin (not a probability or percentage, can be negative, tiny values shown as "near zero"); raw distance and distance ÷ typical baseline distance, shown separately as descriptive diagnostics; the 3 feature groups that differ most; up to 8 features ≥2 spreads from the student's mean in the same direction in ≥2 of the 3 sampled passages, each with observed value, baseline mean and spread, and the passages to highlight |
| Document signals | ✅ | hidden text (white, ≤2pt, hidden formatting, invisible PDF layers); recorded editing time under 1 minute per 100 words. Author names are never copied into statements |
| **AI comparison** — Pipeline 2, interchangeability | ✅ frozen release `p2-interchangeability-v1.1` from `VISKOPIC_PROJECT` (`model/v1-3-mvp-handoff`, commit `9cfe33d`), vendored unchanged in `app/backend/vendor/interchangeability_p2` | labelled **experimental**; score = how much more the opening third (equal word thirds) of the essay's first 250 words shares rare words with its own continuation than AI essays' openings on the same assignment do (below 0 = "interchangeable"; threshold validated on school essays, where it flags about 1 in 10 human essays, and experimental for university essays — an evidence item and a combined-report note say so); the opening highlighted; a note when only GPT comparison essays were used. Needs no baseline. Comparison essays are generated once per assignment from the assignment prompt only (`build_comparison_set` job, `VISKOPIC_P2_GENERATE_COMPARISONS`; GPT with the OpenAI key, plus Qwen and Gemma when `VISKOPIC_P2_OLLAMA_URL` points at an Ollama GPU server, which is the evaluated three-family set); essays under 150 words are recorded as *not eligible* (v1, which needed 9 sentences in 250 words, rejected 44% of university essays) |

**Combined report** (the model team's "either flags" rule, re-implemented over finished runs in
`analysis/combined.py`, stored as a `combined` run): *review recommended* when Δ or Pipeline 2 flags, *no flag*
when what ran raised nothing, *not enough information* when neither ran. Scores are never averaged. It shows each
pipeline's explanation, coverage notes (which kinds of AI essay are left uncovered) and the model team's
interpretation warning. Demonstration profiles never count as the student's style. We don't call their
`CombinedScorer`: it calls V1.3 directly and would skip the website adapter and quality reviews.

**States without a verdict.** The adapter returns `insufficient_evidence` (too few baseline documents or usable
words), `quality_review_required`, `unsupported_text` or `cannot_score` instead of a score. These are stored as
`not_eligible` runs with the state in `details.website_status` and no score, label or evidence: the system never
manufactures a zero. A case waiting only for reviews has status `awaiting_review`.

**Demonstration profiles** (local development only, `VISKOPIC_ALLOW_DEMO_PROFILES`): a reviewer can link a
student to one of the 35 research-author profiles shipped with the model, to try the report before a real
baseline exists. The report says plainly that the comparison is not with the student's writing.

## 3. Roles and delegated administration ✅

```mermaid
flowchart LR
    OP[Platform operator<br/>Viskopic staff] -- creates tenant,<br/>invites first owners --> OW[Institution owner]
    OW --> DA[Department admin]
    OW --> AR[Appeals reviewer]
    DA --> RV[Reviewer]
    DA --> VW[Viewer]
```

| Role | Scope | Can | Cannot |
|---|---|---|---|
| Platform operator | Platform | Create/suspend institutions, invite owners while fewer than two exist, see usage counts, see job health while a support grant is active | See any student, document, text or decision |
| Institution owner | Institution | Everything in the institution, audit log, support grants | — |
| Department admin | Department | Courses, staff roles and students in their department | Act outside their department, create owners |
| Reviewer | Course | Students, assignments, uploads and text quality reviews on the course | Manage people |
| Viewer | Course | Read-only | Change anything |
| Appeals reviewer | Institution | Read-only across courses (appeals 🔜) | — |

- Roles are `(user, role, scope)` assignments, so one person can hold different roles on different courses.
- You can only grant roles within your own scope.
- The last institution owner cannot be removed.
- People sign in with SSO; the invitation is matched to a **verified** email on first sign-in, then bound to the
  SSO subject.

## 4. Database ✅

PostgreSQL. Every tenant table has `institution_id` and a row-level security policy. See
[`app/backend/migrations/0001_foundations.sql`](../app/backend/migrations/0001_foundations.sql).

```mermaid
erDiagram
  INSTITUTIONS ||--o{ DEPARTMENTS : has
  INSTITUTIONS ||--o{ USERS : has
  USERS ||--o{ ROLE_ASSIGNMENTS : holds
  INSTITUTIONS ||--o{ SUPPORT_GRANTS : grants
  DEPARTMENTS ||--o{ COURSES : runs
  COURSES ||--o{ ASSIGNMENTS : sets
  COURSES ||--o{ ENROLMENTS : has
  STUDENTS ||--o{ ENROLMENTS : in
  STUDENTS ||--o{ DOCUMENTS : writes
  ASSIGNMENTS ||--o{ DOCUMENTS : answers
  DOCUMENTS ||--o| DOCUMENT_TEXTS : extracted_to
  DOCUMENTS ||--o| DOCUMENT_SIGNALS : has
  DOCUMENTS ||--o{ JOBS : queued_as
  INSTITUTIONS ||--o{ AUDIT_LOG : records
```

Milestones 4–5 added ([`0002_analysis.sql`](../app/backend/migrations/0002_analysis.sql), [`0003_document_removal.sql`](../app/backend/migrations/0003_document_removal.sql), [`0004_quality_reviews.sql`](../app/backend/migrations/0004_quality_reviews.sql)): `baseline_profiles`
(versioned; encrypted artifact in object storage, SHA-256 in the row), `cases` (one per submission),
`analysis_runs` (append-only for the app role), `evidence_items` (append-only) and `document_quality_reviews`
(append-only; outcome + text digest + who/when, no text). Jobs gained the kinds
`build_profile` and `analyse_case`.

Milestone 6a/6b MVP added ([`0005_viva.sql`](../app/backend/migrations/0005_viva.sql)): `viva_invitations`
(token stored as a SHA-256 hash; student email encrypted; supervision mode and timings frozen at send),
`viva_questions` (frozen copy of what was sent, optional essay passage with offsets), `viva_question_releases`
(server release time and deadline per question), `viva_responses`, `viva_recordings` (encrypted audio in object
storage), `viva_transcripts` (encrypted text plus a numbers-only speech timeline), `viva_session_events` and
`viva_assessments`. All append-only except invitations and recordings. Jobs gained `send_viva_email` and
`transcribe_viva_answer`. `viva_resolve_token` is the only way to turn a student's token into a tenant.

Milestone 6c added ([`0007_visq_and_p2.sql`](../app/backend/migrations/0007_visq_and_p2.sql)): follow-up questions
(`viva_questions.parent_id`, source `visq_followup`), Visq question types and an encrypted reviewer note per question,
`viva_followup_decisions` (append-only, one per main question: follow-up or move on, why, and which engine), and
`assignment_comparison_sets` (Pipeline 2's AI essays per assignment, AI text only). Jobs gained `viva_followup` and
`build_comparison_set`; the worker takes viva jobs first because a student may be waiting.

Visq 2 added ([`0008_visq2.sql`](../app/backend/migrations/0008_visq2.sql)): `viva_invitations.flow`
(`standard` / `visq2`), the full-set switch and extension time; `viva_visq2_states` (Visq's state per invitation,
encrypted, versioned); question source `visq2`; jobs `visq2_prepare` and `visq2_step`. The Visq 1 follow-up
columns and table from 0007 are kept for old vivas but no longer written.

Next milestones add: `segments` (with pgvector embeddings), `evaluator_configs`, `evaluations`, `decisions`,
`appeals`, and the later supervision modes (live, invigilated room, third-party proctor).

### Viva MVP (6a/6b)

A reviewer sends a viva from a submission, in one of two flows.

**Visq 2** (default when external AI is on; local testing with synthetic essays only). The model team's viva engine
(vendored unchanged in `app/backend/vendor/visq_v2`, commit `3d631d8`) runs its world-tree flow
([`0008_visq2.sql`](../app/backend/migrations/0008_visq2.sql)). A worker job reads the essay as a tree of points and
what it takes for granted. During the viva Visq asks clarifying questions only where the essay is silent, then a
timed, typed **extension** (a what-if from outside the essay: what would you keep, drop or add), then a short viva
(3 questions, or 7 with the full set) sampled from fixed types, some with one follow-up, each with a worked example
on another topic on request. Questions depend on earlier answers, so they are written during the viva and not
approved in advance (decision 0009); each is frozen in `viva_questions` (source `visq2`, its step in `qtype`) when
released, with a server deadline. After each answer a `visq2_step` job feeds it into Visq; the page shows "Preparing
the next question", and after `VISKOPIC_VIVA_FOLLOWUP_WAIT_SECONDS` (90 s) the server applies the step offline with
Visq's heuristics. Visq's state is stored encrypted and versioned in `viva_visq2_states`. The report shows every
step with its answer and Visq's reviewer note, which compares the wording of the answers with the essay's structure
(a reading aid, never a score). Answers are typed (paste blocked) or spoken, optionally with the webcam; spoken
answers are transcribed before Visq reads them, and the extension is always typed.

**The reviewer's own questions** (always available): from the template bank or written by the reviewer, approved
before sending, spoken (optionally with webcam) or typed. The rest of this section applies to both flows.

Visq 1 (questions drafted from the essay's argument structure plus answer-driven follow-ups) was briefly used and
is replaced by Visq 2; vivas sent with it keep their questions and follow-up decisions in the report. Visq runs
through OpenRouter with zero data retention required on every call, or on OpenAI with `VISKOPIC_VISQ_PROVIDER=openai`
(see decision 0009), and never writes to disk.

**Full screen** is required by default ([`0009_viva_fullscreen_spoken_visq2.sql`](../app/backend/migrations/0009_viva_fullscreen_spoken_visq2.sql)):
laptop and desktop browsers only; leaving it covers the question until the student returns (the clock keeps
running) and is recorded with the time spent out. Copying the question text is blocked and recorded. See decision 0008.

The student opens an emailed link (no staff login) and answers one question at a time. Rules enforced on the
server: a question is released only after the previous one is answered or timed out (no reading ahead, no going
back); each deadline is measured from release (think time + answer time), with a 10 s grace; one browser session
at a time (opening the link elsewhere takes the session over and is recorded). Spoken answers are recorded in the
browser and transcribed by a worker job when external AI is on; typed answers block paste. With **Record the
webcam too** (default for spoken vivas, [`0006_viva_camera.sql`](../app/backend/migrations/0006_viva_camera.sql),
supervision mode `oral_video`) each answer is one small video (320×240, about 15 fps, about 250 kbps): the student
sees a self-view, the reviewer watches it in the report. The server checks the container for a video track and
records "no camera video" when it is missing. Nothing analyses the video.

The report shows each question, the passage, the recording (played through the API, audited), the automatic
transcript, a speech/silence timeline and neutral signals (left the page, paste attempts, late answers, device
switch). Browser-reported signals are labelled as such. Reviewers rate each answer (consistent / unclear /
concern); a rating is not a decision. Deliberately not built: face, gaze, voice-stress or object detection (see
`docs/decisions/0008-viva-supervision-mvp.md`).

## 5. System (localhost) ✅

```mermaid
flowchart LR
    B[Browser] --> W[Web app<br/>Next.js :3000]
    B -- sign in --> K[Keycloak :18080]
    W -- bearer token --> A[API<br/>FastAPI :8000]
    A -- verify token --> K
    A --> P[(Postgres<br/>RLS + job queue)]
    A -- encrypted files --> M[(SeaweedFS S3 :8333)]
    WK[Worker] -- claim jobs --> P
    WK --> M
    WK --> X[Sandboxed extractor<br/>CPU/memory/time limits]
    WK -- viva emails --> ML[Mailpit :8025]
    S[Student browser<br/>/viva/token] -- token only --> A
```

- One Python codebase runs as both API and worker.
- The job queue is a Postgres table claimed with `FOR UPDATE SKIP LOCKED`, with leases and numbered attempts so
  a stale worker can't overwrite a newer result.
- Files are parsed in a separate process with CPU, memory, file-handle and time limits.
- Pipelines (✅) plug into the worker behind one interface (`viskopic/analysis/base.py`):

```python
class Pipeline(Protocol):
    name: str
    version: str
    def run(self, ctx: AnalysisContext) -> PipelineResult: ...   # score, confidence, evidence items
```

- The Δ model is loaded once per worker process and verifies every asset and frozen-source hash at startup.
  Enrolled profiles are decrypted to a temporary file only to load them, after their recorded hash is checked.
  The model keeps loaded profiles in one in-process registry; nothing that lists profiles may expose it (only the
  built-in research profiles are ever listed).

## 6. Security and "Viskopic cannot see your data" ✅

| Layer | How |
|---|---|
| Tenant isolation | The API sets `app.institution_id` on each transaction; RLS policies hide every other institution's rows. The app's database role is not the table owner and has no `BYPASSRLS`. Tested at API and raw-SQL level. |
| Operator isolation | Operators never get a tenant context. Their only database access is a handful of `SECURITY DEFINER` functions that return metadata (names, counts) or invite owners. |
| Support access | Customer-granted, time-limited (max 7 days), revocable, audited; shows job status and errors only. |
| Encryption | Envelope encryption with a data key per institution. Student numbers, names, original files and extracted text are encrypted before storage. Locally the key-encryption key is in `.env`; in production it lives in the **university's own KMS**, so they can revoke it. |
| Pseudonymisation | Students appear as `S-XXXXXXXX`. Exact-match lookup by student number uses a keyed hash. |
| Audit | Append-only (no UPDATE/DELETE grant, plus a trigger). Records role changes, uploads, text views and operator actions, visible to the institution owner. |
| Uploads | Content-type sniffing, size and zip-bomb limits, macro rejection, sandboxed parsing, checksums. |
| Prompt injection | Hidden text is excluded from the extracted text and reported as a signal. |
| Web | Bearer tokens via OIDC Authorization Code + PKCE; CORS restricted; security headers; ports bound to localhost. |

Planned before a pilot: real university SSO (Keycloak brokering or direct OIDC/SAML), KMS key provider, TLS,
ClamAV, rate limiting, retention/deletion jobs including derived data, backups, DPIA, penetration test, and the
option to deploy into the university's own cloud account.

## 7. Milestones

1. ✅ Foundations: compose stack, migrations, SSO, tenancy + RLS, delegated roles, audit log
2. ✅ Academic records: departments, courses, students, enrolments, assignments
3. ✅ Intake: upload, validation, encrypted storage, queue, sandboxed extraction, retries, document signals
4. ✅ Cases and baselines: case creation on submission, versioned encrypted baseline profiles, eligibility
5. ✅ Pipeline contract, document-signal pipeline, **real Δ (V1.3)** through the model team's website contract with
   quality reviews, α/β placeholders, evidence report UI; **Pipeline 2 (interchangeability)** in the β slot with
   per-assignment AI comparison essays, and the combined either-flags report
6. 🟡 Viva MVP ✅ (invitations, Visq 2 world-tree vivas or the reviewer's own questions, timed
   spoken/typed answers enforced by the server, transcripts, speech timeline, signals, report and ratings). 🔜 Evaluator with versioned θ, live and
   invigilated supervision, decisions, appeals
7. 🔜 An Ollama GPU host for the AI comparison's Qwen and Gemma families (the setting exists); a validated Δ student configuration (fewer, shorter
   baseline essays) from the model team
