# 0002 Use Postgres as the job queue

**Status:** accepted · 2026-10-15

Jobs are rows in `jobs`, claimed with `FOR UPDATE SKIP LOCKED`, with a lease and numbered attempts. This avoids
running Redis for the MVP, keeps job creation in the same transaction as the document insert (no lost jobs), and
is plenty for university-scale volumes. Revisit if throughput or LLM-call fan-out outgrows it.
