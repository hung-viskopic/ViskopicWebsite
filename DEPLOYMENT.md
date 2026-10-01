# Viskopic demo deployment

This repository deploys two free Render services from `render.yaml`:

- `viskopic-marketing`: public static company website
- `viskopic-workspace`: React workspace, NestJS API, and Python document-processing worker in one free web service

PostgreSQL and private document storage are supplied by one Supabase project in Frankfurt. This configuration is for synthetic demonstrations and internal product work. Do not upload identifiable student work until named accounts, tenant isolation, retention controls, and production security review are complete.

## 1. Create Supabase resources

1. Create a Supabase project in **Central EU (Frankfurt)**.
2. In Storage, create a **private** file bucket named `submissions`.
3. In Storage settings, open the S3 configuration page, enable S3 access, and generate server-side S3 access keys.
4. Copy the S3 endpoint, region, access key ID, and secret access key into a temporary password manager entry.
5. Open **Connect** in the Supabase project and copy the **Session pooler** connection string on port `5432`.
6. Insert the database password and append `?sslmode=require` if the copied connection string has no query string. If it already has a query string, append `&sslmode=require`.

Never commit these values or paste them into an issue, pull request, or chat.

## 2. Create the Render Blueprint

1. In Render, choose **New > Blueprint**.
2. Select the private `hung-viskopic/ViskopicWebsite` repository and the `main` branch.
3. Keep the Blueprint path as `render.yaml`.
4. During creation, Render asks for every variable marked `sync: false`. Enter the Supabase values when requested.

Use these mappings:

| Render variable | Supabase value |
| --- | --- |
| `DATABASE_URL` | Session pooler connection string with TLS required |
| `S3_ENDPOINT` | Direct endpoint ending in `/storage/v1/s3` |
| `S3_ACCESS_KEY` | Server-side S3 access key ID |
| `S3_SECRET_KEY` | Server-side S3 secret access key |
| `DEV_ACCESS_KEY` | A new random key of at least 32 characters |

The Blueprint supplies `S3_REGION`, `S3_BUCKET`, the service port, and organisation identifier automatically.

Wait until both services are green. Open the temporary `onrender.com` URL for `viskopic-workspace`, enter the new workspace access key, create a synthetic student, and upload a synthetic TXT or DOCX file. Confirm that processing reaches `completed` before changing DNS.

The free workspace sleeps after 15 minutes without inbound traffic and can take about a minute to wake. The API and document worker run together, which is suitable for demonstrations but not production isolation or continuous background processing.

## 3. Connect GoDaddy DNS

Keep the existing `viskopic.com` records unchanged until the temporary Render URLs are verified.

1. Add `app.viskopic.com` as a custom domain on the `viskopic-workspace` Render service.
2. Render displays the exact DNS target. In GoDaddy DNS, add the CNAME record Render requests, normally with host `app` and Render's service hostname as the value.
3. Verify the custom domain in Render and wait for its managed TLS certificate.
4. Add `viskopic.com` and `www.viskopic.com` to `viskopic-marketing` only when ready to move the public website from its existing host.
5. Replace the GoDaddy root and `www` records with the exact values shown by Render. Do not delete mail-related MX, TXT, SPF, DKIM, or DMARC records.

DNS changes can take time to propagate. Keep the Render temporary URLs until both domains work over HTTPS.

## 4. Deployment behavior

Pushes to `main` automatically redeploy the affected services. The API applies the additive SQL migrations at startup. Original files go to the private Supabase bucket; extracted text, records, and processing jobs go to Supabase PostgreSQL.

Browser requests to `/api/*` and the workspace UI are served by the same Render service. Supabase remains the persistent database and object store; no durable data is written to Render's local filesystem.

## 5. Existing submissions before the NOT NULL migration

New databases need no manual SQL. Existing databases must have a real student assigned to every submission before migration 003 can run. Check with:

```sql
SELECT id, organisation_id, filename FROM submissions WHERE student_id IS NULL;
```

For each result, create or identify the actual student and update the matching submission. Replace the UUID placeholders with real IDs; the organisation condition prevents cross-organisation assignment:

```sql
UPDATE submissions AS d
SET student_id = s.id, updated_at = now()
FROM students AS s
WHERE d.id = 'SUBMISSION_UUID'::uuid
  AND s.id = 'ACTUAL_STUDENT_UUID'::uuid
  AND d.organisation_id = s.organisation_id
  AND d.student_id IS NULL;
```

Repeat the SELECT until it returns no rows, then deploy/restart the API. Migration 003 preserves documents and sets student_id NOT NULL. It stops with a descriptive error if any ownership is unresolved.

## 6. AWS alternative for temporary testing

AWS accounts created from July 15, 2025 use a Free Plan lasting up to six months or until credits run out. New customers receive $100 in credits and can earn up to $100 more. EC2 is not permanently free. Check account eligibility and available services in AWS Billing before provisioning.

For an eligible account, use a small x86-64 Ubuntu EC2 instance in Frankfurt with Docker, and retain Supabase for the database and files. Build the combined image with `docker build -t viskopic-demo -f platform/Dockerfile platform`, then run it using a protected environment file containing the five secrets plus `PORT=10000`, `S3_REGION=eu-central-1`, `S3_BUCKET=submissions`, and `ORGANISATION_ID=viskopic-demo`. Publish the container only on `127.0.0.1:10000` and serve it through an HTTPS reverse proxy. The combined container replaces the Render web service; the application schema and storage interfaces stay the same.

Render + Supabase is the recommended first demo because the repository already supplies its deployment configuration and Render manages HTTPS. AWS requires VM maintenance and proxy setup, and credits have a time limit.

Provider references: https://render.com/docs/free, https://supabase.com/pricing, https://aws.amazon.com/free/free-tier-faqs/.

## 7. Student profiles upgrade

Migration 004 separates student profiles from courses. It creates organisations and enrolments, fills each legacy submission's course_id from its student's original course, and adds a composite foreign key to enrolments. All document IDs, object keys, text and history remain intact. Students with duplicate references in an organisation are kept separate; later duplicates receive an explicit UUID legacy suffix and retain the original reference in legacy_reference. Reconcile these identities manually before any future merge.

Migrations now run once and are tracked in schema_migrations; restarting the API will not recreate the old course_id column on students. A new deployment needs the same five Render secrets. No new service or hosting fee is required.

UI workflow: Students > student profile > Enrol in course > select upload course > Upload document. The profile shows documents from all courses, with course, status and date filters. Courses > Add existing student links an existing profile to a course. Enrolments containing documents can be archived but cannot be removed until those documents are moved to another valid enrolment.
