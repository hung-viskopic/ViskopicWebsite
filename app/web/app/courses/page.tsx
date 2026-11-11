"use client";

import Link from "next/link";
import { useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { Course, Department } from "@/lib/types";
import { plural } from "@/lib/words";

export default function Courses() {
  const { me, api } = useAuth();
  const list = useResource<Course[]>("/courses");
  const departments = useResource<Department[]>("/departments");
  const [adding, setAdding] = useState(false);
  const adminDepts = new Set(me!.permissions.admin_department_ids ?? []);
  const creatable = (departments.data ?? []).filter(
    (d) => d.status === "active" && (me!.permissions.is_owner || adminDepts.has(d.id)),
  );

  async function create(f: FormData) {
    await api("/courses", {
      method: "POST",
      body: JSON.stringify({ department_id: f.get("department_id"), code: f.get("code"), title: f.get("title") }),
    });
    setAdding(false);
    list.reload();
  }

  const active = (list.data ?? []).filter((c) => c.status === "active");
  const archived = (list.data ?? []).filter((c) => c.status !== "active");

  return (
    <>
      <PageHeader title="Courses" meta="Upload essays and manage students from a course.">
        {creatable.length > 0 && !adding && <button className="btn secondary" onClick={() => setAdding(true)}>Add a course</button>}
      </PageHeader>
      {adding && (
        <section className="card">
          <h2>Add a course</h2>
          <ActionForm onSubmit={create} submitLabel="Add course" inline>
            <Field label="Department">
              <select name="department_id" required>
                {creatable.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </Field>
            <Field label="Course code"><input name="code" required placeholder="LAW1001" /></Field>
            <Field label="Course name"><input name="title" required placeholder="Introduction to Contract Law" /></Field>
          </ActionForm>
          <button className="link" onClick={() => setAdding(false)}>Cancel</button>
        </section>
      )}
      <ErrorNote error={list.error} />
      {list.data?.length === 0 && <section className="card"><Empty>You don&apos;t have access to any courses yet. Ask your department&apos;s admin.</Empty></section>}
      <div className="tiles">
        {active.map((c) => <Tile key={c.id} c={c} />)}
      </div>
      {archived.length > 0 && (
        <details className="card" style={{ marginTop: 24 }}>
          <summary>Archived courses ({archived.length})</summary>
          <div className="tiles" style={{ marginTop: 16 }}>{archived.map((c) => <Tile key={c.id} c={c} />)}</div>
        </details>
      )}
    </>
  );
}

function Tile({ c }: { c: Course }) {
  return (
    <Link href={`/courses/${c.id}`} className="tile">
      <span className="muted">{c.code} · {c.department_name}</span>
      <h2>{c.title}</h2>
      <div className="facts">
        <span>{plural(c.active_students ?? 0, "student")}</span>
        <span>{plural(c.documents ?? 0, "essay")}</span>
        <span>{c.access === "manage" ? "You can upload" : "View only"}</span>
        {c.status !== "active" && <Status value={c.status} />}
      </div>
    </Link>
  );
}
