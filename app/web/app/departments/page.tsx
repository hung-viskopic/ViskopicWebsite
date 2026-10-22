"use client";

import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { Department } from "@/lib/types";

export default function Departments() {
  const { api } = useAuth();
  const list = useResource<Department[]>("/departments");

  async function create(f: FormData) {
    await api("/departments", { method: "POST", body: JSON.stringify({ name: f.get("name") }) });
    list.reload();
  }

  async function toggle(d: Department) {
    await api(`/departments/${d.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: d.status === "active" ? "archived" : "active" }),
    });
    list.reload();
  }

  return (
    <>
      <PageHeader title="Departments" />
      <section className="card">
        <ActionForm onSubmit={create} submitLabel="Add department" inline>
          <Field label="Name"><input name="name" required placeholder="School of Law" /></Field>
        </ActionForm>
      </section>
      <section className="card">
        <ErrorNote error={list.error} />
        {list.data?.length === 0 && <Empty>No departments yet. Add one before creating courses.</Empty>}
        {list.data && list.data.length > 0 && (
          <table>
            <thead><tr><th>Department</th><th>Status</th><th /></tr></thead>
            <tbody>
              {list.data.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td><Status value={d.status} /></td>
                  <td><button className="btn secondary" onClick={() => toggle(d)}>{d.status === "active" ? "Archive" : "Restore"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
