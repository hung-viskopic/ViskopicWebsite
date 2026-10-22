#!/bin/sh
# Create the application role: no superuser, no BYPASSRLS, not the table owner.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE viskopic_app LOGIN PASSWORD '${VISKOPIC_APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO viskopic_app;
GRANT USAGE ON SCHEMA public TO viskopic_app;
SQL
