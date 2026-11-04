"use client";

import Link from "next/link";
import { useState } from "react";
import { ErrorNote, Field, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { DraftQuestion, VivaList } from "@/lib/types";

const kindLabel = { style: "Style viva", reasoning: "Reasoning viva" } as const;
const sourceLabel: Record<string, string> = { ai_draft: "AI draft", template: "Template", reviewer: "Written by reviewer" };

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
        <InvitationForm caseId={v.case.id} aiEnabled={v.ai_enabled}
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
                  <td><span className="cap">{i.answer_format}</span>{i.camera_required && " with camera"}</td>
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

function InvitationForm({ caseId, aiEnabled, onCancel, onSent }: {
  caseId: string;
  aiEnabled: boolean;
  onCancel: () => void;
  onSent: (link: string) => void;
}) {
  const { api } = useAuth();
  const [kind, setKind] = useState<"style" | "reasoning">("reasoning");
  const [format, setFormat] = useState<"spoken" | "typed">("spoken");
  const [camera, setCamera] = useState(true);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [provider, setProvider] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function draft() {
    setBusy("draft");
    setError(null);
    try {
      const r = await api<{ provider: string; questions: DraftQuestion[] }>(`/cases/${caseId}/viva-drafts`, {
        method: "POST", body: JSON.stringify({ kind, count: 4 }),
      });
      setQuestions(r.questions);
      setProvider(r.provider);
      if (r.questions.length === 0) setError("No usable questions came back. Try again or write your own.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  function update(i: number, patch: Partial<DraftQuestion>) {
    // An edited draft keeps its origin label; the report shows where each question started.
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
          kind, answer_format: format, camera: format === "spoken" && camera, email: form.get("email"),
          deadline_days: Number(form.get("deadline_days")),
          prep_seconds: Number(form.get("prep_seconds")),
          answer_seconds: Number(form.get("answer_seconds")),
          questions: questions.map((q) => ({ text: q.text, passage: q.passage || null, source: q.source })),
        }),
      });
      onSent(r.test_link);
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  return (
    <form className="viva-form" onSubmit={(e) => { e.preventDefault(); send(new FormData(e.currentTarget)); }}>
      <div className="form inline">
        <Field label="Viva type">
          <select value={kind} onChange={(e) => setKind(e.target.value as "style" | "reasoning")}>
            <option value="reasoning">Reasoning: the argument and evidence</option>
            <option value="style">Style: how it was written</option>
          </select>
        </Field>
        <Field label="Answers" hint={format === "spoken" ? "Recorded in the browser" : "Paste is blocked"}>
          <select value={format} onChange={(e) => setFormat(e.target.value as "spoken" | "typed")}>
            <option value="spoken">Spoken (recommended)</option>
            <option value="typed">Typed (accessibility alternative)</option>
          </select>
        </Field>
        {format === "spoken" && (
          <label className="viva-camera-option">
            <input type="checkbox" checked={camera} onChange={(e) => setCamera(e.target.checked)} />
            <span>
              Record the webcam too
              <small>Video for you to watch. Nothing analyses it. Untick for students who can&apos;t use a camera.</small>
            </span>
          </label>
        )}
      </div>

      <div className="viva-questions">
        <div className="card-head">
          <h3>Questions</h3>
          <span>
            <button type="button" className="btn secondary" onClick={draft} disabled={busy !== null}>
              {busy === "draft" ? "Drafting…" : aiEnabled ? "Draft questions from the essay" : "Use template questions"}
            </button>{" "}
            <button type="button" className="btn secondary" disabled={questions.length >= 8}
              onClick={() => setQuestions((qs) => [...qs, { text: "", passage: null, source: "reviewer" }])}>
              Add question
            </button>
          </span>
        </div>
        {aiEnabled && provider && provider !== "templates" && (
          <p className="note small">
            Drafted by external AI ({provider}) from the submission. Test data only. Check and edit every question
            before sending; each passage was checked to appear in the essay.
          </p>
        )}
        {!aiEnabled && <p className="muted small">External AI is off, so drafts come from the template bank without passages.</p>}
        {questions.length === 0 && <p className="muted small">Draft questions or add your own (up to 8).</p>}
        <ol className="viva-question-list">
          {questions.map((q, i) => (
            <li key={i}>
              <Field label={`Question ${i + 1}`} hint={sourceLabel[q.source]}>
                <textarea rows={2} required minLength={10} maxLength={400} value={q.text}
                  onChange={(e) => update(i, { text: e.target.value })} />
              </Field>
              <Field label="Passage from the essay (optional)" hint="Shown to the student with the question; must be copied exactly from the submission">
                <textarea rows={2} maxLength={600} value={q.passage ?? ""}
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

      <div className="form inline">
        <Field label="Student email"><input name="email" type="email" required /></Field>
        <Field label="Deadline (days)"><input name="deadline_days" type="number" min={1} max={30} defaultValue={7} required /></Field>
        <Field label="Preparation time (seconds)" hint="Per question, before answering">
          <input name="prep_seconds" type="number" min={0} max={300} defaultValue={30} required />
        </Field>
        <Field label="Answer time (seconds)" hint="Enforced by the server">
          <input name="answer_seconds" type="number" min={30} max={600} defaultValue={120} required />
        </Field>
      </div>
      <p className="muted small">
        The email names your institution and the deadline only: no score, no mention of AI, no assignment title.
      </p>
      <ErrorNote error={error} />
      <div className="actions">
        <button className="btn" disabled={busy !== null || questions.length === 0}>{busy === "send" ? "Sending…" : "Send invitation"}</button>
        <button type="button" className="link" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
