# 0001 Build the MVP fresh on Python + Next.js

**Status:** accepted · 2026-10-15

The repository already had a NestJS/Vite prototype in `platform/` with working intake but a shared dev key, a
single organisation and no tenant isolation. The analysis pipelines will be Python (spaCy, scikit-learn,
sentence-transformers), so a Python backend lets the API, worker and pipelines share one codebase and one data
model. We rebuilt in `app/` with FastAPI + Next.js. `platform/` stays for reference and can be removed once the
new app covers it.
