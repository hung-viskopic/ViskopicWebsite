"use client";

import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote, PageHeader, Status } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import type { Student } from "@/lib/types";

export default function Students() {
  const list = useResource<Student[]>("/students");
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const rows = (list.data ?? []).filter(
    (s) =>
      !needle ||
      s.pseudonym.toLowerCase().includes(needle) ||
      s.external_ref.toLowerCase().includes(needle) ||
      (s.display_name ?? "").toLowerCase().includes(needle),
  );

  return (
    <>
      <PageHeader title="Students">
        <input className="search" type="search" placeholder="Search students" value={q} onChange={(e) => setQ(e.target.value)} />
      </PageHeader>
      <section className="card">
        <ErrorNote error={list.error} />
        {list.data?.length === 0 && <Empty>No students yet. Add them from a course page.</Empty>}
        {list.data && list.data.length > 0 && rows.length === 0 && <Empty>No students match your search.</Empty>}
        {rows.length > 0 && (
          <div className="table-wrap">
          <table>
            <thead><tr><th>Pseudonym</th><th>Student number</th><th>Name</th><th>Baseline essays</th><th>Submissions</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id}>
                  <td><Link href={`/students/${s.id}`}>{s.pseudonym}</Link></td>
                  <td>{s.external_ref}</td>
                  <td>{s.display_name ?? "-"}</td>
                  <td>{s.baseline_docs}</td>
                  <td>{s.submissions}</td>
                  <td><Status value={s.status} /></td>
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
