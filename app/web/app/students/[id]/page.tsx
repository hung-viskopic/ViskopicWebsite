"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { BaselineProfile } from "@/components/BaselineProfile";
import { Empty, ErrorNote, PageHeader, Status } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import type { DocumentRow, ProfilesResponse, Student } from "@/lib/types";
import { plural, shortDate } from "@/lib/words";

type Detail = Student & {
  enrolments: { id: string; status: string; course_id: string; code: string; title: string }[];
  documents: DocumentRow[];
  can_manage: boolean;
  baseline: {
    essays: number;
    words: number;
    supervised: number;
    too_short: number;
    eligible: boolean;
    confidence: "low" | "medium" | "high" | null;
    min_essays: number;
    min_words: number;
  };
};

export default function StudentDetail() {
  const { id } = useParams<{ id: string }>();
  const s = useResource<Detail>(`/students/${id}`, 5000);
  const prof = useResource<ProfilesResponse>(`/students/${id}/profiles`, 5000);
  if (s.error) return <ErrorNote error={s.error} />;
  if (!s.data) return <p className="muted">Loading…</p>;
  const d = s.data;
  const b = d.baseline;
  const checked = prof.data?.consistency.quality_passed_documents ?? 0;
  const usable = b.essays;
  const slots = Math.max(b.min_essays, usable);
  const toCheck = Math.max(0, usable - checked);
  const missing = Math.max(0, b.min_essays - usable);
  const past = d.documents.filter((x) => x.role === "baseline");
  const checkedNow = d.documents.filter((x) => x.role === "submission");

  let sentence: string;
  if (missing > 0) sentence = `Writing style can't run yet. ${plural(missing, "more past essay")} ${missing === 1 ? "is" : "are"} needed.`;
  else if (toCheck > 0) sentence = `Almost ready. ${plural(toCheck, "past essay")} still ${toCheck === 1 ? "needs" : "need"} the text checked.`;
  else sentence = "Ready. The writing style check can compare new essays with this student's past work.";

  return (
    <>
      <Link href="/students" className="back">← Students</Link>
      <PageHeader title={d.pseudonym} meta={`${d.external_ref}${d.display_name ? ` · ${d.display_name}` : ""}`}>
        {d.status !== "active" && <Status value={d.status} />}
      </PageHeader>

      <section className={`card summary ${missing > 0 || toCheck > 0 ? "tone-attn" : "tone-calm"}`}>
        <div className="say">
          <p className="sentence">{sentence}</p>
          <div className="slots" style={{ "--n": slots } as React.CSSProperties} aria-label={`${checked} checked, ${toCheck} to check, ${missing} missing, of ${b.min_essays}`}>
            {Array.from({ length: slots }, (_, i) => {
              const cls = i < Math.min(checked, usable) ? "done" : i < usable ? "wait" : "";
              return <span key={i} className={cls}>{i + 1}</span>;
            })}
          </div>
          <div className="legend">
            <span><i style={{ background: "var(--ink)" }} />Checked ({Math.min(checked, usable)})</span>
            <span><i style={{ background: "var(--blue-soft)", border: "2px solid var(--blue)" }} />Uploaded, text not checked ({toCheck})</span>
            <span><i style={{ border: "2px dashed var(--field)" }} />Still needed ({missing})</span>
          </div>
          <p className="muted" style={{ margin: 0 }}>
            Each past essay needs at least {b.min_words.toLocaleString()} words.
            {b.too_short > 0 && ` ${plural(b.too_short, "uploaded essay")} ${b.too_short === 1 ? "is" : "are"} too short and won't count.`}
            {b.eligible && b.confidence && ` Results will have ${b.confidence} confidence${b.supervised ? ` (${b.supervised} written in an exam)` : ""}.`}
          </p>
        </div>
        <div className="act-col">
          <h2>Courses</h2>
          <div className="chips">
            {d.enrolments.map((e) => (
              <Link key={e.id} href={`/courses/${e.course_id}`} className="chip">
                {e.code}{e.status !== "active" ? ` (${e.status})` : ""}
              </Link>
            ))}
          </div>
          {d.can_manage && d.enrolments[0] && (
            <Link href={`/courses/${d.enrolments[0].course_id}`} className="btn secondary">Upload past essays</Link>
          )}
        </div>
      </section>

      <div className="grid">
        <DocList title="Essays being checked" docs={checkedNow} empty="No essays are being checked for this student." minWords={b.min_words} />
        <DocList title="Past essays" docs={past} empty="No past essays uploaded yet." minWords={b.min_words} />
      </div>

      <details className="card">
        <summary>Technical details: writing style profile</summary>
        <div style={{ marginTop: 16 }}><BaselineProfile studentId={d.id} canManage={d.can_manage} /></div>
      </details>
    </>
  );
}

function DocList({ title, docs, empty, minWords }: { title: string; docs: DocumentRow[]; empty: string; minWords: number }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {docs.length === 0 && <Empty>{empty}</Empty>}
      {docs.map((x) => {
        const short = x.role === "baseline" && x.word_count != null && x.word_count < minWords;
        return (
          <div key={x.id} className="assignment">
            <Link href={`/documents/${x.id}`} className="link"><strong>{x.assignment_title ?? x.original_filename}</strong></Link>
            <p className="small muted">
              {x.course_code} · {x.word_count ? `${x.word_count.toLocaleString()} words` : "not read yet"}
              {x.provenance === "supervised" ? " · written in an exam" : ""} · uploaded {shortDate(x.uploaded_at)}
            </p>
            {short ? <Status value="x" tone="attn" label="Too short to count" />
              : x.status !== "completed" && <Status value={x.status} />}
          </div>
        );
      })}
    </section>
  );
}
