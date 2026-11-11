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
      <PageHeader title="Departments" meta="Courses belong to a department. Department admins manage the courses and people in theirs." />
      <section className="card">
        <h2>Add a department</h2>
        <ActionForm onSubmit={create} submitLabel="Add department" inline>
          <Field label="Name"><input name="name" required placeholder="School of Law" /></Field>
        </ActionForm>
      </section>
      <ErrorNote error={list.error} />
      <section className="group">
        {list.data?.length === 0 && <div className="row"><Empty>No departments yet. Add one before adding courses.</Empty></div>}
        {list.data?.map((d) => (
          <div key={d.id} className="row">
            <div className="grow"><span className="who">{d.name}</span></div>
            {d.status !== "active" && <Status value={d.status} />}
            <button className="link" onClick={() => toggle(d)}>{d.status === "active" ? "Archive" : "Restore"}</button>
          </div>
        ))}
      </section>
    </>
  );
}
