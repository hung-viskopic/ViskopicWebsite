"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { BaselineProfile } from "@/components/BaselineProfile";
import { Empty, ErrorNote, PageHeader, Status } from "@/components/ui";
import { fmtDate, useResource } from "@/lib/hooks";
import type { DocumentRow, Student } from "@/lib/types";

type Detail = Student & {
  enrolments: { id: string; status: string; course_id: string; code: string; title: string }[];
  documents: DocumentRow[];
  can_manage: boolean;
  baseline: {
    essays: number;
    words: number;
    supervised: number;
    eligible: boolean;
    confidence: "low" | "medium" | "high" | null;
    min_essays: number;
    min_words: number;
  };
};

export default function StudentDetail() {
  const { id } = useParams<{ id: string }>();
  const s = useResource<Detail>(`/students/${id}`, 5000);
  if (s.error) return <ErrorNote error={s.error} />;
  if (!s.data) return <p className="muted">Loading…</p>;
  const d = s.data;
  const b = d.baseline;

  return (
    <>
      <PageHeader title={d.pseudonym} meta={d.display_name ?? d.external_ref}>
        <Status value={d.status} />
      </PageHeader>

      <div className="grid">
        <section className="card">
          <h2>Identity</h2>
          <dl className="meta">
            <dt>Student number</dt><dd>{d.external_ref}</dd>
            <dt>Name</dt><dd>{d.display_name ?? "Not given"}</dd>
            <dt>Pseudonym</dt><dd>{d.pseudonym}</dd>
          </dl>
          <h3 className="section-gap">Courses</h3>
          <div className="chips">
            {d.enrolments.map((e) => (
              <Link key={e.id} href={`/courses/${e.course_id}`} className="chip">
                {e.code} {e.status !== "active" && `(${e.status})`}
              </Link>
            ))}
          </div>
        </section>

        <section className="card">
          <h2>Baseline</h2>
          <p className="muted small">
            Needs at least {b.min_essays} essays and {b.min_words.toLocaleString()} words.
          </p>
          <div className="stats">
            <div className="stat"><p className="label">Essays</p><strong>{b.essays}</strong></div>
            <div className="stat"><p className="label">Words</p><strong>{b.words.toLocaleString()}</strong></div>
            <div className="stat"><p className="label">Supervised</p><strong>{b.supervised}</strong></div>
          </div>
          {b.eligible ? (
            <p>Ready for analysis. Confidence: <Status value={b.confidence!} /></p>
          ) : (
            <p className="note">
              Not enough baseline yet. Needs {Math.max(0, b.min_essays - b.essays)} more essays or{" "}
              {Math.max(0, b.min_words - b.words).toLocaleString()} more words needed.
            </p>
          )}
        </section>
      </div>

      <BaselineProfile studentId={d.id} canManage={d.can_manage} />

      <section className="card">
        <h2>Documents</h2>
        {d.documents.length === 0 && <Empty>No documents yet.</Empty>}
        {d.documents.length > 0 && (
          <div className="table-wrap">
          <table>
            <thead><tr><th>File</th><th>Course</th><th>Type</th><th>Written</th><th>Words</th><th>Status</th><th>Uploaded</th></tr></thead>
            <tbody>
              {d.documents.map((x) => (
                <tr key={x.id}>
                  <td><Link href={`/documents/${x.id}`}>{x.original_filename}</Link></td>
                  <td>{x.course_code}</td>
                  <td className="cap">{x.role}</td>
                  <td className="cap">{x.provenance}</td>
                  <td>{x.word_count?.toLocaleString() ?? "-"}</td>
                  <td><Status value={x.status} /></td>
                  <td>{fmtDate(x.uploaded_at)}</td>
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
