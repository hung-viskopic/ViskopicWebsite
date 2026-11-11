"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote, Field, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { DraftQuestion, Visq2Preview, VivaInvitation, VivaList } from "@/lib/types";

const kindLabel = { style: "Style viva", reasoning: "Reasoning viva" } as const;
const sourceLabel: Record<string, string> = { ai_draft: "AI draft", template: "Template", reviewer: "Written by reviewer" };
/** What each kind of question is, in the words a reviewer would use (Visq 2 steps, and older Visq 1 types). */
export const qtypeLabel: Record<string, string> = {
  clarify: "Clarifying question", extension: "Extension (what-if)", viva: "Viva question",
  tradeoff: "Trade-off", limit: "What if a reason failed", case: "New case", followup: "Follow-up",
};

/** Viva tab on a case: each invitation as a timeline, and the form to plan a new one. */
export function VivaPanel({ documentId, startOpen = false }: { documentId: string; startOpen?: boolean }) {
  const vivas = useResource<VivaList>(`/documents/${documentId}/vivas`, 5000);
  const [open, setOpen] = useState(startOpen);
  const [lastLink, setLastLink] = useState<string | null>(null);
  const v = vivas.data;
  useEffect(() => { if (startOpen) setOpen(true); }, [startOpen]);

  return (
    <div className="stack">
      <ErrorNote error={vivas.error} />
      {v && !v.case && <p className="muted">A viva can be planned once the essay has been read.</p>}
      {lastLink && (
        <p className="note info" role="status">
          Invitation queued. The email goes to the student shortly. Test link (to try the viva yourself):{" "}
          <a href={lastLink} target="_blank" rel="noreferrer">open the student page</a>
        </p>
      )}
      {open && v?.case && (
        <section className="card">
          <InvitationForm caseId={v.case.id} visqEnabled={v.visq_enabled}
            onCancel={() => setOpen(false)}
            onSent={(link) => { setOpen(false); setLastLink(link); vivas.reload(); }} />
        </section>
      )}
      {v?.invitations.map((i) => (
        <Invitation key={i.id} i={i} canManage={v.can_manage}
          onDone={(link) => { if (link) setLastLink(link); vivas.reload(); }} />
      ))}
      {v && v.case && v.invitations.length === 0 && !open && (
        <section className="card stack-sm">
          <h2>No viva yet</h2>
          <p className="lede">A viva asks the student about their own essay. You read the answers and rate them; the rating is not a decision.</p>
        </section>
      )}
      {v?.can_manage && v.case && v.case.status !== "withdrawn" && !open && (
        <div><button className="btn" onClick={() => { setOpen(true); setLastLink(null); }}>
          {v.invitations.length ? "Plan another viva" : "Plan a viva"}
        </button></div>
      )}
    </div>
  );
}

const stepNames = ["Sent", "Opened", "Started", "Finished"] as const;

function Invitation({ i, canManage, onDone }: { i: VivaInvitation; canManage: boolean; onDone: (link?: string) => void }) {
  const times = [i.sent_at, i.opened_at, i.started_at, i.submitted_at];
  const reached = times.filter(Boolean).length;
  const closed = ["revoked", "expired", "failed", "superseded"].includes(i.status);
  const tone = i.status === "submitted" ? "ok" : i.status === "failed" || i.status === "expired" ? "err" : closed ? "calm" : "act";
  return (
    <section className="card stack-sm">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 style={{ margin: 0 }}>{kindLabel[i.viva_kind]}{i.flow === "visq2" ? ", questions by Visq" : ""}</h2>
          <span className="muted small">Planned {fmtDate(i.created_at)} · due {fmtDate(i.deadline_at)} · {i.answered ?? 0} of {i.questions ?? 0} answered</span>
        </div>
        <Status value={i.status} tone={tone} />
      </div>
      {!closed && (
        <ol className="timeline">
          {stepNames.map((n, k) => (
            <li key={n} className={k < reached ? "done" : k === reached ? "now" : undefined}>
              <b>{n}</b>{times[k] ? fmtDate(times[k]) : k === reached ? "Waiting" : ""}
            </li>
          ))}
        </ol>
      )}
      {i.last_error && <p className="note small">{i.last_error}</p>}
      {i.status === "revoked" && i.revoke_reason && <p className="muted small">Withdrawn: &ldquo;{i.revoke_reason}&rdquo;</p>}
      <div className="page-actions" style={{ justifyContent: "flex-start" }}>
        <Link href={`/vivas/${i.id}`} className={i.status === "submitted" ? "btn" : "btn secondary"}>
          {i.status === "submitted" ? "Read and rate the answers" : "Details"}
        </Link>
        {canManage && ["queued", "sent", "opened", "failed", "expired"].includes(i.status) && (
          <InvitationActions id={i.id} status={i.status} onDone={onDone} />
        )}
      </div>
    </section>
  );
}

function InvitationActions({ id, status, onDone }: { id: string; status: string; onDone: (link?: string) => void }) {
  const { api } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState(false);
  const [reason, setReason] = useState("");
  async function resend() {
    try {
      const r = await api<{ test_link: string }>(`/viva-invitations/${id}/resend`, { method: "POST" });
      onDone(r.test_link);
    } catch (e) { setError((e as Error).message); }
  }
  async function revoke() {
    try {
      await api(`/viva-invitations/${id}/revoke`, { method: "POST", body: JSON.stringify({ reason }) });
      setWithdrawing(false);
      onDone();
    } catch (e) { setError((e as Error).message); }
  }
  return (
    <>
      <button className="btn secondary" onClick={resend}>Send a new link</button>
      {status !== "failed" && status !== "expired" && !withdrawing && (
        <button className="btn secondary" onClick={() => setWithdrawing(true)}>Withdraw</button>
      )}
      {withdrawing && (
        <div className="inline-form" style={{ flexBasis: "100%" }}>
          <Field label="Why are you withdrawing this invitation?" hint="Kept in the activity log. The student isn't told the reason.">
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <div className="page-actions" style={{ justifyContent: "flex-start" }}>
            <button className="btn danger" disabled={reason.trim().length < 3} onClick={revoke}>Withdraw invitation</button>
            <button className="link" onClick={() => setWithdrawing(false)}>Cancel</button>
          </div>
        </div>
      )}
      <ErrorNote error={error} />
    </>
  );
}

/** Visq's viva question types, as a reviewer would describe them. */
const visqTypeLabel: Record<string, string> = {
  rebuild: "Rebuild the argument", recall: "Recall a point", elicit: "Unstated assumption",
  flip: "What if a point changed", counter: "Answer an objection", place: "Place a new case",
};

/** A row of mutually exclusive options that reads as one control. */
function Segmented<T extends string>({ name, value, options, onChange }: {
  name: string; value: T; options: { value: T; label: string; disabled?: boolean }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={name}>
      {options.map((o) => (
        <label key={o.value} className={value === o.value ? "on" : undefined} aria-disabled={o.disabled}>
          <input type="radio" name={name} value={o.value} checked={value === o.value} disabled={o.disabled}
            onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange, title, children }: {
  checked: boolean; onChange: (v: boolean) => void; title: string; children: React.ReactNode;
}) {
  return (
    <label className="toggle-row">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" aria-hidden="true" />
      <span><strong>{title}</strong><small>{children}</small></span>
    </label>
  );
}

function InvitationForm({ caseId, visqEnabled, onCancel, onSent }: {
  caseId: string;
  /** Visq 2 world-tree vivas. */
  visqEnabled: boolean;
  onCancel: () => void;
  onSent: (link: string) => void;
}) {
  const { api } = useAuth();
  const [flow, setFlow] = useState<"visq2" | "standard">(visqEnabled ? "visq2" : "standard");
  const [kind, setKind] = useState<"style" | "reasoning">("reasoning");
  const [full, setFull] = useState(false);
  const [extMinutes, setExtMinutes] = useState(6);
  const [fullscreen, setFullscreen] = useState(true);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [preview, setPreview] = useState<Visq2Preview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visq2 = flow === "visq2";
  // Spoken answers are not offered for now: every viva is typed (the spoken path stays on the server).
  const fmt = "typed" as const;
  const stale = !!preview && (preview.visq2_full !== full || preview.extension_minutes !== extMinutes);
  const ready = visq2 ? preview?.status === "ready" && !stale : questions.length > 0;

  // Visq reads the essay in the background; check back until the preview is ready or failed.
  useEffect(() => {
    if (preview?.status !== "preparing") return;
    const t = setInterval(async () => {
      try { setPreview(await api<Visq2Preview>(`/visq2-previews/${preview.id}`)); } catch (e) { setError((e as Error).message); }
    }, 2000);
    return () => clearInterval(t);
  }, [preview, api]);

  async function prepare() {
    setBusy("prepare");
    setError(null);
    try {
      setPreview(await api<Visq2Preview>(`/cases/${caseId}/visq2-previews`, {
        method: "POST", body: JSON.stringify({ visq2_full: full, extension_minutes: extMinutes }),
      }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

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
          answer_format: fmt,
          camera: false,
          fullscreen,
          deadline_days: Number(form.get("deadline_days")),
          prep_seconds: Number(form.get("prep_seconds")),
          answer_seconds: Number(form.get("answer_seconds")),
          ...(visq2
            ? { visq2_full: full, extension_minutes: extMinutes, visq2_preview_id: preview?.id }
            : { questions: questions.map((q) => ({ text: q.text, passage: q.passage || null, source: q.source })) }),
        }),
      });
      onSent(r.test_link);
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  return (
    <form className="viva-form" onSubmit={(e) => { e.preventDefault(); send(new FormData(e.currentTarget)); }}>
      <div className="vf-head">
        <h2 style={{ margin: 0 }}>Plan a viva</h2>
        <button type="button" className="link" onClick={onCancel}>Cancel</button>
      </div>

      {/* 1. Questions */}
      <fieldset className="vf-section">
        <legend><span className="vf-step">1</span> Questions</legend>
        {visqEnabled ? (
          <div className="choice-cards" role="radiogroup" aria-label="Who writes the questions">
            <label className={visq2 ? "on" : undefined}>
              <input type="radio" name="flow" checked={visq2} onChange={() => setFlow("visq2")} />
              <strong>Visq 2</strong>
              <span>Written from this essay. You preview them before sending.</span>
            </label>
            <label className={!visq2 ? "on" : undefined}>
              <input type="radio" name="flow" checked={!visq2} onChange={() => setFlow("standard")} />
              <strong>My own questions</strong>
              <span>Write your own or start from templates.</span>
            </label>
          </div>
        ) : (
          <p className="muted small">Visq is off, so the viva uses your own or template questions.</p>
        )}

        <div className="vf-grid">
          <div className="vf-control">
            <span className="label">Focus</span>
            <Segmented name="Focus" value={kind} onChange={setKind} options={[
              { value: "reasoning", label: "Reasoning" }, { value: "style", label: "Writing style" }]} />
            <small className="muted">{kind === "reasoning" ? "The argument and the evidence" : "How the essay was written"}</small>
          </div>
          {visq2 && (
            <>
              <div className="vf-control">
                <span className="label">Length</span>
                <Segmented name="Length" value={full ? "full" : "short"} onChange={(v) => setFull(v === "full")} options={[
                  { value: "short", label: "Short · 3" }, { value: "full", label: "Full · 7" }]} />
                <small className="muted">Viva questions after the what-if</small>
              </div>
              <Field label="What-if time (minutes)" hint="Typed, timed by the server">
                <input type="number" min={1} max={30} value={extMinutes} required
                  onChange={(e) => setExtMinutes(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} />
              </Field>
            </>
          )}
        </div>

        {visq2 ? (
          <Visq2PreviewBox preview={preview} stale={stale} busy={busy === "prepare"} onPrepare={prepare} />
        ) : (
          <div className="viva-questions">
            <div className="vf-row">
              <button type="button" className="btn secondary" onClick={draft} disabled={busy !== null}>
                {busy === "draft" ? "Loading…" : "Use template questions"}
              </button>
              <button type="button" className="btn secondary" disabled={questions.length >= 8}
                onClick={() => setQuestions((qs) => [...qs, { text: "", passage: null, source: "reviewer" }])}>
                Add question
              </button>
            </div>
            {questions.length === 0 && <p className="muted small">Up to 8 questions. The student sees them one at a time.</p>}
            <ol className="viva-question-list">
              {questions.map((q, i) => (
                <li key={i}>
                  <Field label={`Question ${i + 1}`} hint={sourceLabel[q.source]}>
                    <textarea rows={2} required minLength={10} maxLength={400} value={q.text}
                      onChange={(e) => update(i, { text: e.target.value })} />
                  </Field>
                  <Field label="Passage from the essay (optional)" hint="Shown with the question; copy it exactly from the submission">
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
      </fieldset>

      {/* 2. Answering */}
      <fieldset className="vf-section">
        <legend><span className="vf-step">2</span> How the student answers</legend>
        <p className="muted small">The student types each answer. Pasting is blocked.</p>
        <div className="vf-grid">
          <Field label="Think time (seconds)" hint="Before each answer">
            <input name="prep_seconds" type="number" min={0} max={300} defaultValue={visq2 ? 0 : 30} key={`p-${flow}`} required />
          </Field>
          <Field label="Answer time (seconds)" hint="Per question, enforced by the server">
            <input name="answer_seconds" type="number" min={30} max={600} defaultValue={visq2 ? 180 : 120} key={`a-${flow}`} required />
          </Field>
        </div>
        <div className="toggle-list">
          <Toggle checked={fullscreen} onChange={setFullscreen} title="Require full screen">
            Laptop or desktop only. Leaving full screen hides the question and is recorded. Turn off if the
            student&apos;s assistive tools don&apos;t work in full screen.
          </Toggle>
        </div>
      </fieldset>

      {/* 3. Recipient */}
      <fieldset className="vf-section">
        <legend><span className="vf-step">3</span> Send to</legend>
        <div className="vf-grid">
          <Field label="Student email"><input name="email" type="email" required autoComplete="off" /></Field>
          <Field label="Deadline (days)"><input name="deadline_days" type="number" min={1} max={30} defaultValue={7} required /></Field>
        </div>
        <p className="muted small">The email names your institution and the deadline only: no score, no mention of AI, no assignment title.</p>
      </fieldset>

      <ErrorNote error={error} />
      <div className="vf-foot">
        <button className="btn" disabled={busy !== null || !ready}>{busy === "send" ? "Sending…" : "Send the invitation"}</button>
        <span className="muted small">
          {visq2 && !ready ? "Prepare and check the questions first." : null}
          {visq2 && ready ? "The essay and answers go to an outside model. Test essays only." : null}
          {!visq2 && !ready ? "Add at least one question." : null}
        </span>
      </div>
    </form>
  );
}

function Visq2PreviewBox({ preview, stale, busy, onPrepare }: {
  preview: Visq2Preview | null; stale: boolean; busy: boolean; onPrepare: () => void;
}) {
  const d = preview?.draft;
  if (!preview || preview.status === "failed" || stale) {
    return (
      <div className="vq-preview vq-empty">
        <div>
          <strong>{stale ? "Settings changed" : preview?.status === "failed" ? "Visq could not read the essay" : "See the questions before you send"}</strong>
          <p className="muted small">
            {stale ? "Length or what-if time changed since the questions were prepared. Prepare them again."
              : preview?.status === "failed" ? preview.error
              : "Visq reads the essay now and shows you what it will ask. It takes up to a minute."}
          </p>
        </div>
        <button type="button" className="btn" onClick={onPrepare} disabled={busy}>
          {busy ? "Starting…" : preview ? "Prepare again" : "Prepare questions"}
        </button>
      </div>
    );
  }
  if (preview.status === "preparing" || !d) {
    return (
      <div className="vq-preview vq-empty" role="status">
        <div><strong><span className="spinner" aria-hidden="true" /> Visq is reading the essay…</strong>
          <p className="muted small">You can fill in the rest of the form meanwhile.</p></div>
      </div>
    );
  }
  return (
    <div className="vq-preview">
      <div className="vq-preview-head">
        <div>
          <strong>Questions prepared</strong>
          <span className="muted small block">Prepared {fmtDate(preview.updated_at)}. {preview.engine}</span>
        </div>
        <button type="button" className="btn secondary" onClick={onPrepare} disabled={busy}>Prepare again</button>
      </div>

      <section className="vq-stage">
        <h4>Clarifying questions <span className="vq-tag exact">Exact</span></h4>
        {d.clarify.length === 0
          ? <p className="muted small">None: the essay isn&apos;t silent about anything it relies on.</p>
          : <ol>{d.clarify.map((q, i) => <li key={i}>{q.text}</li>)}</ol>}
      </section>

      <section className="vq-stage">
        <h4>What-if extension <span className="vq-tag">Draft</span></h4>
        {d.extension.available
          ? <blockquote className="vq-whatif">{d.extension.prompt}</blockquote>
          : <p className="muted small">{d.extension.prompt || "No what-if for this essay."}</p>}
      </section>

      <section className="vq-stage">
        <h4>Viva questions <span className="vq-tag">Draft</span></h4>
        {d.insufficient && <p className="note small">{d.insufficient_reason || "The essay gives too little to build a full viva."}</p>}
        <ol>
          {d.viva.map((q, i) => (
            <li key={i}><span className="vq-type">{visqTypeLabel[q.type] ?? q.type}</span>{q.text}</li>
          ))}
        </ol>
      </section>

      <p className="muted small vq-foot">
        The student gets the clarifying questions exactly as shown. The what-if and the viva questions are written
        again from the student&apos;s own answers, so wording and choice may differ; one-off follow-ups depend on each
        answer. Every question asked appears in the report.
      </p>
    </div>
  );
}
