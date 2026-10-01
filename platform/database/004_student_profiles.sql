CREATE TABLE organisations (id text PRIMARY KEY, name text NOT NULL);
INSERT INTO organisations(id,name)
SELECT organisation_id,organisation_id FROM courses
UNION SELECT organisation_id,organisation_id FROM students
UNION SELECT organisation_id,organisation_id FROM submissions;
ALTER TABLE students ADD CONSTRAINT student_organisation FOREIGN KEY (organisation_id) REFERENCES organisations(id);
ALTER TABLE courses ADD CONSTRAINT course_organisation FOREIGN KEY (organisation_id) REFERENCES organisations(id);
CREATE TABLE enrolments (
  student_id uuid NOT NULL,
  course_id uuid NOT NULL,
  organisation_id text NOT NULL,
  archived boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (student_id,course_id,organisation_id),
  FOREIGN KEY (student_id,organisation_id) REFERENCES students(id,organisation_id),
  FOREIGN KEY (course_id,organisation_id) REFERENCES courses(id,organisation_id)
);
INSERT INTO enrolments(student_id,course_id,organisation_id,created_at)
SELECT id,course_id,organisation_id,created_at FROM students;
ALTER TABLE submissions ADD COLUMN course_id uuid;
UPDATE submissions d SET course_id=s.course_id FROM students s
WHERE d.student_id=s.id AND d.organisation_id=s.organisation_id;
ALTER TABLE submissions ALTER COLUMN course_id SET NOT NULL;
ALTER TABLE submissions ADD CONSTRAINT submission_enrolment
  FOREIGN KEY (student_id,course_id,organisation_id) REFERENCES enrolments(student_id,course_id,organisation_id);
ALTER TABLE students ADD COLUMN legacy_reference text;
-- Preserve ambiguous identities instead of merging writers solely by their reference.
WITH duplicates AS (
  SELECT id, row_number() OVER (PARTITION BY organisation_id,reference ORDER BY created_at,id) AS position FROM students
)
UPDATE students s SET legacy_reference=s.reference,
  reference=left(s.reference,100) || ' [legacy ' || s.id::text || ']'
FROM duplicates d WHERE s.id=d.id AND d.position>1;
ALTER TABLE students DROP COLUMN course_id;
ALTER TABLE students ADD CONSTRAINT student_reference UNIQUE (organisation_id,reference);
CREATE INDEX enrolment_course ON enrolments(organisation_id,course_id);
CREATE INDEX submission_course ON submissions(organisation_id,course_id,student_id,created_at DESC);
