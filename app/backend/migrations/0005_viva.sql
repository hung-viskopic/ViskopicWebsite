-- Viskopic milestone 6a/6b MVP: supervised viva.
-- A reviewer invites a student to a short viva about their own submission. Questions are released one at a
-- time by the server, each with a server-measured time limit; answers are spoken (recorded in the browser)
-- or typed (paste blocked). Recordings and transcripts are encrypted with the institution's data key.
-- Session signals are hints for a human reviewer, never evidence on their own.
--
-- Same rules as before: every tenant table has institution_id + RLS; viskopic_app is not the owner and has
-- no BYPASSRLS. Frozen and log tables are append-only (no UPDATE/DELETE grant + trigger).

-- ---------------------------------------------------------------- invitations
CREATE TABLE viva_invitations (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id        uuid NOT NULL REFERENCES institutions(id),
  case_id               uuid NOT NULL REFERENCES cases(id),
  viva_kind             text NOT NULL CHECK (viva_kind IN ('style','reasoning')),
  answer_format         text NOT NULL CHECK (answer_format IN ('spoken','typed')),
  -- How much weight the answers can bear; shown on the report. MVP: no staff present in either mode.
  supervision_mode      text NOT NULL CHECK (supervision_mode IN ('oral_recorded','restricted')),
  token_sha256          bytea NOT NULL UNIQUE,          -- lookups use the hash only
  token_ciphertext      bytea,                          -- kept only until the email is sent; AAD viva_token:<id>
  email_ciphertext      bytea NOT NULL,                 -- AAD viva_invitation_email:<id>
  status                text NOT NULL DEFAULT 'queued' CHECK (status IN
                          ('queued','sent','opened','in_progress','submitted','failed','expired','revoked')),
  deadline_at           timestamptz NOT NULL,
  prep_seconds          integer NOT NULL CHECK (prep_seconds BETWEEN 0 AND 300),
  answer_seconds        integer NOT NULL CHECK (answer_seconds BETWEEN 30 AND 600),
  is_test               boolean NOT NULL DEFAULT true,
  session_nonce_sha256  bytea,                          -- the one browser session allowed to write
  last_error            text,
  created_by            uuid NOT NULL REFERENCES users(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  sent_at               timestamptz,
  opened_at             timestamptz,
  started_at            timestamptz,
  submitted_at          timestamptz,
  revoked_at            timestamptz,
  revoked_by            uuid REFERENCES users(id),
  revoke_reason         text,
  replaces_id           uuid REFERENCES viva_invitations(id),
  CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX viva_invitations_case ON viva_invitations (case_id, created_at DESC);

-- ---------------------------------------------------------------- questions (frozen copy of what was sent)
CREATE TABLE viva_questions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id     uuid NOT NULL REFERENCES institutions(id),
  invitation_id      uuid NOT NULL REFERENCES viva_invitations(id),
  position           integer NOT NULL CHECK (position BETWEEN 1 AND 10),
  text_ciphertext    bytea NOT NULL,                    -- AAD viva_question:<id>
  passage_ciphertext bytea,                             -- the essay passage it is about; AAD viva_passage:<id>
  passage_start      integer,                           -- offsets into the submission's extracted text
  passage_end        integer,
  source             text NOT NULL CHECK (source IN ('ai_draft','template','reviewer')),
  UNIQUE (invitation_id, position)
);

-- When the server released each question to the student, and by when the answer must arrive.
CREATE TABLE viva_question_releases (
  question_id     uuid PRIMARY KEY REFERENCES viva_questions(id),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  invitation_id   uuid NOT NULL REFERENCES viva_invitations(id),
  released_at     timestamptz NOT NULL DEFAULT now(),
  due_at          timestamptz NOT NULL
);

-- ---------------------------------------------------------------- answers
CREATE TABLE viva_responses (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id     uuid NOT NULL REFERENCES institutions(id),
  invitation_id      uuid NOT NULL REFERENCES viva_invitations(id),
  question_id        uuid NOT NULL REFERENCES viva_questions(id) UNIQUE,   -- one answer per question, no edits
  kind               text NOT NULL CHECK (kind IN ('typed','spoken','skipped')),
  answer_ciphertext  bytea,                             -- typed text; AAD viva_response:<id>
  seconds_taken      integer,                           -- server time from release to save
  saved_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE viva_recordings (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  invitation_id   uuid NOT NULL REFERENCES viva_invitations(id),
  question_id     uuid NOT NULL REFERENCES viva_questions(id) UNIQUE,
  storage_key     text UNIQUE,                          -- encrypted audio; AAD viva_audio:<id>. NULL once purged
  sha256          char(64) NOT NULL,                    -- of the plaintext audio
  mime_type       text NOT NULL CHECK (mime_type IN ('audio/webm','audio/ogg','audio/mp4')),
  size_bytes      integer NOT NULL CHECK (size_bytes > 0),
  status          text NOT NULL DEFAULT 'stored' CHECK (status IN ('stored','purged')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  purged_at       timestamptz
);

-- Automatic transcript plus a timeline of speech and silence, both from the speech-to-text service.
-- The timeline holds numbers only (milliseconds). Append-only: a re-run adds a row.
CREATE TABLE viva_transcripts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id   uuid NOT NULL REFERENCES institutions(id),
  recording_id     uuid NOT NULL REFERENCES viva_recordings(id),
  text_ciphertext  bytea NOT NULL,                      -- AAD viva_transcript:<id>
  timeline         jsonb NOT NULL DEFAULT '{}',
  provider         text NOT NULL,
  model            text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX viva_transcripts_recording ON viva_transcripts (recording_id, created_at DESC);

-- ---------------------------------------------------------------- signals and assessments
CREATE TABLE viva_session_events (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  invitation_id   uuid NOT NULL REFERENCES viva_invitations(id),
  question_id     uuid REFERENCES viva_questions(id),
  kind            text NOT NULL CHECK (kind IN (
                    'open','start','resume','second_session','question_released','answer_saved','question_timed_out',
                    'late_write','focus_lost','focus_back','paste_blocked','fullscreen_exit','mic_unavailable',
                    'submit')),
  source          text NOT NULL CHECK (source IN ('server','browser')),   -- browser events are self-reported
  at              timestamptz NOT NULL DEFAULT now(),
  detail          jsonb NOT NULL DEFAULT '{}'           -- numbers and flags only, never answer text
);
CREATE INDEX viva_session_events_invitation ON viva_session_events (invitation_id, at);

CREATE TABLE viva_assessments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id   uuid NOT NULL REFERENCES institutions(id),
  invitation_id    uuid NOT NULL REFERENCES viva_invitations(id),
  question_id      uuid REFERENCES viva_questions(id),  -- NULL = overall
  rating           text NOT NULL CHECK (rating IN ('consistent','unclear','concern')),
  note_ciphertext  bytea,                               -- AAD viva_assessment:<id>
  created_by       uuid NOT NULL REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX viva_assessments_invitation ON viva_assessments (invitation_id, created_at DESC);

-- ---------------------------------------------------------------- jobs: email and transcription
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('extract_document','build_profile','analyse_case','send_viva_email','transcribe_viva_answer'));
ALTER TABLE jobs ADD COLUMN viva_invitation_id uuid REFERENCES viva_invitations(id);
ALTER TABLE jobs ADD COLUMN viva_recording_id uuid REFERENCES viva_recordings(id);
ALTER TABLE jobs DROP CONSTRAINT jobs_target_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_target_check CHECK (
  (kind = 'extract_document' AND document_id IS NOT NULL) OR
  (kind = 'build_profile' AND profile_id IS NOT NULL) OR
  (kind = 'analyse_case' AND case_id IS NOT NULL) OR
  (kind = 'send_viva_email' AND viva_invitation_id IS NOT NULL) OR
  (kind = 'transcribe_viva_answer' AND viva_recording_id IS NOT NULL)
);

-- ---------------------------------------------------------------- append-only enforcement
CREATE OR REPLACE FUNCTION viva_append_only() RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['viva_questions','viva_question_releases','viva_responses','viva_transcripts',
                           'viva_session_events','viva_assessments']
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION viva_append_only()',
                   t || '_no_update', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- row-level security
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['viva_invitations','viva_questions','viva_question_releases','viva_responses',
                           'viva_recordings','viva_transcripts','viva_session_events','viva_assessments']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (institution_id = app_institution_id()) '
      'WITH CHECK (institution_id = app_institution_id())', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- public token resolution
-- A student's request carries only the token, so it has no tenant context. This is the one way to turn a
-- token hash into (institution, invitation). It returns nothing for unknown tokens and leaks nothing else;
-- the API then does all work inside tenant_tx(institution_id) under normal RLS.
CREATE OR REPLACE FUNCTION viva_resolve_token(p_token_sha256 bytea)
RETURNS TABLE (institution_id uuid, invitation_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT v.institution_id, v.id FROM viva_invitations v
  JOIN institutions i ON i.id = v.institution_id AND i.status = 'active'
  WHERE v.token_sha256 = p_token_sha256
$$;
REVOKE ALL ON FUNCTION viva_resolve_token(bytea), viva_append_only() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION viva_resolve_token(bytea) TO viskopic_app;

-- ---------------------------------------------------------------- grants
GRANT SELECT, INSERT, UPDATE ON viva_invitations, viva_recordings TO viskopic_app;
GRANT SELECT, INSERT ON viva_questions, viva_question_releases, viva_responses, viva_transcripts,
  viva_session_events, viva_assessments TO viskopic_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO viskopic_app;
