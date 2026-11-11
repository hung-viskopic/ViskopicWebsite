"use client";

import { useState, type FormEvent, type ReactNode } from "react";

export function PageHeader({ title, meta, children }: { title: string; meta?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {meta && <p className="meta-line">{meta}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

export type Tone = "attn" | "act" | "ok" | "err" | "calm";

/** Plain words for stored statuses. A status is always a word; the mark beside it only repeats it. */
const statusWords: Record<string, [string, Tone]> = {
  active: ["Active", "ok"],
  completed: ["Ready", "ok"],
  ready: ["Ready", "ok"],
  submitted: ["Finished", "ok"],
  queued: ["Waiting to start", "calm"],
  processing: ["Reading the file", "calm"],
  building: ["Being prepared", "calm"],
  invited: ["Invited", "calm"],
  sent: ["Sent", "calm"],
  opened: ["Opened", "calm"],
  in_progress: ["In progress", "calm"],
  archived: ["Archived", "calm"],
  superseded: ["Replaced", "calm"],
  expired: ["Expired", "calm"],
  revoked: ["Withdrawn", "calm"],
  withdrawn: ["Withdrawn", "calm"],
  removed: ["Removed", "calm"],
  disabled: ["Disabled", "calm"],
  suspended: ["Suspended", "calm"],
  not_eligible: ["Not scored", "calm"],
  unavailable: ["Did not run", "calm"],
  failed: ["Failed", "err"],
  low: ["Low confidence", "calm"],
  medium: ["Medium confidence", "calm"],
  high: ["High confidence", "calm"],
};

export function Status({ value, label, tone, wrap }: { value: string; label?: string; tone?: Tone; wrap?: boolean }) {
  const key = value.replaceAll(" ", "_");
  const [word, t] = statusWords[key] ?? [value.charAt(0).toUpperCase() + value.slice(1).replaceAll("_", " "), "calm" as Tone];
  return <span className={`st tone-${tone ?? t}${wrap ? " wrap" : ""}`}>{label ?? word}</span>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** A form that shows its own submit error and busy state. */
export function ActionForm({
  onSubmit,
  submitLabel,
  children,
  inline,
}: {
  onSubmit: (form: FormData) => Promise<void>;
  submitLabel: string;
  children: ReactNode;
  inline?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handle(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(new FormData(formEl));
      formEl.reset();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handle} className={inline ? "form inline" : "form"}>
      {children}
      <button className="btn" disabled={busy}>
        {busy ? "Working…" : submitLabel}
      </button>
      <ErrorNote error={error} />
    </form>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

/** The Viskopic mark, drawn inline so it takes the text colour of its place (ink on white, white on navy). */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" aria-hidden="true">
      <path fill="currentColor" d="M50 79c37 1 66 2 87 2 31 0 58-1 82-2l1 11c-21 1-36 2-44 5-7 2-11 7-11 13l3 12 102 236h2l95-239c1-2 2-6 2-10 0-5-3-9-9-11-6-2-16-4-30-6l-1-11c21 1 40 1 57 1 19 0 37 0 56-1l1 11c-13 1-23 4-31 8-8 4-14 10-18 19L261 445h-12L107 121c-7-17-26-27-57-30l-1-12z" />
      <path fill="#a3155f" d="M295 415l-7-1 101-249c8-20 32-34 54-32l7 1-101 249c-8 20-32 34-54 32z" />
    </svg>
  );
}
