"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { VivaAssessment, VivaReport, VivaReportQuestion, VivaSignals, VivaTimeline } from "@/lib/types";

const ratingLabel = { consistent: "Consistent with authorship", unclear: "Unclear", concern: "Concern" } as const;
const modeLabel = {
  oral_recorded: "Spoken answers, recorded, no staff present",
  restricted: "Typed answers, paste blocked, no staff present",
} as const;

export default function VivaReportPage() {
  const { id } = useParams<{ id: string }>();
  const rep = useResource<VivaReport>(`/viva-invitations/${id}/report`);
  // Transcripts arrive a little after submission; refresh until none is pending.
  const pending = rep.data?.questions.some((q) => q.transcript_job === "queued" || q.transcript_job === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(rep.reload, 4000);
    return () => clearInterval(t);
  }, [pending, rep.reload]);

  if (rep.error) return <ErrorNote error={rep.error} />;
  if (!rep.data) return <p className="muted">Loading…</p>;
  const r = rep.data;
  const inv = r.invitation;
  const submitted = inv.status === "submitted";

  return (
    <>
      <PageHeader
        title={`${inv.viva_kind === "style" ? "Style" : "Reasoning"} viva`}
        meta={<><Link href={`/documents/${inv.document_id}`}>Back to the submission</Link> · Sent {fmtDate(inv.created_at)}</>}
      >
        <Status value={inv.status} />
      </PageHeader>

      <p className="note">
        {inv.is_test && "Test viva. "}
        {modeLabel[inv.supervision_mode]}. Signals below are hints, not evidence: browser signals are reported by the
        student&apos;s browser and can be missing. Rate each answer on what you hear or read.
      </p>

      <section className="card">
        <h2>Session</h2>
        <dl className="meta">
          <dt>Opened</dt><dd>{fmtDate(inv.opened_at)}</dd>
          <dt>Started</dt><dd>{fmtDate(inv.started_at)}</dd>
          <dt>Submitted</dt><dd>{fmtDate(inv.submitted_at)}</dd>
          <dt>Time per question</dt><dd>{inv.prep_seconds}s to think, {inv.answer_seconds}s to answer</dd>
          <dt>Switched device or window</dt><dd>{r.session.second_sessions === 0 ? "No" : `${r.session.second_sessions} time(s)`}</dd>
          {inv.revoke_reason && (<><dt>Withdrawn</dt><dd>{fmtDate(inv.revoked_at)}: {inv.revoke_reason}</dd></>)}
        </dl>
        {!submitted && <p className="muted small">The full report appears once the student submits.</p>}
      </section>

      {r.questions.map((q) => (
        <QuestionReport key={q.id} q={q} canAssess={r.can_manage && submitted} invitationId={id} onSaved={rep.reload} />
      ))}

      {submitted && (
        <section className="card">
          <h2>Overall</h2>
          <Assessment invitationId={id} questionId={null} current={r.overall} canAssess={r.can_manage} onSaved={rep.reload} />
          <p className="muted small section-gap">
            A rating records your view of the viva. It is not a decision on the case; decisions and appeals come later.
          </p>
        </section>
      )}
    </>
  );
}

function QuestionReport({ q, canAssess, invitationId, onSaved }: {
  q: VivaReportQuestion; canAssess: boolean; invitationId: string; onSaved: () => void;
}) {
  return (
    <section className="card viva-report-q">
      <div className="card-head">
        <h2>Question {q.position}</h2>
        <span className="muted small">{q.source === "ai_draft" ? "Drafted by AI, approved by reviewer" : q.source === "template" ? "Template" : "Written by reviewer"}</span>
      </div>
      {q.passage && <blockquote className="viva-passage"><span className="label">Passage shown</span><p>{q.passage}</p></blockquote>}
      <p className="viva-q-text">{q.text}</p>

      {q.answer_kind === null && <p className="muted">Not answered yet.</p>}
      {q.answer_kind === "skipped" && <p className="note">No answer: the time ran out.</p>}
      {q.answer_kind === "typed" && <div className="text-view">{q.typed_answer}</div>}
      {q.answer_kind === "spoken" && q.recording && (
        <>
          <AudioPlayer recordingId={q.recording.id} status={q.recording.status} />
          {q.transcript ? (
            <>
              <Timeline t={q.transcript.timeline} />
              <h3>Automatic transcript</h3>
              <div className="text-view">{q.transcript.text || <span className="muted">(no speech detected)</span>}</div>
              <p className="muted small">Transcribed by {q.transcript.provider} ({q.transcript.model}). May contain errors; the recording is the record.</p>
            </>
          ) : (
            <p className="muted small">
              {q.transcript_job === "failed" ? "Transcription failed. Listen to the recording." :
                q.transcript_job ? "Transcribing…" : "No transcript (external AI is off). Listen to the recording."}
            </p>
          )}
        </>
      )}

      {q.answer_kind && <Signals s={q.signals} seconds={q.seconds_taken} />}
      {canAssess && <Assessment invitationId={invitationId} questionId={q.id} current={q.assessment} canAssess onSaved={onSaved} />}
      {!canAssess && q.assessment && <p className="small">Rated: <strong>{ratingLabel[q.assessment.rating]}</strong>{q.assessment.note && `: ${q.assessment.note}`}</p>}
    </section>
  );
}

function AudioPlayer({ recordingId, status }: { recordingId: string; status: string }) {
  const { apiBlob } = useAuth();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rate, setRate] = useState(1);

  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  if (status !== "stored") return <p className="muted small">The recording was deleted under the retention policy.</p>;
  if (!url) {
    return (
      <p>
        <button className="btn secondary" onClick={async () => {
          try { setUrl(URL.createObjectURL(await apiBlob(`/viva-recordings/${recordingId}/audio`))); }
          catch (e) { setError((e as Error).message); }
        }}>Load recording</button>{" "}
        <span className="muted small">Playing a recording is recorded in the audit log.</span>
        <ErrorNote error={error} />
      </p>
    );
  }
  return (
    <div className="viva-audio">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio controls src={url} autoPlay
        ref={(el) => { if (el) el.playbackRate = rate; }} />
      <label className="small">
        Speed{" "}
        <select value={rate} onChange={(e) => setRate(Number(e.target.value))}>
          {[1, 1.25, 1.5, 2].map((r) => <option key={r} value={r}>{r}x</option>)}
        </select>
      </label>
    </div>
  );
}

/** Speech and silence across the recording, from the transcription's timestamps. A neutral picture, not a score. */
function Timeline({ t }: { t: VivaTimeline }) {
  const total = Math.max(t.duration_ms, 1);
  const s = (ms: number) => `${(ms / 1000).toFixed(1)}s`;
  return (
    <figure className="viva-timeline">
      <svg viewBox="0 0 1000 28" preserveAspectRatio="none" role="img"
        aria-label={`Speech timeline: first speech after ${t.first_speech_ms === null ? "none" : s(t.first_speech_ms)}, longest pause ${s(t.longest_pause_ms)}`}>
        <rect x="0" y="8" width="1000" height="12" rx="3" className="viva-tl-silence" />
        {t.segments.map(([a, b], i) => (
          <rect key={i} x={(a / total) * 1000} y="4" width={Math.max(2, ((b - a) / total) * 1000)} height="20" rx="2" className="viva-tl-speech" />
        ))}
      </svg>
      <figcaption className="small muted">
        Speaking (dark) and silence (light) over {s(t.duration_ms)}. First speech after{" "}
        {t.first_speech_ms === null ? "no speech detected" : s(t.first_speech_ms)}; longest pause {s(t.longest_pause_ms)};
        {" "}{t.pauses_over_3s} pause{t.pauses_over_3s === 1 ? "" : "s"} of 3s or more. Long pauses also happen when people think.
      </figcaption>
    </figure>
  );
}

function Signals({ s, seconds }: { s: VivaSignals; seconds: number | null }) {
  const items: string[] = [];
  if (seconds !== null) items.push(`Answered ${seconds}s after the question appeared`);
  items.push(s.focus_lost ? `Left the page ${s.focus_lost} time(s), ${s.away_seconds}s in total` : "Stayed on the page");
  if (s.paste_blocked) items.push(`${s.paste_blocked} paste attempt(s) blocked`);
  if (s.fullscreen_exit) items.push(`Left full screen ${s.fullscreen_exit} time(s)`);
  if (s.late_write) items.push(`${s.late_write} answer(s) arrived after the time limit and were refused`);
  return (
    <ul className="chips section-gap" aria-label="Signals for this question">
      {items.map((i) => <li key={i} className="chip">{i}</li>)}
    </ul>
  );
}

function Assessment({ invitationId, questionId, current, canAssess, onSaved }: {
  invitationId: string; questionId: string | null; current: VivaAssessment | null; canAssess: boolean; onSaved: () => void;
}) {
  const { api } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="viva-assess">
      {current && (
        <p className="small">
          Current rating: <strong>{ratingLabel[current.rating]}</strong>{current.note && `: ${current.note}`}
          <span className="muted"> ({current.created_by_email}, {fmtDate(current.created_at)})</span>
        </p>
      )}
      {canAssess && (
        <form className="form inline" onSubmit={async (e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          setBusy(true);
          setError(null);
          try {
            await api(`/viva-invitations/${invitationId}/assessments`, {
              method: "POST",
              body: JSON.stringify({ question_id: questionId, rating: f.get("rating"), note: f.get("note") || null }),
            });
            onSaved();
          } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
        }}>
          <Field label={current ? "New rating" : "Rating"}>
            <select name="rating" required defaultValue={current?.rating ?? ""}>
              <option value="" disabled>Choose…</option>
              {Object.entries(ratingLabel).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Note (optional)"><input name="note" maxLength={4000} /></Field>
          <button className="btn" disabled={busy}>{busy ? "Saving…" : "Save rating"}</button>
          <ErrorNote error={error} />
        </form>
      )}
    </div>
  );
}
