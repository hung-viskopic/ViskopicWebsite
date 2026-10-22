-- Quality reviews for the Δ consistency model's website contract (model 1.3, website adapter v1).
-- The model only scores when a person has checked that the submission and every baseline document
-- are English prose and were extracted cleanly. A review is a server-side record bound to the
-- SHA-256 of the exact extracted text that was reviewed, so a changed text needs a new review.
-- Append-only: a new review supersedes the previous one for the same document; nothing is edited.
-- No essay text is stored here, only the outcome, the digest and who/when.

CREATE TABLE document_quality_reviews (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  document_id     uuid NOT NULL REFERENCES documents(id),
  text_sha256     char(64) NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
  language        text NOT NULL CHECK (language IN ('english','non_english','mixed','uncertain')),
  extraction      text NOT NULL CHECK (extraction IN ('pass','fail')),
  reviewed_by     uuid NOT NULL REFERENCES users(id),
  reviewed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_quality_reviews_document ON document_quality_reviews (document_id, reviewed_at DESC);

ALTER TABLE document_quality_reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_quality_reviews
  USING (institution_id = app_institution_id()) WITH CHECK (institution_id = app_institution_id());
GRANT SELECT, INSERT ON document_quality_reviews TO viskopic_app;

-- A case whose submission or baseline still needs a quality review before the model can score it.
ALTER TABLE cases DROP CONSTRAINT cases_status_check;
ALTER TABLE cases ADD CONSTRAINT cases_status_check
  CHECK (status IN ('analysing','awaiting_baseline','awaiting_review','evidence_ready','failed','withdrawn'));
