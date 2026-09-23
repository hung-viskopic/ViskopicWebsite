import logging
import os
import time
import boto3
import psycopg
from psycopg.rows import dict_row
from botocore.config import Config
from extraction import extract, PIPELINE_VERSION

logging.basicConfig(level=logging.INFO)
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'], region_name=os.environ['S3_REGION'], aws_access_key_id=os.environ['S3_ACCESS_KEY'], aws_secret_access_key=os.environ['S3_SECRET_KEY'], config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 2}))

def process_one():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
        with conn.transaction():
            row = conn.execute("""SELECT * FROM submissions
                WHERE status='queued' OR (status='processing' AND lease_until < now())
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return False
            attempt = row['attempts'] + 1
            if attempt > 3:
                conn.execute("UPDATE submissions SET status='failed',error='Processing attempts exhausted',updated_at=now(),lease_until=NULL WHERE id=%s", (row['id'],))
                conn.execute("INSERT INTO processing_events(submission_id,event) VALUES(%s,'Processing attempts exhausted')", (row['id'],))
                return True
            conn.execute("UPDATE submissions SET status='processing',attempts=%s,lease_until=now()+interval '2 minutes',updated_at=now() WHERE id=%s", (attempt, row['id']))
            conn.execute("INSERT INTO processing_events(submission_id,event) VALUES(%s,%s)", (row['id'], f'Processing attempt {attempt}'))
        try:
            response = s3.get_object(Bucket=os.environ['S3_BUCKET'], Key=row['object_key'])
            with response['Body'] as stream:
                raw = stream.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError('Document exceeds the 10 MB processing limit')
            text, count = extract(raw, row['sha256'], row['filename'])
            with conn.transaction():
                # Attempt number fences off a worker whose lease was reclaimed.
                result = conn.execute("UPDATE submissions SET status='completed',extracted_text=%s,word_count=%s,pipeline_version=%s,error=NULL,lease_until=NULL,updated_at=now() WHERE id=%s AND status='processing' AND attempts=%s", (text, count, PIPELINE_VERSION, row['id'], attempt))
                if result.rowcount:
                    conn.execute("INSERT INTO processing_events(submission_id,event) VALUES(%s,'Text extraction completed')", (row['id'],))
        except Exception:
            logging.exception('Processing failed for submission %s', row['id'])
            with conn.transaction():
                result = conn.execute("UPDATE submissions SET status=%s,error='Text processing failed',lease_until=NULL,updated_at=now() WHERE id=%s AND status='processing' AND attempts=%s", ('queued' if attempt < 3 else 'failed', row['id'], attempt))
                if result.rowcount:
                    conn.execute("INSERT INTO processing_events(submission_id,event) VALUES(%s,'Processing attempt failed')", (row['id'],))
        return True

if __name__ == '__main__':
    while True:
        try:
            if not process_one():
                time.sleep(2)
        except Exception:
            logging.exception('Worker connection failure')
            time.sleep(5)
