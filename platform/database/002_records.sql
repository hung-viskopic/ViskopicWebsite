CREATE TABLE IF NOT EXISTS courses (
  id uuid PRIMARY KEY,
  organisation_id text NOT NULL,
  name text NOT NULL,
  archived boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, organisation_id)
);
CREATE TABLE IF NOT EXISTS students (
  id uuid PRIMARY KEY,
  organisation_id text NOT NULL,
  course_id uuid NOT NULL,
  reference text NOT NULL,
  name text NOT NULL DEFAULT '',
  archived boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, organisation_id),
  UNIQUE (course_id, reference),
  FOREIGN KEY (course_id, organisation_id) REFERENCES courses(id, organisation_id)
);
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS student_id uuid;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'submission_student') THEN
    ALTER TABLE submissions ADD CONSTRAINT submission_student FOREIGN KEY (student_id, organisation_id) REFERENCES students(id, organisation_id);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS submissions_student ON submissions(student_id, created_at DESC);
CREATE TABLE IF NOT EXISTS audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  organisation_id text NOT NULL,
  entity_id uuid NOT NULL,
  entity_type text NOT NULL,
  action text NOT NULL,
  actor text NOT NULL DEFAULT 'Shared development access',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_entity ON audit_events(organisation_id, entity_id, id DESC);
