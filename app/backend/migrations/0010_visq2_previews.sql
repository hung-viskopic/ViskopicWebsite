-- Visq 2 preview: the reviewer sees the questions before sending the invitation.
--
-- Visq reads the essay (the same prepare step as 0008) before any invitation exists. The reviewer sees the exact
-- clarifying questions, plus a draft what-if extension and draft viva questions worked out as if the student gave no
-- clarifying answers. In the viva those two may change, because Visq writes them from the student's own answers.
-- Sending the invitation hands the prepared state over unchanged, so the clarifying questions are the ones shown.

CREATE TABLE viva_visq2_previews (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id     uuid NOT NULL REFERENCES institutions(id),
  case_id            uuid NOT NULL REFERENCES cases(id),
  created_by         uuid NOT NULL REFERENCES users(id),
  visq2_full         boolean NOT NULL,
  extension_seconds  integer NOT NULL CHECK (extension_seconds BETWEEN 60 AND 1800),
  status             text NOT NULL DEFAULT 'preparing' CHECK (status IN ('preparing','ready','failed','used')),
  state_ciphertext   bytea,          -- Visq's prepared state; AAD viva_visq2_preview_state:<id>
  draft_ciphertext   bytea,          -- what the reviewer is shown; AAD viva_visq2_preview_draft:<id>
  engine             text,
  error              text,
  invitation_id      uuid REFERENCES viva_invitations(id),   -- the invitation that used it
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CHECK ((status = 'used') = (invitation_id IS NOT NULL))
);
CREATE INDEX viva_visq2_previews_case ON viva_visq2_previews (case_id, created_at DESC);

ALTER TABLE jobs ADD COLUMN visq2_preview_id uuid REFERENCES viva_visq2_previews(id);
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('extract_document','build_profile','analyse_case','send_viva_email','transcribe_viva_answer',
                  'viva_followup','build_comparison_set','visq2_prepare','visq2_step','visq2_preview'));
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
  (kind = 'visq2_step' AND viva_question_id IS NOT NULL) OR
  (kind = 'visq2_preview' AND visq2_preview_id IS NOT NULL)
);

ALTER TABLE viva_visq2_previews ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON viva_visq2_previews USING (institution_id = app_institution_id())
  WITH CHECK (institution_id = app_institution_id());
GRANT SELECT, INSERT, UPDATE ON viva_visq2_previews TO viskopic_app;
