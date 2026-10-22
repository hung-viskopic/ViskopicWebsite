-- Viskopic MVP: tenancy, access control, academic records, intake, jobs, audit.
-- Applied by the migration owner role. The API and worker connect as viskopic_app,
-- which is not the table owner and has no BYPASSRLS, so row-level security always applies
-- to it. RLS is deliberately not FORCEd: the owner is used only by migrations and by the
-- narrow SECURITY DEFINER functions at the end of this file.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tenant context helper: returns NULL when no institution is set (e.g. platform operator),
-- so every tenant policy evaluates to false and no rows are visible.
CREATE OR REPLACE FUNCTION app_institution_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.institution_id', true), '')::uuid
$$;

-- ---------------------------------------------------------------- tenancy

CREATE TABLE institutions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{2,40}$'),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  kms_key_ref   text NOT NULL,          -- which key in the institution's KMS wraps the data key
  wrapped_dek   bytea NOT NULL,         -- per-institution data key, encrypted by that KMS key
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE departments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  name            text NOT NULL,
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (institution_id, name)
);

CREATE TABLE users (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  email           text NOT NULL,
  display_name    text,
  sso_subject     text UNIQUE,          -- bound on first verified login
  status          text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','disabled')),
  invited_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_login_at   timestamptz
);
-- MVP simplification: an email address belongs to one institution.
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE role_assignments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  user_id         uuid NOT NULL REFERENCES users(id),
  role            text NOT NULL CHECK (role IN
                    ('institution_owner','department_admin','reviewer','appeals_reviewer','viewer')),
  scope_type      text NOT NULL CHECK (scope_type IN ('institution','department','course')),
  scope_id        uuid NOT NULL,        -- institution, department or course id
  granted_by      uuid REFERENCES users(id),   -- NULL only for the platform bootstrap owner
  granted_at      timestamptz NOT NULL DEFAULT now(),
  revoked_at      timestamptz,
  revoked_by      uuid REFERENCES users(id),
  CHECK (
    (role IN ('institution_owner','appeals_reviewer') AND scope_type = 'institution') OR
    (role = 'department_admin' AND scope_type = 'department') OR
    (role IN ('reviewer','viewer') AND scope_type = 'course')
  )
);
CREATE UNIQUE INDEX role_assignments_active_unique
  ON role_assignments (user_id, role, scope_type, scope_id) WHERE revoked_at IS NULL;

-- Time-limited, customer-granted support access for the platform operator.
CREATE TABLE support_grants (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  granted_by      uuid NOT NULL REFERENCES users(id),
  reason          text NOT NULL,
  expires_at      timestamptz NOT NULL,
  revoked_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '7 days')
);

-- ---------------------------------------------------------------- academic

CREATE TABLE courses (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  department_id   uuid NOT NULL REFERENCES departments(id),
  code            text NOT NULL,
  title           text NOT NULL,
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (institution_id, code)
);

-- Students are pseudonymous inside the platform. The real reference and name are
-- encrypted with the institution's data key; the HMAC allows exact-match lookup
-- and uniqueness without storing the reference in clear.
CREATE TABLE students (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id      uuid NOT NULL REFERENCES institutions(id),
  pseudonym           text NOT NULL,
  external_ref_hmac   bytea NOT NULL,
  external_ref_enc    bytea NOT NULL,
  display_name_enc    bytea,
  status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (institution_id, pseudonym),
  UNIQUE (institution_id, external_ref_hmac)
);

CREATE TABLE enrolments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  student_id      uuid NOT NULL REFERENCES students(id),
  course_id       uuid NOT NULL REFERENCES courses(id),
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','withdrawn')),
  enrolled_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (student_id, course_id)
);

CREATE TABLE assignments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  course_id       uuid NOT NULL REFERENCES courses(id),
  title           text NOT NULL,
  prompt          text,
  genre           text,                 -- e.g. essay, lab_report, reflection
  due_at          timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- intake

CREATE TABLE documents (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  institution_id      uuid NOT NULL REFERENCES institutions(id),
  student_id          uuid NOT NULL REFERENCES students(id),
  course_id           uuid NOT NULL REFERENCES courses(id),
  assignment_id       uuid REFERENCES assignments(id),
  role                text NOT NULL CHECK (role IN ('baseline','submission')),
  provenance          text NOT NULL CHECK (provenance IN ('supervised','unsupervised')),
  original_filename   text NOT NULL,
  file_type           text NOT NULL CHECK (file_type IN ('pdf','docx','txt')),
  size_bytes          integer NOT NULL CHECK (size_bytes > 0),
  sha256              char(64) NOT NULL,      -- of the plaintext original
  storage_key         text NOT NULL UNIQUE,
  status              text NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','processing','completed','failed')),
  attempts            integer NOT NULL DEFAULT 0,
  last_error          text,
  uploaded_by         uuid NOT NULL REFERENCES users(id),
  uploaded_at         timestamptz NOT NULL DEFAULT now(),
  processed_at        timestamptz
);
CREATE INDEX documents_student ON documents (student_id, uploaded_at DESC);
CREATE INDEX documents_course ON documents (course_id, uploaded_at DESC);

CREATE TABLE document_texts (
  document_id         uuid PRIMARY KEY REFERENCES documents(id),
  institution_id      uuid NOT NULL REFERENCES institutions(id),
  text_enc            bytea NOT NULL,         -- encrypted with the institution's data key
  word_count          integer NOT NULL,
  char_count          integer NOT NULL,
  extractor_version   text NOT NULL,
  extracted_at        timestamptz NOT NULL DEFAULT now()
);

-- Signals from the file itself, captured during extraction. These become
-- "document signal" evidence items once the analysis stage exists.
CREATE TABLE document_signals (
  document_id         uuid PRIMARY KEY REFERENCES documents(id),
  institution_id      uuid NOT NULL REFERENCES institutions(id),
  hidden_text_chars   integer NOT NULL DEFAULT 0,
  hidden_text_spans   jsonb NOT NULL DEFAULT '[]',   -- [{reason, chars, sample_hash}]
  metadata            jsonb NOT NULL DEFAULT '{}',   -- author, created, modified, edit minutes, revisions
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- jobs
-- Holds identifiers only (no essay content), so the worker can claim jobs
-- before it knows which tenant they belong to.

CREATE TABLE jobs (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  uuid NOT NULL REFERENCES institutions(id),
  kind            text NOT NULL CHECK (kind IN ('extract_document')),
  document_id     uuid NOT NULL REFERENCES documents(id),
  status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 3,
  run_after       timestamptz NOT NULL DEFAULT now(),
  lease_until     timestamptz,
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim ON jobs (status, run_after) WHERE status IN ('queued','running');

-- ---------------------------------------------------------------- audit

CREATE TABLE audit_log (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  institution_id  uuid REFERENCES institutions(id),   -- NULL for platform-level events
  actor_user_id   uuid REFERENCES users(id),
  actor_label     text NOT NULL,          -- e.g. user email, 'platform_operator', 'worker'
  action          text NOT NULL,
  target_type     text,
  target_id       text,
  details         jsonb NOT NULL DEFAULT '{}',
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_tenant_time ON audit_log (institution_id, at DESC);

CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();

-- ---------------------------------------------------------------- row-level security

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['departments','users','role_assignments','support_grants','courses',
                           'students','enrolments','assignments','documents','document_texts',
                           'document_signals','audit_log']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (institution_id = app_institution_id()) '
      'WITH CHECK (institution_id = app_institution_id())', t);
  END LOOP;
END $$;

-- institutions: a tenant can read its own row only. Platform-level management of
-- institutions goes through the SECURITY DEFINER functions below.
ALTER TABLE institutions ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_institution ON institutions FOR SELECT USING (id = app_institution_id());

-- ---------------------------------------------------------------- narrow cross-tenant functions
-- These are the only ways to touch data without a tenant context. Each returns or
-- writes the minimum needed and none of them can read essays, students or decisions.

-- Login: find the user record for a token, binding the SSO subject on first login.
CREATE OR REPLACE FUNCTION auth_resolve_user(p_subject text, p_email text, p_email_verified boolean)
RETURNS TABLE (user_id uuid, institution_id uuid, status text, institution_status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE u users%ROWTYPE;
BEGIN
  SELECT * INTO u FROM users WHERE users.sso_subject = p_subject;
  IF NOT FOUND AND p_email_verified AND p_email IS NOT NULL THEN
    UPDATE users SET sso_subject = p_subject, status = 'active'
      WHERE lower(users.email) = lower(p_email) AND users.sso_subject IS NULL AND users.status = 'invited'
      RETURNING * INTO u;
  END IF;
  IF u.id IS NULL THEN RETURN; END IF;
  UPDATE users SET last_login_at = now() WHERE users.id = u.id;
  RETURN QUERY SELECT u.id, u.institution_id, u.status, i.status
    FROM institutions i WHERE i.id = u.institution_id;
END $$;

CREATE OR REPLACE FUNCTION platform_list_institutions()
RETURNS TABLE (id uuid, name text, slug text, status text, created_at timestamptz,
               user_count bigint, document_count bigint, failed_jobs bigint, owner_count bigint)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT i.id, i.name, i.slug, i.status, i.created_at,
    (SELECT count(*) FROM users u WHERE u.institution_id = i.id),
    (SELECT count(*) FROM documents d WHERE d.institution_id = i.id),
    (SELECT count(*) FROM jobs j WHERE j.institution_id = i.id AND j.status = 'failed'),
    (SELECT count(*) FROM role_assignments r WHERE r.institution_id = i.id
       AND r.role = 'institution_owner' AND r.revoked_at IS NULL)
  FROM institutions i ORDER BY i.created_at
$$;

CREATE OR REPLACE FUNCTION platform_create_institution(p_name text, p_slug text,
                                                       p_kms_key_ref text, p_wrapped_dek bytea)
RETURNS uuid LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  INSERT INTO institutions (name, slug, kms_key_ref, wrapped_dek)
  VALUES (p_name, p_slug, p_kms_key_ref, p_wrapped_dek) RETURNING id
$$;

CREATE OR REPLACE FUNCTION platform_set_institution_status(p_id uuid, p_status text)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE institutions SET status = p_status WHERE id = p_id
$$;

-- Invite an institution owner. Allowed only while the institution has fewer than
-- two owners, so the operator can bootstrap a tenant but cannot keep adding itself
-- back in later.
CREATE OR REPLACE FUNCTION platform_invite_owner(p_institution uuid, p_email text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_user uuid; v_owners int;
BEGIN
  SELECT count(*) INTO v_owners FROM role_assignments
    WHERE institution_id = p_institution AND role = 'institution_owner' AND revoked_at IS NULL;
  IF v_owners >= 2 THEN
    RAISE EXCEPTION 'institution already has owners; further owners are added by the institution'
      USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO users (institution_id, email, status) VALUES (p_institution, lower(p_email), 'invited')
    RETURNING id INTO v_user;
  INSERT INTO role_assignments (institution_id, user_id, role, scope_type, scope_id)
    VALUES (p_institution, v_user, 'institution_owner', 'institution', p_institution);
  RETURN v_user;
END $$;

-- Support view: job health only (no content), and only while a support grant is active.
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
    FROM jobs j JOIN documents d ON d.id = j.document_id
    WHERE j.institution_id = p_institution ORDER BY j.updated_at DESC LIMIT 200;
END $$;

CREATE OR REPLACE FUNCTION platform_audit(p_institution uuid, p_action text, p_details jsonb)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  INSERT INTO audit_log (institution_id, actor_label, action, target_type, target_id, details)
  VALUES (p_institution, 'platform_operator', p_action, 'institution', p_institution::text, p_details)
$$;

-- ---------------------------------------------------------------- grants for the app role

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON FUNCTION auth_resolve_user(text, text, boolean), platform_list_institutions(),
  platform_create_institution(text, text, text, bytea), platform_set_institution_status(uuid, text),
  platform_invite_owner(uuid, text), platform_support_jobs(uuid), platform_audit(uuid, text, jsonb)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_institution_id() TO viskopic_app;
GRANT EXECUTE ON FUNCTION auth_resolve_user(text, text, boolean) TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_list_institutions() TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_create_institution(text, text, text, bytea) TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_set_institution_status(uuid, text) TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_invite_owner(uuid, text) TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_support_jobs(uuid) TO viskopic_app;
GRANT EXECUTE ON FUNCTION platform_audit(uuid, text, jsonb) TO viskopic_app;

GRANT SELECT ON institutions TO viskopic_app;
GRANT SELECT, INSERT, UPDATE ON departments, users, role_assignments, support_grants, courses,
  students, enrolments, assignments, documents, document_texts, document_signals TO viskopic_app;
GRANT DELETE ON enrolments TO viskopic_app;
GRANT SELECT, INSERT ON audit_log TO viskopic_app;
GRANT SELECT, INSERT, UPDATE ON jobs TO viskopic_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO viskopic_app;
