"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import { EvidenceReport } from "@/components/EvidenceReport";
import { VivaPanel } from "@/components/VivaPanel";
import { ActionForm, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtBytes, fmtDate, useResource } from "@/lib/hooks";
import type { DocumentRow, QualityReview } from "@/lib/types";
import { plural } from "@/lib/words";

type Detail = DocumentRow & {
  access: "manage" | "view";
  can_remove: boolean;
  signals: {
    hidden_text_chars: number;
    hidden_text_spans: { reason: string; chars: number; pages?: number }[];
    metadata: Record<string, string | number>;
  } | null;
  text_meta: { word_count: number; char_count: number; extractor_version: string; extracted_at: string } | null;
  quality_review: QualityReview | null;
};

const hiddenReason: Record<string, string> = {
  white_text: "white text on the page",
  tiny_text: "text at 2pt or smaller",
  hidden_formatting: "text formatted as hidden",
  invisible_render_mode: "invisible text layer in the PDF",
};

const metaLabel: Record<string, string> = {
  author: "Author",
  last_modified_by: "Last modified by",
  created: "Created",
  modified: "Modified",
  revision: "Revision count",
  total_edit_minutes: "Total editing time (minutes)",
  application: "Application",
  creator_tool: "Created with",
  producer: "PDF producer",
  pages: "Pages",
  declared_words: "Word count in file",
};

type Tab = "evidence" | "viva" | "file";

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const { api } = useAuth();
  const doc = useResource<Detail>(`/documents/${id}`, 3000);
  const [text, setText] = useState<string | null>(null);
  const [textOpen, setTextOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("evidence");
  const [planViva, setPlanViva] = useState(false);
  // Bumped after a text check so the evidence follows the re-analysis it triggers.
  const [reportKey, setReportKey] = useState(0);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "viva" || t === "file") setTab(t);
  }, []);

  if (doc.error) return <ErrorNote error={doc.error} />;
  if (!doc.data) return <p className="muted">Loading…</p>;
  const d = doc.data;
  const editMins = Number(d.signals?.metadata.total_edit_minutes);
  const fastEdit = d.word_count && editMins >= 0 && !Number.isNaN(editMins) && editMins < d.word_count / 100;
  const isCase = d.role === "submission" && d.status === "completed";
  const current: Tab = isCase ? tab : "file";

  async function showText() {
    try {
      if (text === null) setText((await api<{ text: string }>(`/documents/${id}/text`)).text);
      setTextOpen(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function retry() {
    try {
      await api(`/documents/${id}/retry`, { method: "POST" });
      doc.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function go(t: Tab) {
    setTab(t);
    window.history.replaceState(null, "", t === "evidence" ? window.location.pathname : `?tab=${t}`);
  }

  return (
    <>
      <Link href="/" className="back">← Cases</Link>
      <PageHeader
        title={isCase ? `${d.student_pseudonym}: ${d.assignment_title ?? d.original_filename}` : d.original_filename}
        meta={<>
          <Link href={`/students/${d.student_id}`} className="link">{d.student_pseudonym}</Link> ·{" "}
          <Link href={`/courses/${d.course_id}`} className="link">{d.course_code}</Link> ·{" "}
          {d.role === "submission" ? "essay being checked" : "past essay"}
          {d.provenance === "supervised" ? ", written in an exam" : ""}
          {d.word_count ? ` · ${d.word_count.toLocaleString()} words` : ""}
        </>}
      >
        {d.status !== "completed" && <Status value={d.status} />}
        {d.status === "failed" && d.access === "manage" && <button className="btn" onClick={retry}>Try again</button>}
      </PageHeader>
      <ErrorNote error={error} />
      {d.status === "removed" && (
        <p className="note">
          Removed on {fmtDate(d.removed_at)} by {d.removed_by_email}. Reason: &ldquo;{d.removal_reason}&rdquo;. The file
          and its text were deleted. {d.role === "baseline" ? "It no longer counts toward the student's past essays." : "Its case was withdrawn."}
        </p>
      )}
      {d.last_error && d.status !== "removed" && (
        <p className="note">{d.status === "failed" ? "The file couldn't be read: " : "Reading the file failed once and will be retried: "}{d.last_error}</p>
      )}

      {isCase && (
        <div className="tabs" role="tablist" aria-label="Case sections">
          {([["evidence", "Evidence"], ["viva", "Viva"], ["file", "File details"]] as [Tab, string][]).map(([t, l]) => (
            <button key={t} id={`tab-${t}`} role="tab" className={current === t ? "on" : ""} aria-selected={current === t}
              aria-controls="case-panel" tabIndex={current === t ? 0 : -1} onClick={() => go(t)}
              onKeyDown={(e) => {
                const order: Tab[] = ["evidence", "viva", "file"];
                const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!step) return;
                const next = order[(order.indexOf(current) + step + order.length) % order.length];
                go(next);
                document.getElementById(`tab-${next}`)?.focus();
              }}>{l}</button>
          ))}
        </div>
      )}

      <div id="case-panel" {...(isCase ? { role: "tabpanel", "aria-labelledby": `tab-${current}` } : {})}>
      {isCase && current === "evidence" && (
        <EvidenceReport documentId={d.id} canManage={d.access === "manage"} refreshKey={reportKey}
          onSendViva={d.access === "manage" ? () => { setPlanViva(true); go("viva"); } : undefined} />
      )}

      {isCase && current === "viva" && <VivaPanel documentId={d.id} startOpen={planViva} />}

      {current === "file" && (
        <>
          <div className="grid">
            <section className="card">
              <h2>About the file</h2>
              <dl className="meta">
                <dt>Student</dt><dd><Link href={`/students/${d.student_id}`}>{d.student_pseudonym}</Link></dd>
                <dt>Course</dt><dd><Link href={`/courses/${d.course_id}`}>{d.course_code}</Link></dd>
                <dt>Assignment</dt><dd>{d.assignment_title ?? "None"}</dd>
                <dt>File</dt><dd>{d.original_filename}, {d.file_type.toUpperCase()}, {fmtBytes(d.size_bytes)}</dd>
                <dt>Words</dt><dd>{d.word_count?.toLocaleString() ?? "-"}</dd>
                <dt>Uploaded</dt><dd>{fmtDate(d.uploaded_at)} by {d.uploaded_by_email}</dd>
                <dt>Read</dt><dd>{fmtDate(d.processed_at)} {d.attempts > 1 && `(${d.attempts} attempts)`}</dd>
                <dt>SHA-256</dt><dd className="mono">{d.sha256}</dd>
              </dl>
              {d.status === "completed" && (
                <QualityReviewPanel id={d.id} review={d.quality_review} canManage={d.access === "manage"}
                  onDone={() => {
                    doc.reload();
                    setReportKey((k) => k + 1);
                  }}
                />
              )}
              {d.can_remove && <RemoveDocument id={d.id} role={d.role} onDone={doc.reload} />}
            </section>

            <section className="card">
              <h2>What the file itself shows</h2>
              {!d.signals && <p className="muted">Available once the file has been read.</p>}
              {d.signals && (
                <>
                  {d.signals.hidden_text_spans.length > 0 ? (
                    <p className="note">
                      Hidden content found:{" "}
                      {d.signals.hidden_text_spans
                        .map((h) => (h.chars ? `${h.chars} characters of ${hiddenReason[h.reason] ?? h.reason}` : `${hiddenReason[h.reason] ?? h.reason} on ${plural(h.pages ?? 0, "page")}`))
                        .join("; ")}
                      . It was left out of the text.
                    </p>
                  ) : (
                    <p className="muted">No hidden text found.</p>
                  )}
                  {fastEdit && (
                    <p className="note">
                      The file records {editMins} minutes of editing for {d.word_count} words. Text pasted in from
                      elsewhere often shows a short editing time, but so does a file re-saved from another editor.
                    </p>
                  )}
                  <dl className="meta">
                    {Object.entries(d.signals.metadata).map(([k, v]) => (
                      <Fragment key={k}><dt>{metaLabel[k] ?? k}</dt><dd>{String(v)}</dd></Fragment>
                    ))}
                  </dl>
                </>
              )}
            </section>
          </div>

          {d.status === "completed" && (
            <section className="card">
              <div className="card-head">
                <h2>Text as Viskopic read it</h2>
                {textOpen
                  ? <button className="btn secondary" onClick={() => setTextOpen(false)}>Hide the text</button>
                  : <button className="btn secondary" onClick={showText}>Show the text</button>}
              </div>
              {textOpen && text !== null && <div className="text-view">{text}</div>}
              <p className="hint section-gap">Opening the text is recorded in the activity log.</p>
            </section>
          )}
        </>
      )}
      </div>
    </>
  );
}

const languageLabel: Record<string, string> = {
  english: "English prose",
  non_english: "Not English",
  mixed: "Mixed languages",
  uncertain: "Unsure",
};

/**
 * The consistency model scores only texts whose language and extraction a reviewer has checked.
 * The server binds each review to the exact stored text; a new review replaces the previous one.
 */
function QualityReviewPanel({ id, review, canManage, onDone }: {
  id: string;
  review: QualityReview | null;
  canManage: boolean;
  onDone: () => void;
}) {
  const { api } = useAuth();
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const [saved, setSaved] = useState(false);
  return (
    <div className="quality-review">
      <h3>Was the text read correctly?</h3>
      {saved && (
        <p className="note info small" role="status">
          Saved. The checks are running again.
        </p>
      )}
      {review ? (
        <p className={review.current ? "small" : "note"}>
          {languageLabel[review.language] ?? review.language}, extraction {review.extraction === "pass" ? "clean" : "failed"}.
          Checked {fmtDate(review.reviewed_at)} by {review.reviewed_by_email}.
          {!review.current && " The text has changed since: check it again."}
        </p>
      ) : (
        <p className="muted small">Not checked yet. The writing style check won&apos;t use this text until a person checks it.</p>
      )}
      {canManage && (
        <details ref={detailsRef} onToggle={(e) => e.currentTarget.open && setSaved(false)}>
          <summary className="link">{review ? "Check it again" : "Check the text"}</summary>
          <p className="muted small">
            Open the extracted text below and check that it is English prose and that extraction kept it intact
            (no garbled characters, missing pages, or tables and reference lists run into the prose).
          </p>
          <ActionForm
            inline
            submitLabel="Save"
            onSubmit={async (f) => {
              await api(`/documents/${id}/quality-review`, {
                method: "POST",
                body: JSON.stringify({ language: f.get("language"), extraction: f.get("extraction") }),
              });
              if (detailsRef.current) detailsRef.current.open = false;
              setSaved(true);
              onDone();
            }}
          >
            <Field label="Language">
              <select name="language" required defaultValue="english">
                {Object.entries(languageLabel).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label="Extraction">
              <select name="extraction" required defaultValue="pass">
                <option value="pass">Clean</option>
                <option value="fail">Failed</option>
              </select>
            </Field>
          </ActionForm>
        </details>
      )}
    </div>
  );
}

/** Institution owners only: permanently remove a wrongly uploaded document, with a reason. */
function RemoveDocument({ id, role, onDone }: { id: string; role: string; onDone: () => void }) {
  const { api } = useAuth();
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <p className="section-gap">
        <button className="btn danger" onClick={() => setOpen(true)}>Remove this file</button>
      </p>
    );
  }
  return (
    <div className="remove-box">
      <h3>Remove this document</h3>
      <p className="muted small">
        For wrong uploads only. The file and its text are deleted permanently.
        {role === "baseline"
          ? " The student's profile is rebuilt without it, and their submissions are analysed again."
          : " Its case is withdrawn."}
      </p>
      <ActionForm
        submitLabel="Remove permanently"
        onSubmit={async (f) => {
          await api(`/documents/${id}/remove`, { method: "POST", body: JSON.stringify({ reason: f.get("reason") }) });
          onDone();
        }}
      >
        <Field label="Reason" hint="For example: uploaded for the wrong student">
          <textarea name="reason" required minLength={5} maxLength={500} rows={2} />
        </Field>
      </ActionForm>
      <button className="link" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}
