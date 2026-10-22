"use client";

import { useMemo, useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, roleLabel, useResource } from "@/lib/hooks";
import type { Course, Department, User } from "@/lib/types";

const scopeOf: Record<string, "institution" | "department" | "course"> = {
  institution_owner: "institution",
  appeals_reviewer: "institution",
  department_admin: "department",
  reviewer: "course",
  viewer: "course",
};

const PAGE_SIZE = 25;

export default function People() {
  const { me, api } = useAuth();
  const users = useResource<User[]>("/users");
  const departments = useResource<Department[]>("/departments");
  const courses = useResource<Course[]>("/courses");
  const [role, setRole] = useState("reviewer");
  const [actionError, setActionError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [scopeFilter, setScopeFilter] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);

  const isOwner = !!me!.permissions.is_owner;
  const adminDepts = new Set(me!.permissions.admin_department_ids ?? []);
  const grantableRoles = isOwner
    ? Object.keys(roleLabel)
    : ["department_admin", "reviewer", "viewer"];
  const deptOptions = (departments.data ?? []).filter((d) => isOwner || adminDepts.has(d.id));
  const courseOptions = (courses.data ?? []).filter((c) => isOwner || adminDepts.has(c.department_id));

  function scopeName(scopeType: string, id: string) {
    if (scopeType === "institution") return "Whole institution";
    if (scopeType === "department") return departments.data?.find((d) => d.id === id)?.name ?? "Department";
    const c = courses.data?.find((c) => c.id === id);
    return c ? `${c.code} ${c.title}` : "Course";
  }

  const all = users.data ?? [];
  const statuses = useMemo(() => Array.from(new Set(all.map((u) => u.status))).sort(), [all]);
  const filtering = !!(q.trim() || statusFilter || roleFilter || scopeFilter);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter((u) => {
      if (needle && !u.email.toLowerCase().includes(needle) && !(u.display_name ?? "").toLowerCase().includes(needle)) {
        return false;
      }
      if (statusFilter && u.status !== statusFilter) return false;
      if (roleFilter === "none") return u.roles.length === 0 && !scopeFilter;
      if (roleFilter || scopeFilter) {
        // Role and place must match on the same assignment.
        return u.roles.some(
          (r) =>
            (!roleFilter || r.role === roleFilter) &&
            (!scopeFilter ||
              (scopeFilter === "institution" ? r.scope_type === "institution" : `${r.scope_type}:${r.scope_id}` === scopeFilter)),
        );
      }
      return true;
    });
  }, [all, q, statusFilter, roleFilter, scopeFilter]);

  function setFilter(fn: (v: string) => void) {
    return (v: string) => {
      fn(v);
      setLimit(PAGE_SIZE);
    };
  }

  function clearFilters() {
    setQ("");
    setStatusFilter("");
    setRoleFilter("");
    setScopeFilter("");
    setLimit(PAGE_SIZE);
  }

  const shown = filtered.slice(0, limit);

  async function invite(f: FormData) {
    await api("/users", { method: "POST", body: JSON.stringify({ email: f.get("email"), display_name: f.get("display_name") || null }) });
    users.reload();
  }

  async function grant(f: FormData) {
    const scope_type = scopeOf[role];
    await api("/role-assignments", {
      method: "POST",
      body: JSON.stringify({
        user_id: f.get("user_id"),
        role,
        scope_type,
        scope_id: scope_type === "institution" ? me!.institution_id : f.get("scope_id"),
      }),
    });
    users.reload();
  }

  async function run(fn: () => Promise<unknown>) {
    setActionError(null);
    try {
      await fn();
      users.reload();
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  return (
    <>
      <PageHeader title="People and roles" />

      <div className="grid">
        <section className="card">
          <h2>Invite someone</h2>
          <p className="muted small">They sign in with their university account. The invite is matched on first sign-in.</p>
          <ActionForm onSubmit={invite} submitLabel="Invite">
            <Field label="Email"><input name="email" type="email" required /></Field>
            <Field label="Name (optional)"><input name="display_name" /></Field>
          </ActionForm>
        </section>

        <section className="card">
          <h2>Grant a role</h2>
          <p className="muted small">You can only grant roles within your own scope.</p>
          <ActionForm onSubmit={grant} submitLabel="Grant role">
            <Field label="Person">
              <select name="user_id" required>
                <option value="">Choose…</option>
                {users.data?.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
              </select>
            </Field>
            <Field label="Role">
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {grantableRoles.map((r) => <option key={r} value={r}>{roleLabel[r]}</option>)}
              </select>
            </Field>
            {scopeOf[role] === "department" && (
              <Field label="Department">
                <select name="scope_id" required>
                  <option value="">Choose…</option>
                  {deptOptions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </Field>
            )}
            {scopeOf[role] === "course" && (
              <Field label="Course">
                <select name="scope_id" required>
                  <option value="">Choose…</option>
                  {courseOptions.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
                </select>
              </Field>
            )}
          </ActionForm>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Everyone</h2>
          {users.data && (
            <span className="muted small">
              {filtering ? `${filtered.length} of ${all.length} people` : `${all.length} ${all.length === 1 ? "person" : "people"}`}
            </span>
          )}
        </div>
        {all.length > 0 && (
          <div className="filters">
            <input
              type="search"
              placeholder="Search name or email"
              aria-label="Search name or email"
              value={q}
              onChange={(e) => setFilter(setQ)(e.target.value)}
            />
            <select aria-label="Status" value={statusFilter} onChange={(e) => setFilter(setStatusFilter)(e.target.value)}>
              <option value="">Any status</option>
              {statuses.map((st) => (
                <option key={st} value={st}>{st.charAt(0).toUpperCase() + st.slice(1)}</option>
              ))}
            </select>
            <select aria-label="Role" value={roleFilter} onChange={(e) => setFilter(setRoleFilter)(e.target.value)}>
              <option value="">Any role</option>
              {Object.keys(roleLabel).map((r) => (
                <option key={r} value={r}>{roleLabel[r]}</option>
              ))}
              <option value="none">No roles</option>
            </select>
            <select
              aria-label="Department or course"
              value={scopeFilter}
              disabled={roleFilter === "none"}
              onChange={(e) => setFilter(setScopeFilter)(e.target.value)}
            >
              <option value="">Anywhere</option>
              <option value="institution">Whole institution</option>
              {(departments.data ?? []).length > 0 && (
                <optgroup label="Departments">
                  {departments.data!.map((d) => (
                    <option key={d.id} value={`department:${d.id}`}>{d.name}</option>
                  ))}
                </optgroup>
              )}
              {(courses.data ?? []).length > 0 && (
                <optgroup label="Courses">
                  {courses.data!.map((c) => (
                    <option key={c.id} value={`course:${c.id}`}>{c.code} {c.title}</option>
                  ))}
                </optgroup>
              )}
            </select>
            {filtering && (
              <button className="link" onClick={clearFilters}>Clear filters</button>
            )}
          </div>
        )}
        <ErrorNote error={users.error ?? actionError} />
        {users.data?.length === 0 && <Empty>No one yet.</Empty>}
        {all.length > 0 && filtered.length === 0 && <Empty>No one matches these filters.</Empty>}
        {shown.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Person</th><th>Status</th><th>Roles</th><th>Last sign-in</th><th /></tr></thead>
              <tbody>
                {shown.map((u) => (
                  <tr key={u.id}>
                    <td>{u.display_name ? <><strong>{u.display_name}</strong><span className="sub">{u.email}</span></> : u.email}</td>
                    <td><Status value={u.status} /></td>
                    <td>
                      {u.roles.length === 0 && <span className="muted">No roles</span>}
                      {u.roles.map((r) => (
                        <div key={r.id} className="role-row">
                          <span>{roleLabel[r.role]} <span className="muted">({scopeName(r.scope_type, r.scope_id)})</span></span>
                          <button className="link" onClick={() => run(() => api(`/role-assignments/${r.id}`, { method: "DELETE" }))}>
                            Revoke
                          </button>
                        </div>
                      ))}
                    </td>
                    <td>{fmtDate(u.last_login_at)}</td>
                    <td>
                      {isOwner && u.id !== me!.user_id && (
                        u.status === "disabled" ? (
                          <button className="btn secondary" onClick={() => run(() => api(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) }))}>Enable</button>
                        ) : (
                          <button className="btn danger" onClick={() => run(() => api(`/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ status: "disabled" }) }))}>Disable</button>
                        )
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {filtered.length > shown.length && (
          <div className="more">
            <button className="btn secondary" onClick={() => setLimit((l) => l + PAGE_SIZE)}>
              Show {Math.min(PAGE_SIZE, filtered.length - shown.length)} more
            </button>
            <span className="muted small">Showing {shown.length} of {filtered.length}</span>
          </div>
        )}
      </section>
    </>
  );
}
