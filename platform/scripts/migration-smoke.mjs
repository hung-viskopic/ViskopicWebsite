import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
const migrations = ['001_initial.sql','002_records.sql','003_required_student.sql'];
const setup = migrations.map(name => readFileSync(new URL('../database/' + name, import.meta.url), 'utf8')).join('\n');
const upgrade = readFileSync(new URL('../database/004_student_profiles.sql', import.meta.url), 'utf8');
const sql = `BEGIN;
CREATE SCHEMA profile_migration_test;
SET LOCAL search_path TO profile_migration_test;
${setup}
INSERT INTO courses(id,organisation_id,name) VALUES
 ('10000000-0000-0000-0000-000000000001','test','A'),
 ('10000000-0000-0000-0000-000000000002','test','B');
INSERT INTO students(id,organisation_id,course_id,reference,name) VALUES
 ('20000000-0000-0000-0000-000000000001','test','10000000-0000-0000-0000-000000000001','DUPLICATE','Writer A'),
 ('20000000-0000-0000-0000-000000000002','test','10000000-0000-0000-0000-000000000002','DUPLICATE','Writer B');
INSERT INTO submissions(id,organisation_id,student_id,filename,object_key,sha256,byte_count,extracted_text) VALUES
 ('30000000-0000-0000-0000-000000000001','test','20000000-0000-0000-0000-000000000001','a.txt','original-a',repeat('a',64),1,'preserved A'),
 ('30000000-0000-0000-0000-000000000002','test','20000000-0000-0000-0000-000000000002','b.txt','original-b',repeat('b',64),1,'preserved B');
${upgrade}
DO $$ BEGIN
 IF (SELECT count(*) FROM students) <> 2 OR (SELECT count(DISTINCT reference) FROM students) <> 2 THEN RAISE EXCEPTION 'Ambiguous students lost'; END IF;
 IF (SELECT count(*) FROM students WHERE legacy_reference='DUPLICATE') <> 1 THEN RAISE EXCEPTION 'Legacy reference not retained'; END IF;
 IF (SELECT count(*) FROM enrolments) <> 2 THEN RAISE EXCEPTION 'Enrolments not backfilled'; END IF;
 IF EXISTS(SELECT 1 FROM submissions WHERE course_id IS NULL) THEN RAISE EXCEPTION 'Course not backfilled'; END IF;
 IF (SELECT extracted_text FROM submissions WHERE object_key='original-a') <> 'preserved A' THEN RAISE EXCEPTION 'Text changed'; END IF;
 BEGIN
  UPDATE submissions SET course_id='10000000-0000-0000-0000-000000000002' WHERE object_key='original-a';
  RAISE EXCEPTION 'Invalid enrolment allowed';
 EXCEPTION WHEN foreign_key_violation THEN NULL;
 END;
 BEGIN
  UPDATE submissions SET student_id=NULL WHERE object_key='original-a';
  RAISE EXCEPTION 'Null ownership allowed';
 EXCEPTION WHEN not_null_violation THEN NULL;
 END;
END $$;
ROLLBACK;`;
const result = spawnSync('docker', ['compose','exec','-T','db','psql','-v','ON_ERROR_STOP=1','-U','viskopic','-d','viskopic'], { input: sql, encoding: 'utf8' });
if (result.status !== 0) throw new Error(result.stderr || result.error?.message || 'Migration test failed');
console.log('PASS: legacy migration preserves separate identities, references, documents and enrolments; database rejects null ownership and invalid enrolments. Test transaction rolled back.');
