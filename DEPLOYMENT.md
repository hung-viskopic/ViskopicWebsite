# Viskopic demo deployment

This repository deploys four Render services from `render.yaml`:

- `viskopic-marketing`: public static company website
- `viskopic-workspace`: public web gateway for the research workspace
- `viskopic-api`: private NestJS API, reachable only from Render's private network
- `viskopic-worker`: private Python document-processing worker

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
4. During creation, Render asks for every variable marked `sync: false`. Enter the same Supabase values for both API and worker where requested.

Use these mappings:

| Render variable | Supabase value |
| --- | --- |
| `DATABASE_URL` | Session pooler connection string with TLS required |
| `S3_ENDPOINT` | Direct endpoint ending in `/storage/v1/s3` |
| `S3_ACCESS_KEY` | Server-side S3 access key ID |
| `S3_SECRET_KEY` | Server-side S3 secret access key |
| `DEV_ACCESS_KEY` | A new random key of at least 32 characters; API only |

The Blueprint supplies `S3_REGION`, `S3_BUCKET`, service ports, organisation identifier, and private API discovery automatically.

Wait until all four services are green. Open the temporary `onrender.com` URL for `viskopic-workspace`, enter the new workspace access key, create a synthetic student, and upload a synthetic TXT or DOCX file. Confirm that processing reaches `completed` before changing DNS.

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

The API is not assigned a public domain. Browser requests to `/api/*` pass through the workspace gateway to the API over Render's private network.
