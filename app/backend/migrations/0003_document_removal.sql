-- Removing a wrongly uploaded document (institution owners only, with a reason).
-- The original file, extracted text and file signals are deleted. The documents row stays as a
-- tombstone (status 'removed', who, when, why) so the audit trail and any earlier analysis runs
-- still make sense. Profiles built from a removed baseline document are retired and their
-- artifacts deleted; a case on a removed submission is withdrawn.

ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
  CHECK (status IN ('queued','processing','completed','failed','removed'));
ALTER TABLE documents ADD COLUMN removed_at timestamptz;
ALTER TABLE documents ADD COLUMN removed_by uuid REFERENCES users(id);
ALTER TABLE documents ADD COLUMN removal_reason text;
ALTER TABLE documents ADD CONSTRAINT documents_removed_check CHECK (
  (status = 'removed') = (removed_at IS NOT NULL AND removed_by IS NOT NULL AND removal_reason IS NOT NULL)
);

ALTER TABLE baseline_profiles DROP CONSTRAINT baseline_profiles_status_check;
ALTER TABLE baseline_profiles ADD CONSTRAINT baseline_profiles_status_check
  CHECK (status IN ('queued','building','ready','failed','superseded','removed'));

ALTER TABLE cases DROP CONSTRAINT cases_status_check;
ALTER TABLE cases ADD CONSTRAINT cases_status_check
  CHECK (status IN ('analysing','awaiting_baseline','evidence_ready','failed','withdrawn'));

-- The app may delete extracted text and file signals (only on removal). Still scoped by RLS.
GRANT DELETE ON document_texts, document_signals TO viskopic_app;
