"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ErrorNote, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { AnalysisReport, AnalysisRun, ConsistencyMeasurements, EvidenceItem, Span } from "@/lib/types";

const caseStatus: Record<string, string> = {
  analysing: "Analysing",
  awaiting_baseline: "Waiting for a baseline",
  awaiting_review: "Waiting for a quality review",
  evidence_ready: "Evidence ready",
  failed: "Analysis failed",
  withdrawn: "Withdrawn (document removed)",
};

const outcomeLabel: Record<string, string> = {
  "same-author-compatible": "Style compatible with the reference profile",
  "stylistically-unusual": "Stylistically unusual relative to the reference profile",
};

/** Titles for Δ states that carry no score or verdict (the model team's website contract). */
const notScoredTitle: Record<string, string> = {
  insufficient_evidence: "More writing needed",
  quality_review_required: "Text quality review needed",
  unsupported_text: "Unsupported text",
  cannot_score: "Comparison unavailable",
  demo_requires_unreviewed_mode: "Demonstration profile can't be scored here",
};

const qualityLabel: Record<string, string> = {
  passed: "reviewed",
  unreviewed: "not reviewed",
  stale_review: "review out of date",
  unsupported_text: "not supported",
  not_checked: "not checked",
};

/** Model numbers as the model team displays them: 3 decimals, tiny values as "Near zero", never a percentage. */
export function formatScore(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  if (value === 0) return "0.000";
  if (Math.abs(value) < 0.0005) return "Near zero";
  return value.toFixed(3);
}

const categoryLabel: Record<string, string> = {
  style: "Style",
  semantic: "Semantic",
  regeneration: "Regeneration",
  document: "Document",
};

type Selection = { key: string; spans: Span[] } | null;

/** Which pipelines the viewer has opened or folded, remembered in this browser only (a viewing convenience). */
const OPEN_STORAGE_KEY = "viskopic.report.openPipelines";

function readOpenState(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(OPEN_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeOpenState(state: Record<string, boolean>) {
  try {
    window.localStorage.setItem(OPEN_STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: the choice just isn't remembered */
  }
}

/** Default when the viewer hasn't chosen: completed runs open, placeholders and not-eligible runs folded. */
function defaultOpen(run: AnalysisRun) {
  return run.status === "completed";
}

/** One line shown in a folded pipeline's header so it can be scanned without opening it. */
function runSummary(run: AnalysisRun): string {
  const d = run.details;
  if (run.pipeline === "consistency" && run.status === "completed") {
    const label = outcomeLabel[run.outcome ?? ""] ?? run.outcome ?? "";
    return `Score ${formatScore(run.score)} · ${label}${run.confidence ? ` · ${run.confidence} confidence` : ""}`;
  }
  if (run.pipeline === "consistency" && run.status === "not_eligible" && d.website_status) {
    return notScoredTitle[d.website_status] ?? "Not scored";
  }
  if (run.status === "completed") {
    const n = run.evidence.length;
    return n ? `${n} observation${n > 1 ? "s" : ""}` : "No notable observations";
  }
  return run.reason ?? "";
}

/** The evidence report for one submission: why, where and how sure, per pipeline. */
export function EvidenceReport({ documentId, canManage, refreshKey = 0 }: {
  documentId: string;
  canManage: boolean;
  /** Bump this when something elsewhere on the page (e.g. a quality review) re-queues the analysis. */
  refreshKey?: number;
}) {
  const { api } = useAuth();
  const [poll, setPoll] = useState(true);
  const report = useResource<AnalysisReport>(`/documents/${documentId}/analysis`, poll ? 3000 : undefined);
  const [text, setText] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection>(null);
  const [error, setError] = useState<string | null>(null);
  const [openState, setOpenState] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  // After an action, keep polling until the case has visibly moved on (its updated_at changes and it is no
  // longer analysing). Watching the status alone misses a re-run that ends in the same status as before.
  const waitRef = useRef<{ since: string | null; started: number } | null>(null);

  useEffect(() => {
    setOpenState(readOpenState());
  }, []);

  useEffect(() => {
    if (!report.data) return;
    const c = report.data.case;
    const wait = waitRef.current;
    if (wait && c && c.status !== "analysing" && c.updated_at !== wait.since) waitRef.current = null;
    if (wait && Date.now() - wait.started > 120_000) waitRef.current = null;
    setPoll(c?.status === "analysing" || waitRef.current !== null);
  }, [report.data]);

  function followUp() {
    waitRef.current = { since: report.data?.case?.updated_at ?? null, started: Date.now() };
    setPoll(true);
    report.reload();
  }

  const reload = report.reload;
  useEffect(() => {
    if (refreshKey > 0) {
      waitRef.current = { since: null, started: Date.now() };
      setPoll(true);
      reload();
    }
  }, [refreshKey, reload]);

  function updateOpen(next: Record<string, boolean>) {
    setOpenState(next);
    writeOpenState(next);
  }

  const isOpen = (run: AnalysisRun) => openState[run.pipeline] ?? defaultOpen(run);

  if (report.error) return <ErrorNote error={report.error} />;
  if (!report.data) return null;
  const r = report.data;
  if (r.role !== "submission") return null;

  async function loadText() {
    if (text !== null) return text;
    const t = (await api<{ text: string }>(`/documents/${documentId}/text`)).text;
    setText(t);
    return t;
  }

  async function show(key: string, spans: Span[]) {
    try {
      await loadText();
      setSelected({ key, spans });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function reanalyse() {
    setBusy(true);
    setError(null);
    try {
      await api(`/cases/${r.case!.id}/reanalyse`, { method: "POST" });
      followUp();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card report">
      <div className="report-head">
        <div>
          <h2>Evidence report</h2>
          <p className="muted">
            {r.case ? caseStatus[r.case.status] ?? r.case.status : "Not analysed yet"}
            {r.case && r.case.status !== "analysing" && (
              <span className="small"> · last updated {fmtDate(r.case.updated_at)}</span>
            )}
          </p>
        </div>
        <div className="report-actions">
          {r.runs.length > 1 && (
            <>
              <button
                className="link"
                onClick={() => updateOpen({ ...openState, ...Object.fromEntries(r.runs.map((x) => [x.pipeline, true])) })}
              >
                Expand all
              </button>
              <button
                className="link"
                onClick={() => updateOpen({ ...openState, ...Object.fromEntries(r.runs.map((x) => [x.pipeline, false])) })}
              >
                Collapse all
              </button>
            </>
          )}
          {r.case && canManage && r.case.status !== "analysing" && (
            <button className="btn secondary" onClick={reanalyse} disabled={busy}>
              {busy ? "Queuing…" : "Run analysis again"}
            </button>
          )}
        </div>
      </div>
      <ErrorNote error={error} />
      {!r.case && <p className="muted">Analysis starts automatically once the text has been extracted.</p>}
      {r.case?.status === "analysing" && <p className="muted">Running analysis. This page updates automatically.</p>}
      {r.case?.status === "awaiting_baseline" && (
        <p className="note info">
          Waiting for enough baseline writing from this student. The case is analysed again once the profile is built.
        </p>
      )}
      {r.case?.status === "awaiting_review" && (
        <p className="note info">
          The consistency model only compares texts a reviewer has checked. Record a quality review for this
          submission (above) and for each of the student&apos;s baseline documents; the case is analysed again
          after each review. The Δ pipeline below shows which side still needs one.
        </p>
      )}

      {r.runs.map((run) => (
        <RunCard
          key={run.id}
          run={run}
          open={isOpen(run)}
          onToggle={() => updateOpen({ ...openState, [run.pipeline]: !isOpen(run) })}
          selectedKey={selected?.key ?? null}
          onShow={show}
        />
      ))}

      {r.runs.length > 0 && (
        <p className="muted small">
          These results support a review. They are not a finding of misconduct, and no outcome has been recorded.
          {r.total_runs && r.total_runs > r.runs.length ? ` Earlier analysis runs are kept (${r.total_runs} in total).` : ""}
        </p>
      )}

      {text !== null && (
        <div className="report-text">
          <div className="report-text-head">
            <h3>Where</h3>
            <span className="muted small">
              {selected?.spans.length
                ? `${selected.spans.length} highlighted passage${selected.spans.length > 1 ? "s" : ""}`
                : "Choose Show in text on an evidence item."}
              {selected?.spans.some((s) => s.status === "approximate") && " (approximate boundaries)"}
            </span>
          </div>
          <HighlightedText text={text} spans={selected?.spans ?? []} />
          <p className="muted small section-gap">Opening the text is recorded in the audit log.</p>
        </div>
      )}
    </section>
  );
}

function RunCard({ run, open, onToggle, selectedKey, onShow }: {
  run: AnalysisRun;
  open: boolean;
  onToggle: () => void;
  selectedKey: string | null;
  onShow: (key: string, spans: Span[]) => void;
}) {
  const d = run.details;
  const bodyId = `run-body-${run.id}`;
  const summary = runSummary(run);
  return (
    <div className={`run run-${run.status}${open ? " open" : " folded"}`}>
      <h3 className="run-head">
        <button type="button" className="run-toggle" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
          <span className="chevron" aria-hidden="true" />
          <span className="run-title">{run.label}</span>
          <Status value={run.status === "not_eligible" ? "not eligible" : run.status} />
          {!open && summary && <span className="run-summary">{summary}</span>}
        </button>
      </h3>

      <div id={bodyId} className="run-body" hidden={!open}>

        {run.pipeline === "consistency" && run.status === "not_eligible" && d.website_status && (
          <p className="not-scored-title">{notScoredTitle[d.website_status] ?? "Not scored"}</p>
        )}
        {run.reason && <p className="muted">{run.reason}</p>}
        {run.pipeline === "consistency" && run.status === "not_eligible" && d.quality && d.website_status !== "insufficient_evidence" && (
          <p className="muted small">
            Submission: {qualityLabel[d.quality.candidate] ?? d.quality.candidate}. Baseline documents:{" "}
            {qualityLabel[d.quality.references] ?? d.quality.references}.
          </p>
        )}

        {run.pipeline === "consistency" && run.status === "completed" && (
          <>
            <p className="experimental">
              <span className="status status-medium">Experimental</span>
              {d.website_status === "scored_demo_unreviewed"
                ? " Unreviewed demonstration: nobody has checked the language and extraction of these texts."
                : " Input quality reviewed. The model is not yet validated for student writing."}
            </p>
            {run.profile?.source === "demonstration" && (
              <p className="note">
                Demonstration only. Compared with research author {run.profile.demo_author_id}, not this student&apos;s
                own writing.
              </p>
            )}
            <div className="stats">
              <div className="stat">
                <p className="label">Centred style score (V1.3)</p>
                <strong>{formatScore(run.score)}</strong>
                <span className="muted small">Frozen threshold {formatScore(run.threshold)}</span>
              </div>
              <div className="stat">
                <p className="label">Model label</p>
                <span className="outcome">{outcomeLabel[run.outcome ?? ""] ?? run.outcome}</span>
              </div>
              <div className="stat">
                <p className="label">Confidence</p>
                {run.confidence && <Status value={run.confidence} />}
                <span className="muted small block">{d.confidence_reason}</span>
              </div>
            </div>
            {d.measurements && <Measurements m={d.measurements} />}
            {d.interpretation_warning && <p className="muted small">{d.interpretation_warning}</p>}
          </>
        )}

        {run.evidence.length > 0 && (
          <ol className="evidence">
            {run.evidence.map((ev) => {
              const key = `${run.id}:${ev.ordinal}`;
              return (
                <EvidenceRow key={key} ev={ev} active={selectedKey === key} onShow={() => onShow(key, ev.spans)} />
              );
            })}
          </ol>
        )}
        {run.status === "completed" && run.evidence.length === 0 && (
          <p className="muted">No notable observations.</p>
        )}

        <p className="muted small">{run.pipeline_version}, {fmtDate(run.created_at)}</p>
      </div>
    </div>
  );
}

/** The descriptive diagnostics, each shown separately and unchanged. Only the centred score decides the label. */
function Measurements({ m }: { m: ConsistencyMeasurements }) {
  const rows: [string, string][] = [
    ["Margin to threshold", formatScore(m.margin_to_threshold)],
    ["Raw distance (lower is closer)", formatScore(m.raw_distance)],
    ["Distance ÷ typical distance", formatScore(m.relative_distance)],
    ["Typical distance in the baseline", formatScore(m.shrunk_typical_genuine_distance)],
    ["Background median", formatScore(m.background_median_v1_2_similarity)],
  ];
  return (
    <>
      <dl className="meta measurements">
        {rows.map(([k, v]) => (
          <Fragment key={k}><dt>{k}</dt><dd className="mono">{v}</dd></Fragment>
        ))}
      </dl>
      <p className="muted small">
        The centred score is not a percentage or a probability, and can be negative. Near zero means about equal to
        the background median. Distances are descriptive: they use clipped feature deviations and do not change the
        label.
      </p>
    </>
  );
}

function EvidenceRow({ ev, active, onShow }: { ev: EvidenceItem; active: boolean; onShow: () => void }) {
  return (
    <li className={active ? "active" : undefined}>
      <div className="evidence-meta">
        <span className="chip">{categoryLabel[ev.category] ?? ev.category}</span>
        {ev.contribution !== null && (
          <span className="contribution" title={`Share of this pipeline's evidence: ${(ev.contribution * 100).toFixed(0)}%`}>
            <span style={{ width: `${Math.max(4, Math.min(100, ev.contribution * 100))}%` }} />
          </span>
        )}
      </div>
      <p>{ev.statement}</p>
      {ev.spans.length > 0 && (
        <button className="link" onClick={onShow}>Show in text ({ev.spans.length})</button>
      )}
    </li>
  );
}

/** Renders text with <mark> around the given spans and scrolls to the first one. */
function HighlightedText({ text, spans }: { text: string; spans: Span[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const parts = useMemo(() => {
    const sorted = [...spans].filter((s) => s.end > s.start).sort((a, b) => a.start - b.start);
    const out: { text: string; mark: boolean }[] = [];
    let pos = 0;
    for (const s of sorted) {
      const start = Math.max(s.start, pos);
      if (start > pos) out.push({ text: text.slice(pos, start), mark: false });
      if (s.end > start) out.push({ text: text.slice(start, s.end), mark: true });
      pos = Math.max(pos, s.end);
    }
    out.push({ text: text.slice(pos), mark: false });
    return out;
  }, [text, spans]);

  useEffect(() => {
    ref.current?.querySelector("mark")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [parts]);

  return (
    <div className="text-view" ref={ref}>
      {parts.map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <Fragment key={i}>{p.text}</Fragment>))}
    </div>
  );
}
