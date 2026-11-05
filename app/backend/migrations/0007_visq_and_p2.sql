-- Visq viva questions and follow-ups, and Pipeline 2 (interchangeability) comparison sets.
--
-- Viva: Visq (the model team's viva engine, vendored in vendor/visq_v2) writes the main questions from the
-- submission's argument structure; the reviewer still edits and approves them before sending. After each
-- answer Visq may ask at most one follow-up, decided by a worker job from the typed answer or the transcript.
-- Follow-ups are the one kind of question a reviewer does not approve in advance; they pass Visq's code
-- checks and its proofreading call, are labelled as follow-ups everywhere, and the decision is recorded.
--
-- Pipeline 2: a set of AI-written essays per assignment, generated once from the assignment prompt only
-- (never from student work), reused to score every submission to that assignment.
--
-- Same rules as before: institution_id + RLS on every tenant table; log tables are append-only.

-- ---------------------------------------------------------------- viva: invitations and questions
ALTER TABLE viva_invitations ADD COLUMN followups_enabled boolean NOT NULL DEFAULT false;
-- What wrote the main questions, for the report: e.g. "visq q1.20 openai/gpt-oss-120b (3d631d8)" or "templates".
ALTER TABLE viva_invitations ADD COLUMN question_engine text;

ALTER TABLE viva_questions ADD COLUMN parent_id uuid REFERENCES viva_questions(id);
ALTER TABLE viva_questions ADD COLUMN qtype text;                 -- Visq question type (tradeoff, limit, case, followup)
ALTER TABLE viva_questions ADD COLUMN reviewer_note_ciphertext bytea;   -- "what a good answer covers"; AAD viva_question_note:<id>

ALTER TABLE viva_questions DROP CONSTRAINT viva_questions_source_check;
ALTER TABLE viva_questions ADD CONSTRAINT viva_questions_source_check
  CHECK (source IN ('ai_draft','template','reviewer','visq','visq_followup'));
ALTER TABLE viva_questions ADD CONSTRAINT viva_questions_followup_check
  CHECK ((source = 'visq_followup') = (parent_id IS NOT NULL));

-- A follow-up shares its main question's position; main questions keep one position each.
ALTER TABLE viva_questions DROP CONSTRAINT viva_questions_invitation_id_position_key;
CREATE UNIQUE INDEX viva_questions_main_position ON viva_questions (invitation_id, position) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX viva_questions_one_followup ON viva_questions (parent_id) WHERE parent_id IS NOT NULL;

-- One decision per main question: ask a follow-up or move on, and why. Append-only.
CREATE TABLE viva_followup_decisions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id        uuid NOT NULL REFERENCES institutions(id),
  invitation_id         uuid NOT NULL REFERENCES viva_invitations(id),
  question_id           uuid NOT NULL UNIQUE REFERENCES viva_questions(id),
  action                text NOT NULL CHECK (action IN ('follow_up','move_on')),
  -- model: Visq decided. no_answer: skipped or empty. timeout: the decision was not ready in time.
  -- error: Visq or transcription failed. disabled: follow-ups off for this invitation.
  reason                text NOT NULL CHECK (reason IN ('model','no_answer','timeout','error','disabled')),
  why_ciphertext        bytea,                              -- Visq's short reason, reviewer only; AAD viva_followup_why:<id>
  followup_question_id  uuid REFERENCES viva_questions(id),
  engine                text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CHECK ((action = 'follow_up') = (followup_question_id IS NOT NULL))
);

ALTER TABLE viva_session_events DROP CONSTRAINT viva_session_events_kind_check;
ALTER TABLE viva_session_events ADD CONSTRAINT viva_session_events_kind_check CHECK (kind IN (
  'open','start','resume','second_session','question_released','answer_saved','question_timed_out',
  'late_write','focus_lost','focus_back','paste_blocked','fullscreen_exit','mic_unavailable',
  'camera_unavailable','submit','followup_waiting'));

-- ---------------------------------------------------------------- Pipeline 2: comparison sets
CREATE TABLE assignment_comparison_sets (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  assignment_id   uuid NOT NULL REFERENCES assignments(id),
  prompt_sha256   char(64) NOT NULL,                     -- the assignment prompt the set was made from
  families        text[] NOT NULL,
  status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','building','ready','failed','superseded')),
  data            jsonb,                                 -- AI-written essays only; no student data
  generator       text,                                  -- e.g. "p2-interchangeability-v1 (e99417a)"
  error           text,
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  ready_at        timestamptz,
  CHECK (status <> 'ready' OR data IS NOT NULL)
);
-- At most one set in use or on the way per assignment.
CREATE UNIQUE INDEX assignment_comparison_sets_active ON assignment_comparison_sets (assignment_id)
  WHERE status IN ('queued','building','ready');

-- ---------------------------------------------------------------- jobs
ALTER TABLE jobs ADD COLUMN viva_question_id uuid REFERENCES viva_questions(id);
ALTER TABLE jobs ADD COLUMN comparison_set_id uuid REFERENCES assignment_comparison_sets(id);
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('extract_document','build_profile','analyse_case','send_viva_email','transcribe_viva_answer',
                  'viva_followup','build_comparison_set'));
ALTER TABLE jobs DROP CONSTRAINT jobs_target_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_target_check CHECK (
  (kind = 'extract_document' AND document_id IS NOT NULL) OR
  (kind = 'build_profile' AND profile_id IS NOT NULL) OR
  (kind = 'analyse_case' AND case_id IS NOT NULL) OR
  (kind = 'send_viva_email' AND viva_invitation_id IS NOT NULL) OR
  (kind = 'transcribe_viva_answer' AND viva_recording_id IS NOT NULL) OR
  (kind = 'viva_followup' AND viva_question_id IS NOT NULL) OR
  (kind = 'build_comparison_set' AND comparison_set_id IS NOT NULL)
);

-- ---------------------------------------------------------------- append-only, RLS, grants
CREATE TRIGGER viva_followup_decisions_no_update BEFORE UPDATE OR DELETE ON viva_followup_decisions
  FOR EACH ROW EXECUTE FUNCTION viva_append_only();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['viva_followup_decisions','assignment_comparison_sets']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (institution_id = app_institution_id()) '
      'WITH CHECK (institution_id = app_institution_id())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT ON viva_followup_decisions TO viskopic_app;
GRANT SELECT, INSERT, UPDATE ON assignment_comparison_sets TO viskopic_app;
