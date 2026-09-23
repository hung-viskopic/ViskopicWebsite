CREATE TABLE IF NOT EXISTS submissions (
  id uuid PRIMARY KEY,
  organisation_id text NOT NULL,
  filename text NOT NULL,
  object_key text NOT NULL UNIQUE,
  sha256 char(64) NOT NULL,
  byte_count integer NOT NULL CHECK (byte_count > 0),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','completed','failed')),
  attempts integer NOT NULL DEFAULT 0,
  lease_until timestamptz,
  extracted_text text,
  word_count integer,
  pipeline_version text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS submission_queue ON submissions(status, created_at);
CREATE INDEX IF NOT EXISTS submission_organisation ON submissions(organisation_id, created_at DESC);
CREATE TABLE IF NOT EXISTS processing_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  submission_id uuid NOT NULL REFERENCES submissions(id),
  event text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
