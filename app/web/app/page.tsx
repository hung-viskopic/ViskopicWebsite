"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Empty, ErrorNote, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, roleLabel, useResource } from "@/lib/hooks";
import type { Course, DocumentRow } from "@/lib/types";

export default function Overview() {
  const { me } = useAuth();
  const router = useRouter();
  const operator = me!.kind === "platform_operator";
  useEffect(() => {
    if (operator) router.replace("/platform");
  }, [operator, router]);

  const courses = useResource<Course[]>(operator ? null : "/courses");
  const docs = useResource<DocumentRow[]>(operator ? null : "/documents", 5000);
  if (operator) return null;

  const failed = docs.data?.filter((d) => d.status === "failed").length ?? 0;
  const pending = docs.data?.filter((d) => d.status === "queued" || d.status === "processing").length ?? 0;

  return (
    <>
      <PageHeader title="Overview" />
      {me!.roles.length === 0 && (
        <p className="note info">You don&apos;t have any roles yet. Ask your institution owner or department admin for access.</p>
      )}
      <div className="stats">
        <div className="stat"><p className="label">Courses</p><strong>{courses.data?.length ?? "-"}</strong></div>
        <div className="stat"><p className="label">Documents</p><strong>{docs.data?.length ?? "-"}</strong></div>
        <div className="stat"><p className="label">Processing</p><strong>{pending}</strong></div>
        <div className="stat"><p className="label">Failed</p><strong>{failed}</strong></div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Recent documents</h2>
          <Link href="/courses">All courses</Link>
        </div>
        <ErrorNote error={docs.error} />
        {docs.data && docs.data.length === 0 && <Empty>No documents uploaded yet.</Empty>}
        {docs.data && docs.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>File</th><th>Student</th><th>Course</th><th>Type</th><th>Status</th><th>Uploaded</th></tr>
              </thead>
              <tbody>
                {docs.data.slice(0, 10).map((d) => (
                  <tr key={d.id}>
                    <td><Link href={`/documents/${d.id}`}>{d.original_filename}</Link></td>
                    <td><Link href={`/students/${d.student_id}`}>{d.student_pseudonym}</Link></td>
                    <td>{d.course_code}</td>
                    <td className="cap">{d.role}</td>
                    <td><Status value={d.status} /></td>
                    <td>{fmtDate(d.uploaded_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {me!.roles.length > 0 && (
        <section className="card">
          <h2>Your roles</h2>
          <ul className="list-plain">
            {me!.roles.map((r) => (
              <li key={r.id}>
                <strong>{roleLabel[r.role]}</strong>
                <span className="muted">{r.scope_name}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
