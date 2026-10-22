"use client";

import Link from "next/link";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { Course, Department } from "@/lib/types";

export default function Courses() {
  const { me, api } = useAuth();
  const list = useResource<Course[]>("/courses");
  const departments = useResource<Department[]>("/departments");
  const adminDepts = new Set(me!.permissions.admin_department_ids ?? []);
  const creatable = (departments.data ?? []).filter(
    (d) => d.status === "active" && (me!.permissions.is_owner || adminDepts.has(d.id)),
  );

  async function create(f: FormData) {
    await api("/courses", {
      method: "POST",
      body: JSON.stringify({ department_id: f.get("department_id"), code: f.get("code"), title: f.get("title") }),
    });
    list.reload();
  }

  return (
    <>
      <PageHeader title="Courses" />
      {creatable.length > 0 && (
        <section className="card">
          <h2>New course</h2>
          <ActionForm onSubmit={create} submitLabel="Create course" inline>
            <Field label="Department">
              <select name="department_id" required>
                {creatable.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </Field>
            <Field label="Code"><input name="code" required placeholder="LAW1001" /></Field>
            <Field label="Title"><input name="title" required placeholder="Introduction to Contract Law" /></Field>
          </ActionForm>
        </section>
      )}
      <section className="card">
        <ErrorNote error={list.error} />
        {list.data?.length === 0 && <Empty>You don&apos;t have access to any courses yet.</Empty>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
          <table>
            <thead><tr><th>Course</th><th>Department</th><th>Students</th><th>Documents</th><th>Your access</th><th>Status</th></tr></thead>
            <tbody>
              {list.data.map((c) => (
                <tr key={c.id}>
                  <td><Link href={`/courses/${c.id}`}><strong>{c.code}</strong></Link><span className="sub">{c.title}</span></td>
                  <td>{c.department_name}</td>
                  <td>{c.active_students}</td>
                  <td>{c.documents}</td>
                  <td>{c.access === "manage" ? "Manage" : "View"}</td>
                  <td><Status value={c.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </>
  );
}
