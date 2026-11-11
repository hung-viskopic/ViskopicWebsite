"use client";

import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote, PageHeader, Status } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import type { Student } from "@/lib/types";
import { plural } from "@/lib/words";

/** The writing style check needs this many past essays (the model's own rule). */
const NEEDED = 8;

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
      <PageHeader title="Students" meta="Students appear as pseudonyms. Open one to see whether the writing style check can run.">
        <input className="search" type="search" aria-label="Search students" placeholder="Search by pseudonym, number or name"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </PageHeader>
      <ErrorNote error={list.error} />
      <section className="group">
        {list.data?.length === 0 && <div className="row"><Empty>No students yet. Add them from a course.</Empty></div>}
        {list.data && list.data.length > 0 && rows.length === 0 && <div className="row"><Empty>No students match “{q}”.</Empty></div>}
        {rows.map((s) => {
          const n = s.baseline_docs ?? 0;
          return (
            <div className="row" key={s.id}>
              <div className="grow">
                <Link href={`/students/${s.id}`} className="who link">{s.pseudonym}</Link>
                <span className="sub">{s.external_ref}{s.display_name ? ` · ${s.display_name}` : ""} · {plural(s.submissions ?? 0, "essay")} checked</span>
              </div>
              <div style={{ flex: "0 1 260px", minWidth: 200 }}>
                <div className="progress" aria-hidden><span style={{ width: `${Math.min(100, (n / NEEDED) * 100)}%`, background: n >= NEEDED ? "var(--ok)" : undefined }} /></div>
                <span className="small muted">{n} of {NEEDED} past essays uploaded</span>
              </div>
              {s.status !== "active" && <Status value={s.status} />}
            </div>
          );
        })}
      </section>
      <p className="hint">Past essays must each have at least 1,650 words to count. The student page shows which ones are too short.</p>
    </>
  );
}
