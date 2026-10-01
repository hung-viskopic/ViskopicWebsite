DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM submissions WHERE student_id IS NULL) THEN
    RAISE EXCEPTION 'Cannot require student_id: existing submissions have no student. Assign each legacy submission to its actual student before restarting the API. See DEPLOYMENT.md.';
  END IF;
END $$;
ALTER TABLE submissions ALTER COLUMN student_id SET NOT NULL;
