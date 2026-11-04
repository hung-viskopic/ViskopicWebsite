-- Visq 2 (the world-tree flow) replaces Visq 1 question drafting and follow-ups.
--
-- Visq 2 reads the submission as a tree of points and what it takes for granted, asks clarifying questions where
-- the essay is silent, gives a timed typed extension (a what-if from outside the essay), then a short viva whose
-- questions depend on the earlier answers. So the questions are generated during the viva, not approved before it;
-- every question shown is still frozen in viva_questions as it is released, and the reviewer reads them all.
--
-- Visq's own state (the tree, its plan and the answers it has taken in) is kept encrypted, one row per invitation.
-- The Visq 1 follow-up tables and columns from 0007 stay for the record but are no longer written.

ALTER TABLE viva_invitations ADD COLUMN flow text NOT NULL DEFAULT 'standard' CHECK (flow IN ('standard','visq2'));
ALTER TABLE viva_invitations ADD COLUMN visq2_full boolean NOT NULL DEFAULT false;       -- 7 viva questions instead of 3
ALTER TABLE viva_invitations ADD COLUMN extension_seconds integer CHECK (extension_seconds BETWEEN 60 AND 1800);
ALTER TABLE viva_invitations ADD CONSTRAINT viva_invitations_visq2_check
  CHECK (flow = 'standard' OR (answer_format = 'typed' AND NOT camera_required AND extension_seconds IS NOT NULL));

-- Questions appear as the flow releases them: clarifying questions, the extension, viva questions and their
-- follow-ups, numbered in the order shown.
ALTER TABLE viva_questions DROP CONSTRAINT viva_questions_position_check;
ALTER TABLE viva_questions ADD CONSTRAINT viva_questions_position_check CHECK (position BETWEEN 1 AND 60);
ALTER TABLE viva_questions DROP CONSTRAINT viva_questions_source_check;
ALTER TABLE viva_questions ADD CONSTRAINT viva_questions_source_check
  CHECK (source IN ('ai_draft','template','reviewer','visq','visq_followup','visq2'));

CREATE TABLE viva_visq2_states (
  invitation_id        uuid PRIMARY KEY REFERENCES viva_invitations(id),
  institution_id       uuid NOT NULL REFERENCES institutions(id),
  status               text NOT NULL DEFAULT 'preparing' CHECK (status IN ('preparing','ready','failed')),
  state_ciphertext     bytea,                         -- Visq's state as JSON; AAD viva_visq2_state:<invitation_id>
  version              integer NOT NULL DEFAULT 0,    -- bumped on every write; a slow worker can't overwrite a newer state
  applied_question_id  uuid REFERENCES viva_questions(id),   -- the last answer the state has taken in
  engine               text,
  error                text,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE viva_session_events DROP CONSTRAINT viva_session_events_kind_check;
ALTER TABLE viva_session_events ADD CONSTRAINT viva_session_events_kind_check CHECK (kind IN (
  'open','start','resume','second_session','question_released','answer_saved','question_timed_out',
  'late_write','focus_lost','focus_back','paste_blocked','fullscreen_exit','mic_unavailable',
  'camera_unavailable','submit','followup_waiting','example_shown','visq2_offline_step'));

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('extract_document','build_profile','analyse_case','send_viva_email','transcribe_viva_answer',
                  'viva_followup','build_comparison_set','visq2_prepare','visq2_step'));
ALTER TABLE jobs DROP CONSTRAINT jobs_target_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_target_check CHECK (
  (kind = 'extract_document' AND document_id IS NOT NULL) OR
  (kind = 'build_profile' AND profile_id IS NOT NULL) OR
  (kind = 'analyse_case' AND case_id IS NOT NULL) OR
  (kind = 'send_viva_email' AND viva_invitation_id IS NOT NULL) OR
  (kind = 'transcribe_viva_answer' AND viva_recording_id IS NOT NULL) OR
  (kind = 'viva_followup' AND viva_question_id IS NOT NULL) OR
  (kind = 'build_comparison_set' AND comparison_set_id IS NOT NULL) OR
  (kind = 'visq2_prepare' AND viva_invitation_id IS NOT NULL) OR
  (kind = 'visq2_step' AND viva_question_id IS NOT NULL)
);

ALTER TABLE viva_visq2_states ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON viva_visq2_states USING (institution_id = app_institution_id())
  WITH CHECK (institution_id = app_institution_id());
GRANT SELECT, INSERT, UPDATE ON viva_visq2_states TO viskopic_app;
