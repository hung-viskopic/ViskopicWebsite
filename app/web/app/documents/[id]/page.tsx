"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useRef, useState } from "react";
import { EvidenceReport } from "@/components/EvidenceReport";
import { ActionForm, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtBytes, fmtDate, useResource } from "@/lib/hooks";
import type { DocumentRow, QualityReview } from "@/lib/types";

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

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const { api } = useAuth();
  const doc = useResource<Detail>(`/documents/${id}`, 3000);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a quality review so the evidence report follows the re-analysis it triggers.
  const [reportKey, setReportKey] = useState(0);

  if (doc.error) return <ErrorNote error={doc.error} />;
  if (!doc.data) return <p className="muted">Loading…</p>;
  const d = doc.data;
  const editMins = Number(d.signals?.metadata.total_edit_minutes);
  const fastEdit = d.word_count && editMins >= 0 && !Number.isNaN(editMins) && editMins < d.word_count / 100;

  async function showText() {
    try {
      setText((await api<{ text: string }>(`/documents/${id}/text`)).text);
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

  return (
    <>
      <PageHeader title={d.original_filename} meta={<span className="cap">{d.role}, {d.provenance}</span>}>
        <Status value={d.status} />
        {d.status === "failed" && d.access === "manage" && <button className="btn" onClick={retry}>Retry</button>}
      </PageHeader>
      <ErrorNote error={error} />
      {d.status === "removed" && (
        <p className="note">
          Removed on {fmtDate(d.removed_at)} by {d.removed_by_email}. Reason: &ldquo;{d.removal_reason}&rdquo;. The file
          and its text were deleted. {d.role === "baseline" ? "It is no longer part of the student's baseline." : "Its case was withdrawn."}
        </p>
      )}
      {d.last_error && d.status !== "removed" && <p className="note">{d.status === "failed" ? "Failed: " : "Last attempt failed, retrying: "}{d.last_error}</p>}

      <div className="grid">
        <section className="card">
          <h2>Details</h2>
          <dl className="meta">
            <dt>Student</dt><dd><Link href={`/students/${d.student_id}`}>{d.student_pseudonym}</Link></dd>
            <dt>Course</dt><dd><Link href={`/courses/${d.course_id}`}>{d.course_code}</Link></dd>
            <dt>Assignment</dt><dd>{d.assignment_title ?? "None"}</dd>
            <dt>File</dt><dd>{d.file_type.toUpperCase()}, {fmtBytes(d.size_bytes)}</dd>
            <dt>Words</dt><dd>{d.word_count?.toLocaleString() ?? "-"}</dd>
            <dt>Uploaded</dt><dd>{fmtDate(d.uploaded_at)} by {d.uploaded_by_email}</dd>
            <dt>Processed</dt><dd>{fmtDate(d.processed_at)} {d.attempts > 1 && `(${d.attempts} attempts)`}</dd>
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
          <h2>Document signals</h2>
          {!d.signals && <p className="muted">Available once processing finishes.</p>}
          {d.signals && (
            <>
              {d.signals.hidden_text_spans.length > 0 ? (
                <p className="note">
                  Hidden content found:{" "}
                  {d.signals.hidden_text_spans
                    .map((h) => (h.chars ? `${h.chars} characters of ${hiddenReason[h.reason] ?? h.reason}` : `${hiddenReason[h.reason] ?? h.reason} on ${h.pages} page(s)`))
                    .join("; ")}
                  . It is excluded from the extracted text.
                </p>
              ) : (
                <p className="muted">No hidden text detected.</p>
              )}
              {fastEdit && (
                <p className="note">
                  The file records {editMins} minutes of editing for {d.word_count} words. Files copied from elsewhere often
                  show very short editing times.
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

      {d.role === "submission" && d.status === "completed" && (
        <EvidenceReport documentId={d.id} canManage={d.access === "manage"} refreshKey={reportKey} />
      )}

      {d.status === "completed" && (
        <section className="card">
          <div className="card-head">
            <h2>Extracted text</h2>
            {text === null && <button className="btn secondary" onClick={showText}>Show text</button>}
          </div>
          {text !== null && <div className="text-view">{text}</div>}
          <p className="muted small section-gap">Opening the text is recorded in the audit log.</p>
        </section>
      )}
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
      <h3>Quality review</h3>
      {saved && (
        <p className="note info small" role="status">
          Review saved. The evidence report below is being updated.
        </p>
      )}
      {review ? (
        <p className={review.current ? "small" : "note"}>
          {languageLabel[review.language] ?? review.language}, extraction {review.extraction === "pass" ? "clean" : "failed"}.
          Reviewed {fmtDate(review.reviewed_at)} by {review.reviewed_by_email}.
          {!review.current && " The text has changed since this review: review it again."}
        </p>
      ) : (
        <p className="muted small">Not reviewed. The consistency model won&apos;t compare this text until it is.</p>
      )}
      {canManage && (
        <details ref={detailsRef} onToggle={(e) => e.currentTarget.open && setSaved(false)}>
          <summary className="link">{review ? "Review again" : "Record a review"}</summary>
          <p className="muted small">
            Open the extracted text below and check that it is English prose and that extraction kept it intact
            (no garbled characters, missing pages, or tables and reference lists run into the prose).
          </p>
          <ActionForm
            inline
            submitLabel="Save review"
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
        <button className="btn danger" onClick={() => setOpen(true)}>Remove document</button>
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
