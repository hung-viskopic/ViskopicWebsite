# 0003 Enforce tenant isolation with row-level security

**Status:** accepted · 2026-10-15

Every tenant table has a policy `institution_id = app_institution_id()`, where the setting is applied per
transaction by the API. The app connects as `viskopic_app`, which does not own the tables and has no
`BYPASSRLS`, so a missing `WHERE` clause cannot leak data across institutions. The platform operator never sets a
tenant, so tenant tables return nothing. The few operations that need to cross tenants (login resolution,
institution creation, owner invitation, usage counts, support job health) are narrow `SECURITY DEFINER`
functions that never return essay, student or decision content. RLS is not FORCEd so those functions (owned by the
migration role) work; the app role is still always subject to it.
