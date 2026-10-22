-- Viskopic MVP milestone 4–5: baseline profiles, cases, analysis runs and evidence items.
-- Same rules as 0001: every tenant table has institution_id + RLS, the app role is not the
-- owner and has no BYPASSRLS. Nothing here stores essay text in clear: profiles are encrypted
-- numeric artifacts in object storage, and evidence items hold template statements, numbers
-- and character offsets only.

-- ---------------------------------------------------------------- baseline profiles
-- A versioned, immutable profile of one student's own writing for one pipeline.
-- source = 'enrolled'       built from the student's completed baseline documents
-- source = 'demonstration'  linked to a research author shipped with the model, for demos
--                           only; never presented as the student's own writing
CREATE TABLE baseline_profiles (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id          uuid NOT NULL REFERENCES institutions(id),
  student_id              uuid NOT NULL REFERENCES students(id),
  pipeline                text NOT NULL CHECK (pipeline IN ('consistency')),
  version                 integer NOT NULL CHECK (version > 0),
  source                  text NOT NULL CHECK (source IN ('enrolled','demonstration')),
  demo_author_id          text,
  model_version           text NOT NULL,
  status                  text NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued','building','ready','failed','superseded')),
  reference_document_ids  uuid[] NOT NULL DEFAULT '{}',
  reference_count         integer NOT NULL DEFAULT 0,
  supervised_count        integer NOT NULL DEFAULT 0,
  artifact_storage_key    text UNIQUE,          -- encrypted with the institution's data key
  artifact_sha256         char(64),             -- of the plaintext artifact; checked before loading
  summary                 jsonb NOT NULL DEFAULT '{}',   -- maturity, background median, counts (numbers only)
  error                   text,
  created_by              uuid REFERENCES users(id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  built_at                timestamptz,
  UNIQUE (student_id, pipeline, version),
  CHECK ((source = 'demonstration') = (demo_author_id IS NOT NULL)),
  CHECK (status <> 'ready' OR source = 'demonstration' OR (artifact_storage_key IS NOT NULL AND artifact_sha256 IS NOT NULL))
);
-- At most one usable profile per student and pipeline at a time.
CREATE UNIQUE INDEX baseline_profiles_one_ready
  ON baseline_profiles (student_id, pipeline) WHERE status = 'ready';
CREATE INDEX baseline_profiles_student ON baseline_profiles (student_id, pipeline, version DESC);

-- ---------------------------------------------------------------- cases
-- One case per submission. Created automatically once the submission's text is extracted.
CREATE TABLE cases (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  document_id     uuid NOT NULL UNIQUE REFERENCES documents(id),
  student_id      uuid NOT NULL REFERENCES students(id),
  course_id       uuid NOT NULL REFERENCES courses(id),
  status          text NOT NULL DEFAULT 'analysing'
                    CHECK (status IN ('analysing','awaiting_baseline','evidence_ready','failed')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cases_student ON cases (student_id, created_at DESC);

-- ---------------------------------------------------------------- analysis runs
-- One row per pipeline per analysis attempt. The newest row per (case, pipeline) is current;
-- older rows are kept so results stay reproducible and auditable.
CREATE TABLE analysis_runs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id    uuid NOT NULL REFERENCES institutions(id),
  case_id           uuid NOT NULL REFERENCES cases(id),
  pipeline          text NOT NULL,          -- consistency (Δ), language (β), authenticity (α), document_signals
  pipeline_version  text NOT NULL,
  status            text NOT NULL CHECK (status IN ('completed','not_eligible','unavailable','failed')),
  profile_id        uuid REFERENCES baseline_profiles(id),
  score             double precision,       -- pipeline-specific; never a probability
  threshold         double precision,       -- the frozen threshold the score was compared with
  outcome           text,                   -- pipeline's own label, e.g. 'stylistically-unusual'
  confidence        text CHECK (confidence IN ('low','medium','high')),
  reason            text,                   -- why not eligible / unavailable / failed
  details           jsonb NOT NULL DEFAULT '{}',   -- numeric summary only, no essay text
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX analysis_runs_case ON analysis_runs (case_id, pipeline, created_at DESC);

-- ---------------------------------------------------------------- evidence items
CREATE TABLE evidence_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  run_id          uuid NOT NULL REFERENCES analysis_runs(id),
  ordinal         integer NOT NULL,
  category        text NOT NULL CHECK (category IN ('style','semantic','regeneration','document')),
  kind            text NOT NULL,          -- e.g. overall_score, feature_deviation, feature_group, hidden_text
  statement       text NOT NULL,          -- generated from a template, never free LLM text
  observed        double precision,
  baseline_mean   double precision,
  baseline_sd     double precision,
  z_score         double precision,
  contribution    double precision,
  spans           jsonb NOT NULL DEFAULT '[]',   -- [{start, end, status, window}] offsets into the extracted text
  details         jsonb NOT NULL DEFAULT '{}',
  UNIQUE (run_id, ordinal)
);

-- ---------------------------------------------------------------- jobs: new kinds

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('extract_document','build_profile','analyse_case'));
ALTER TABLE jobs ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE jobs ADD COLUMN profile_id uuid REFERENCES baseline_profiles(id);
ALTER TABLE jobs ADD COLUMN case_id uuid REFERENCES cases(id);
ALTER TABLE jobs ADD CONSTRAINT jobs_target_check CHECK (
  (kind = 'extract_document' AND document_id IS NOT NULL) OR
  (kind = 'build_profile' AND profile_id IS NOT NULL) OR
  (kind = 'analyse_case' AND case_id IS NOT NULL)
);

-- Support view keeps returning job health only; profile and analysis jobs have no document.
CREATE OR REPLACE FUNCTION platform_support_jobs(p_institution uuid)
RETURNS TABLE (job_id bigint, document_id uuid, status text, attempts int, last_error text,
               file_type text, size_bytes int, updated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM support_grants g WHERE g.institution_id = p_institution
                 AND g.revoked_at IS NULL AND g.expires_at > now()) THEN
    RAISE EXCEPTION 'no active support grant' USING ERRCODE = 'P0001';
  END IF;
  RETURN QUERY SELECT j.id, j.document_id, j.status, j.attempts, j.last_error,
                      d.file_type, d.size_bytes, j.updated_at
    FROM jobs j LEFT JOIN documents d ON d.id = j.document_id
    WHERE j.institution_id = p_institution ORDER BY j.updated_at DESC LIMIT 200;
END $$;
REVOKE ALL ON FUNCTION platform_support_jobs(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_support_jobs(uuid) TO viskopic_app;

-- ---------------------------------------------------------------- row-level security and grants

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['baseline_profiles','cases','analysis_runs','evidence_items']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (institution_id = app_institution_id()) '
      'WITH CHECK (institution_id = app_institution_id())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON baseline_profiles, cases TO viskopic_app;
-- Results are append-only for the app: a re-analysis adds new rows instead of editing old ones.
GRANT SELECT, INSERT ON analysis_runs, evidence_items TO viskopic_app;
