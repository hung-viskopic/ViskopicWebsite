"use client";

import Link from "next/link";
import { useState } from "react";
import { ErrorNote, Field, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { DraftQuestion, VivaList } from "@/lib/types";

const kindLabel = { style: "Style viva", reasoning: "Reasoning viva" } as const;
const sourceLabel: Record<string, string> = { ai_draft: "AI draft", template: "Template", reviewer: "Written by reviewer" };
/** What each kind of question is, in the words a reviewer would use (Visq 2 steps, and older Visq 1 types). */
export const qtypeLabel: Record<string, string> = {
  clarify: "Clarifying question", extension: "Extension (what-if)", viva: "Viva question",
  tradeoff: "Trade-off", limit: "What if a reason failed", case: "New case", followup: "Follow-up",
};

/** Viva section on a submission: list of invitations and the form to send a new one. */
export function VivaPanel({ documentId }: { documentId: string }) {
  const vivas = useResource<VivaList>(`/documents/${documentId}/vivas`, 5000);
  const [open, setOpen] = useState(false);
  const [lastLink, setLastLink] = useState<string | null>(null);
  const v = vivas.data;

  return (
    <section className="card">
      <div className="card-head">
        <h2>Viva</h2>
        {v?.can_manage && v.case && v.case.status !== "withdrawn" && !open && (
          <button className="btn" onClick={() => { setOpen(true); setLastLink(null); }}>Send viva invitation</button>
        )}
      </div>
      <ErrorNote error={vivas.error} />
      {v && !v.case && <p className="muted">A viva can be sent once the submission has been processed.</p>}
      {lastLink && (
        <p className="note info small" role="status">
          Invitation queued. The email goes to the student shortly. Test link (for trying the viva yourself):{" "}
          <a href={lastLink} target="_blank" rel="noreferrer">open the student page</a>
        </p>
      )}
      {open && v?.case && (
        <InvitationForm caseId={v.case.id} aiEnabled={v.ai_enabled} visqEnabled={v.visq_enabled}
          onCancel={() => setOpen(false)}
          onSent={(link) => { setOpen(false); setLastLink(link); vivas.reload(); }} />
      )}
      {v && v.invitations.length > 0 && (
        <div className="table-wrap section-gap">
          <table>
            <thead>
              <tr><th>Sent</th><th>Type</th><th>Answers</th><th>Status</th><th>Progress</th><th /></tr>
            </thead>
            <tbody>
              {v.invitations.map((i) => (
                <tr key={i.id}>
                  <td>{fmtDate(i.created_at)}</td>
                  <td>{kindLabel[i.viva_kind]}</td>
                  <td>
                    <span className="cap">{i.answer_format}</span>{i.camera_required && " with camera"}
                    {i.flow === "visq2" && <div className="muted small">Visq 2</div>}
                  </td>
                  <td><Status value={i.status} />{i.last_error && <div className="muted small">{i.last_error}</div>}</td>
                  <td>{i.answered ?? 0} of {i.questions ?? 0} answered</td>
                  <td>
                    <Link href={`/vivas/${i.id}`}>{i.status === "submitted" ? "Open report" : "Details"}</Link>
                    {v.can_manage && ["queued", "sent", "opened", "failed", "expired"].includes(i.status) && (
                      <InvitationActions id={i.id} status={i.status} onDone={(link) => { if (link) setLastLink(link); vivas.reload(); }} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v && v.case && v.invitations.length === 0 && !open && <p className="muted">No viva has been sent for this submission.</p>}
    </section>
  );
}

function InvitationActions({ id, status, onDone }: { id: string; status: string; onDone: (link?: string) => void }) {
  const { api } = useAuth();
  const [error, setError] = useState<string | null>(null);
  async function resend() {
    try {
      const r = await api<{ test_link: string }>(`/viva-invitations/${id}/resend`, { method: "POST" });
      onDone(r.test_link);
    } catch (e) { setError((e as Error).message); }
  }
  async function revoke() {
    const reason = window.prompt("Why are you withdrawing this invitation?");
    if (!reason) return;
    try {
      await api(`/viva-invitations/${id}/revoke`, { method: "POST", body: JSON.stringify({ reason }) });
      onDone();
    } catch (e) { setError((e as Error).message); }
  }
  return (
    <span className="viva-actions">
      <button className="link" onClick={resend}>New link</button>
      {status !== "failed" && status !== "expired" && <button className="link" onClick={revoke}>Withdraw</button>}
      <ErrorNote error={error} />
    </span>
  );
}

function InvitationForm({ caseId, visqEnabled, onCancel, onSent }: {
  caseId: string;
  /** Transcription of spoken answers (not used by Visq 2, which is typed). */
  aiEnabled: boolean;
  /** Visq 2 world-tree vivas. */
  visqEnabled: boolean;
  onCancel: () => void;
  onSent: (link: string) => void;
}) {
  const { api } = useAuth();
  const [flow, setFlow] = useState<"visq2" | "standard">(visqEnabled ? "visq2" : "standard");
  const [kind, setKind] = useState<"style" | "reasoning">("reasoning");
  const [format, setFormat] = useState<"spoken" | "typed">("spoken");
  const [camera, setCamera] = useState(true);
  const [full, setFull] = useState(false);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visq2 = flow === "visq2";

  async function draft() {
    setBusy("draft");
    setError(null);
    try {
      const r = await api<{ provider: string; questions: DraftQuestion[] }>(`/cases/${caseId}/viva-drafts`, {
        method: "POST", body: JSON.stringify({ kind, count: 4 }),
      });
      setQuestions(r.questions);
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  function update(i: number, patch: Partial<DraftQuestion>) {
    setQuestions((qs) => qs.map((q, j) => (j === i ? { ...q, ...patch } : q)));
  }
  function move(i: number, d: -1 | 1) {
    setQuestions((qs) => {
      const next = [...qs];
      const [q] = next.splice(i, 1);
      next.splice(i + d, 0, q);
      return next;
    });
  }

  async function send(form: FormData) {
    setBusy("send");
    setError(null);
    try {
      const r = await api<{ test_link: string }>(`/cases/${caseId}/viva-invitations`, {
        method: "POST",
        body: JSON.stringify({
          kind, flow, email: form.get("email"),
          answer_format: visq2 ? "typed" : format,
          camera: !visq2 && format === "spoken" && camera,
          deadline_days: Number(form.get("deadline_days")),
          prep_seconds: Number(form.get("prep_seconds")),
          answer_seconds: Number(form.get("answer_seconds")),
          ...(visq2
            ? { visq2_full: full, extension_minutes: Number(form.get("extension_minutes")) }
            : { questions: questions.map((q) => ({ text: q.text, passage: q.passage || null, source: q.source })) }),
        }),
      });
      onSent(r.test_link);
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  return (
    <form className="viva-form" onSubmit={(e) => { e.preventDefault(); send(new FormData(e.currentTarget)); }}>
      <div className="form inline">
        {visqEnabled && (
          <Field label="Questions" hint={visq2 ? "Written by Visq during the viva" : "Approved by you before sending"}>
            <select value={flow} onChange={(e) => setFlow(e.target.value as "visq2" | "standard")}>
              <option value="visq2">Visq 2: written from the essay during the viva</option>
              <option value="standard">My own questions</option>
            </select>
          </Field>
        )}
        <Field label="Viva type">
          <select value={kind} onChange={(e) => setKind(e.target.value as "style" | "reasoning")}>
            <option value="reasoning">Reasoning: the argument and evidence</option>
            <option value="style">Style: how it was written</option>
          </select>
        </Field>
        {!visq2 && (
          <Field label="Answers" hint={format === "spoken" ? "Recorded in the browser" : "Paste is blocked"}>
            <select value={format} onChange={(e) => setFormat(e.target.value as "spoken" | "typed")}>
              <option value="spoken">Spoken (recommended)</option>
              <option value="typed">Typed (accessibility alternative)</option>
            </select>
          </Field>
        )}
        {!visq2 && format === "spoken" && (
          <label className="viva-camera-option">
            <input type="checkbox" checked={camera} onChange={(e) => setCamera(e.target.checked)} />
            <span>
              Record the webcam too
              <small>Video for you to watch. Nothing analyses it. Untick for students who can&apos;t use a camera.</small>
            </span>
          </label>
        )}
      </div>

      {visq2 ? (
        <div className="viva-questions">
          <h3>How a Visq 2 viva runs</h3>
          <ol className="small viva-visq2-steps">
            <li>Visq reads the essay as a tree of points and what it takes for granted.</li>
            <li>Clarifying questions, only where the essay is silent about something it relies on.</li>
            <li>A timed, typed <strong>extension</strong>: a what-if from outside the essay. The student says what they would keep, drop or add.</li>
            <li>A short viva: {full ? 7 : 3} questions, some with one follow-up. Each has a worked example on request.</li>
          </ol>
          <p className="note small">
            Visq writes the questions during the viva, so you don&apos;t approve them first; the report shows every
            question with its answer, plus Visq&apos;s note comparing the wording with the essay (a reading aid, not a
            score). Answers are typed, with paste blocked. The essay and answers go to an outside model. Test data only.
          </p>
          <label className="viva-camera-option">
            <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
            <span>Full set<small>7 viva questions instead of 3.</small></span>
          </label>
        </div>
      ) : (
        <div className="viva-questions">
          <div className="card-head">
            <h3>Questions</h3>
            <span>
              <button type="button" className="btn secondary" onClick={draft} disabled={busy !== null}>
                {busy === "draft" ? "Loading…" : "Use template questions"}
              </button>{" "}
              <button type="button" className="btn secondary" disabled={questions.length >= 8}
                onClick={() => setQuestions((qs) => [...qs, { text: "", passage: null, source: "reviewer" }])}>
                Add question
              </button>
            </span>
          </div>
          {!visqEnabled && <p className="muted small">Visq is off, so a viva uses your own or template questions.</p>}
          {questions.length === 0 && <p className="muted small">Use template questions or add your own (up to 8).</p>}
          <ol className="viva-question-list">
            {questions.map((q, i) => (
              <li key={i}>
                <Field label={`Question ${i + 1}`} hint={sourceLabel[q.source]}>
                  <textarea rows={2} required minLength={10} maxLength={400} value={q.text}
                    onChange={(e) => update(i, { text: e.target.value })} />
                </Field>
                <Field label="Passage from the essay (optional)" hint="Shown to the student with the question; must be copied exactly from the submission">
                  <textarea rows={2} maxLength={1000} value={q.passage ?? ""}
                    onChange={(e) => update(i, { passage: e.target.value || null })} />
                </Field>
                <span className="viva-question-tools">
                  <button type="button" className="link" disabled={i === 0} onClick={() => move(i, -1)}>Move up</button>
                  <button type="button" className="link" disabled={i === questions.length - 1} onClick={() => move(i, 1)}>Move down</button>
                  <button type="button" className="link" onClick={() => setQuestions((qs) => qs.filter((_, j) => j !== i))}>Remove</button>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="form inline">
        <Field label="Student email"><input name="email" type="email" required /></Field>
        <Field label="Deadline (days)"><input name="deadline_days" type="number" min={1} max={30} defaultValue={7} required /></Field>
        <Field label="Preparation time (seconds)" hint="Per question, before answering">
          <input name="prep_seconds" type="number" min={0} max={300} defaultValue={visq2 ? 0 : 30} required />
        </Field>
        <Field label="Answer time (seconds)" hint="Per question, enforced by the server">
          <input name="answer_seconds" type="number" min={30} max={600} defaultValue={visq2 ? 180 : 120} required />
        </Field>
        {visq2 && (
          <Field label="Extension time (minutes)" hint="For the typed what-if">
            <input name="extension_minutes" type="number" min={1} max={30} defaultValue={6} required />
          </Field>
        )}
      </div>
      <p className="muted small">
        The email names your institution and the deadline only: no score, no mention of AI, no assignment title.
      </p>
      <ErrorNote error={error} />
      <div className="actions">
        <button className="btn" disabled={busy !== null || (!visq2 && questions.length === 0)}>{busy === "send" ? "Sending…" : "Send invitation"}</button>
        <button type="button" className="link" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
