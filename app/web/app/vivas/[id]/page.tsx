"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { qtypeLabel } from "@/components/VivaPanel";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import { plural } from "@/lib/words";
import type { FollowupDecision, VivaAssessment, VivaReport, VivaReportQuestion, VivaSignals, VivaTimeline } from "@/lib/types";

const ratingLabel = { consistent: "Consistent with authorship", unclear: "Unclear", concern: "Concern" } as const;
const sourceText: Record<string, string> = {
  visq: "Written by Visq, approved by reviewer",
  visq_followup: "Follow-up asked by Visq, not approved in advance",
  visq2: "Written by Visq 2 during the viva, not approved in advance",
  ai_draft: "Drafted by AI, approved by reviewer",
  template: "Template",
  reviewer: "Written by reviewer",
};
const decisionReason: Record<FollowupDecision["reason"], string> = {
  model: "",
  no_answer: "no answer to follow up",
  timeout: "the decision was not ready in time, so the viva moved on",
  error: "Visq or transcription failed, so the viva moved on",
  disabled: "follow-ups were off",
};
const modeLabel = {
  oral_recorded: "Spoken answers, recorded, no staff present",
  oral_video: "Spoken answers with webcam video, recorded, no staff present. The video was not analysed by software",
  restricted: "Typed answers, paste blocked, no staff present",
} as const;

export default function VivaReportPage() {
  const { id } = useParams<{ id: string }>();
  const rep = useResource<VivaReport>(`/viva-invitations/${id}/report`);
  const [sel, setSel] = useState<string>("");
  // Transcripts arrive a little after submission; refresh until none is pending.
  const pending = rep.data?.questions.some((q) => q.transcript_job === "queued" || q.transcript_job === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(rep.reload, 4000);
    return () => clearInterval(t);
  }, [pending, rep.reload]);

  useEffect(() => {
    if (!rep.data || sel) return;
    const first = rep.data.questions.find((q) => !q.assessment) ?? rep.data.questions[0];
    setSel(first?.id ?? "overall");
  }, [rep.data, sel]);

  if (rep.error) return <ErrorNote error={rep.error} />;
  if (!rep.data) return <p className="muted">Loading…</p>;
  const r = rep.data;
  const inv = r.invitation;
  const submitted = inv.status === "submitted";
  const qs = r.questions;
  const rated = qs.filter((q) => q.assessment).length;
  const idx = qs.findIndex((q) => q.id === sel);
  const q = idx >= 0 ? qs[idx] : null;

  function next() {
    const after = qs.slice(idx + 1).find((x) => !x.assessment) ?? qs.find((x, k) => k !== idx && !x.assessment);
    setSel(after?.id ?? "overall");
  }

  return (
    <>
      <Link href={`/documents/${inv.document_id}?tab=viva`} className="back">← Back to the case</Link>
      <PageHeader
        title={`${inv.viva_kind === "style" ? "Style" : "Reasoning"} viva`}
        meta={submitted ? `Finished ${fmtDate(inv.submitted_at)} · ${rated} of ${qs.length} answers rated` : `Planned ${fmtDate(inv.created_at)}`}
      >
        <Status value={inv.status} />
      </PageHeader>

      <p className="note info">
        {inv.is_test && "Test viva. "}
        {modeLabel[inv.supervision_mode]}. The notes under each answer are hints reported by the student&apos;s
        browser and can be missing. Rate each answer on what you read.
      </p>

      <div className="split">
        <aside className="side-col sticky stack-sm" style={{ flexBasis: 300, flexGrow: 0 }}>
          {qs.length > 0 && (
            <nav className="qnav" aria-label="Answers">
              {qs.map((x) => (
                <button key={x.id} className={x.id === sel ? "on" : ""} onClick={() => setSel(x.id)} aria-current={x.id === sel ? "true" : undefined}>
                  <b className={x.assessment ? "" : "open"}>{x.position}</b>
                  <span>{x.parent_id ? "Follow-up" : qtypeLabel[x.qtype ?? ""] ?? "Question"}
                    {x.text && <span className="qnav-gist">{gist(x.text)}</span>}
                    <small>{x.assessment ? ratingLabel[x.assessment.rating] : x.answer_kind === null ? "Not answered" : x.answer_kind === "skipped" ? "No answer" : "Not rated"}</small>
                  </span>
                </button>
              ))}
              {submitted && (
                <button className={sel === "overall" ? "on" : ""} onClick={() => setSel("overall")}>
                  <b className={r.overall ? "" : "open"}>✓</b>
                  <span>Overall<small>{r.overall ? ratingLabel[r.overall.rating] : "Not rated"}</small></span>
                </button>
              )}
            </nav>
          )}
          <details className="card" style={{ marginBottom: 0 }}>
            <summary>About this viva</summary>
            <dl className="meta" style={{ marginTop: 12 }}>
              <dt>Opened</dt><dd>{fmtDate(inv.opened_at)}</dd>
              <dt>Started</dt><dd>{fmtDate(inv.started_at)}</dd>
              <dt>Finished</dt><dd>{fmtDate(inv.submitted_at)}</dd>
              <dt>Full screen</dt><dd>{inv.fullscreen_required ? "Required" : "Not required"}</dd>
              <dt>Time per question</dt><dd>{inv.prep_seconds}s to think, {inv.answer_seconds}s to answer</dd>
              <dt>Questions by</dt><dd>{inv.question_engine ?? "Reviewer"}</dd>
              {inv.flow === "visq2" && (
                <><dt>Visq 2 set</dt><dd>{inv.visq2_full ? "Full (7 viva questions)" : "Short (3 viva questions)"}, what-if {Math.round((inv.extension_seconds ?? 0) / 60)} min</dd>
                  <dt>Previewed</dt><dd>{r.visq2?.previewed ? "Yes" : "No"}</dd></>
              )}
              {r.visq2?.status === "failed" && (<><dt>Visq</dt><dd className="error">Could not read the essay: {r.visq2.error}</dd></>)}
              <dt>Opened on another device</dt><dd>{r.session.second_sessions === 0 ? "No" : plural(r.session.second_sessions, "time")}</dd>
              {inv.revoke_reason && (<><dt>Withdrawn</dt><dd>{fmtDate(inv.revoked_at)}: {inv.revoke_reason}</dd></>)}
            </dl>
          </details>
        </aside>

        <div className="main-col">
          {!submitted && <p className="muted">The answers appear here once the student finishes.</p>}
          {qs.length === 0 && inv.flow === "visq2" && (
            <p className="muted">Visq writes the questions as the student goes; they appear here once asked.</p>
          )}
          {q && <QuestionReport key={q.id} q={q} />}
          {q && submitted && (
            <section className="card">
              <Assessment key={q.id} invitationId={id} questionId={q.id} current={q.assessment} canAssess={r.can_manage}
                onSaved={() => { rep.reload(); next(); }} />
            </section>
          )}
          {sel === "overall" && submitted && (
            <>
              {r.visq2?.note && (
                <section className="card">
                  <h2>Visq&apos;s reading note</h2>
                  <p className="muted small">
                    A reading aid: it compares the wording of the answers with the essay&apos;s own structure.
                    &quot;Not evident&quot; is not &quot;wrong&quot;, and nothing in it is a finding about the student.
                  </p>
                  <div className="text-view viva-note">{r.visq2.note}</div>
                </section>
              )}
              <section className="card">
                <h2>Your overall view</h2>
                <Assessment key="overall" invitationId={id} questionId={null} current={r.overall} canAssess={r.can_manage} onSaved={rep.reload} />
              </section>
            </>
          )}
          {submitted && (
            <p className="hint">
              A rating records your view of the viva. It is not a decision on the case.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function QuestionReport({ q }: { q: VivaReportQuestion }) {
  return (
    <section className="card viva-report-q">
      <div className="card-head">
        <h2>
          {q.source === "visq2" ? `${q.position}. ${qtypeLabel[q.qtype ?? ""] ?? "Question"}`
            : q.parent_id ? `Follow-up to question ${q.position}` : `Question ${q.position}`}
        </h2>
        <span className="muted small">
          {sourceText[q.source] ?? q.source}
          {q.qtype && !q.parent_id && q.source !== "visq2" && ` · ${qtypeLabel[q.qtype] ?? q.qtype}`}
        </span>
      </div>
      {q.passage && <blockquote className="viva-passage"><span className="label">Passage shown</span><p>{q.passage}</p></blockquote>}
      <div className="report-q">
        <span className="label">Question asked</span>
        <p>{q.text}</p>
      </div>
      {q.reviewer_note && (
        <p className="viva-reviewer-note small"><strong>What a good answer covers</strong> (not shown to the student): {q.reviewer_note}</p>
      )}

      {q.answer_kind === null && <p className="muted">Not answered yet.</p>}
      {q.answer_kind === "skipped" && <p className="note">No answer: the time ran out.</p>}
      {q.answer_kind === "typed" && (
        <div className="report-answer">
          <span className="label">The student’s answer</span>
          <div className="report-answer-text">{q.typed_answer}</div>
        </div>
      )}
      {q.answer_kind === "spoken" && q.recording && (
        <>
          <MediaPlayer recordingId={q.recording.id} status={q.recording.status} video={q.recording.has_video} />
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
      {q.followup_decision && <Decision d={q.followup_decision} />}
    </section>
  );
}

function Decision({ d }: { d: FollowupDecision }) {
  return (
    <p className="muted small">
      {d.action === "follow_up" ? "Visq asked a follow-up" : "No follow-up"}
      {d.reason !== "model" && `: ${decisionReason[d.reason]}`}
      {d.why && `. Visq's reason: ${d.why}`}
    </p>
  );
}

function MediaPlayer({ recordingId, status, video }: { recordingId: string; status: string; video: boolean }) {
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
        }}>{video ? "Load video" : "Load recording"}</button>{" "}
        <span className="muted small">Playing a recording is recorded in the audit log.</span>
        <ErrorNote error={error} />
      </p>
    );
  }
  return (
    <div className="viva-audio">
      {video ? (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video className="viva-video" controls src={url} autoPlay playsInline
          ref={(el) => { if (el) el.playbackRate = rate; }} />
      ) : (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <audio controls src={url} autoPlay
          ref={(el) => { if (el) el.playbackRate = rate; }} />
      )}
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
  items.push(s.focus_lost ? `Left the page ${plural(s.focus_lost, "time")}, ${s.away_seconds}s in total` : "Stayed on the page");
  if (s.paste_blocked) items.push(`${s.paste_blocked} paste attempt(s) blocked`);
  if (s.copy_blocked) items.push(`${s.copy_blocked} copy attempt(s) blocked`);
  if (s.fullscreen_exit) items.push(`Left full screen ${plural(s.fullscreen_exit, "time")}${s.fullscreen_out_seconds ? `, ${s.fullscreen_out_seconds}s in total` : ""}`);
  if (s.camera_unavailable) items.push("No camera video for this answer (camera off or unavailable)");
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
  const [rating, setRating] = useState<string>(current?.rating ?? "");
  const [note, setNote] = useState("");
  return (
    <div className="stack-sm">
      {current && (
        <p className="small" style={{ margin: 0 }}>
          Rated <strong>{ratingLabel[current.rating]}</strong>{current.note && `: ${current.note}`}
          <span className="muted"> ({current.created_by_email}, {fmtDate(current.created_at)})</span>
        </p>
      )}
      {canAssess && (
        <form className="stack-sm" onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await api(`/viva-invitations/${invitationId}/assessments`, {
              method: "POST",
              body: JSON.stringify({ question_id: questionId, rating, note: note || null }),
            });
            setNote("");
            onSaved();
          } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
        }}>
          <fieldset className="options">
            <legend>{current ? "Change the rating" : questionId ? "How does this answer read?" : "Your view of the whole viva"}</legend>
            {Object.entries(ratingLabel).map(([v, l]) => (
              <label key={v} className="option">
                <input type="radio" name="rating" value={v} required checked={rating === v} onChange={() => setRating(v)} />
                <span>{l}</span>
              </label>
            ))}
          </fieldset>
          <Field label="Note (optional)">
            <textarea rows={2} maxLength={4000} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div><button className="btn" disabled={busy || !rating}>{busy ? "Saving…" : questionId ? "Save and go to the next answer" : "Save"}</button></div>
          <ErrorNote error={error} />
        </form>
      )}
    </div>
  );
}

/** The first few words of a question, so questions of the same type can be told apart in the list. */
function gist(text: string, words = 7): string {
  const w = text.trim().split(/\s+/);
  return w.length <= words ? w.join(" ") : `${w.slice(0, words).join(" ")}…`;
}
