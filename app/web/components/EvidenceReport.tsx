"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ErrorNote, Status, type Tone } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { AnalysisReport, AnalysisRun, CombinedReport, ConsistencyMeasurements, EvidenceItem, Span } from "@/lib/types";
import { aiSentence, plural, styleSentence } from "@/lib/words";

/** The model's own labels, shown word for word in the technical details. */
const outcomeLabel: Record<string, string> = {
  "same-author-compatible": "Style compatible with the reference profile",
  "stylistically-unusual": "Stylistically unusual relative to the reference profile",
  interchangeable: "Interchangeable with AI essays on this assignment",
  not_interchangeable: "Parts depend on each other more than in AI essays",
};

/** Δ states that carry no score or verdict (the model team's website contract). */
const notScoredTitle: Record<string, string> = {
  insufficient_evidence: "More past writing is needed",
  quality_review_required: "Texts need checking first",
  unsupported_text: "These texts can't be compared",
  cannot_score: "The comparison couldn't run",
  demo_requires_unreviewed_mode: "This demonstration profile can't be scored here",
};

const qualityLabel: Record<string, string> = {
  passed: "checked",
  unreviewed: "not checked",
  stale_review: "checked, but the text has changed since",
  unsupported_text: "not supported",
  not_checked: "not checked",
};

/** Plain headings for evidence kinds whose template statement doesn't start with its own label. */
const kindHeading: Record<string, string> = {
  interchangeability: "How the opening relates to the rest of the essay",
  comparison_coverage: "Which AI essays it was compared with",
  hidden_text: "Hidden text in the file",
  editing_time: "Editing time recorded in the file",
};

/** Items that describe the method rather than this essay: shown in the technical details. */
const METHOD_KINDS = new Set(["overall_score", "style_distance", "threshold_validation"]);

/** Model numbers as the model team displays them: 3 decimals, tiny values as "Near zero", never a percentage. */
export function formatScore(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  if (value === 0) return "0.000";
  if (Math.abs(value) < 0.0005) return "Near zero";
  return value.toFixed(3);
}

function num(v: number) {
  return Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : Number(v.toFixed(2)).toLocaleString();
}

/** The heading is the model's own label where the statement starts with one ("Sentence length: …"). */
function heading(ev: EvidenceItem): string {
  if (kindHeading[ev.kind]) return kindHeading[ev.kind];
  const i = ev.statement.indexOf(":");
  return i > 0 && i < 70 ? ev.statement.slice(0, i) : ev.statement.split(". ")[0];
}

type Selection = { key: string; spans: Span[] } | null;

/** The evidence for one submission: a plain summary, the findings beside the essay, technical details folded. */
export function EvidenceReport({ documentId, canManage, refreshKey = 0, onSendViva }: {
  documentId: string;
  canManage: boolean;
  /** Bump this when something elsewhere on the page (e.g. a text check) re-queues the analysis. */
  refreshKey?: number;
  onSendViva?: () => void;
}) {
  const { api } = useAuth();
  const [poll, setPoll] = useState(true);
  const report = useResource<AnalysisReport>(`/documents/${documentId}/analysis`, poll ? 3000 : undefined);
  const [text, setText] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection>(null);
  // The text is fetched once (each fetch is an audited view); hiding it only folds the panel.
  const [essayOpen, setEssayOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // After an action, keep polling until the case has visibly moved on (its updated_at changes and it is no
  // longer analysing). Watching the status alone misses a re-run that ends in the same status as before.
  const waitRef = useRef<{ since: string | null; started: number } | null>(null);

  useEffect(() => {
    if (!report.data) return;
    const c = report.data.case;
    const wait = waitRef.current;
    if (wait && c && c.status !== "analysing" && c.updated_at !== wait.since) waitRef.current = null;
    if (wait && Date.now() - wait.started > 120_000) waitRef.current = null;
    setPoll(c?.status === "analysing" || waitRef.current !== null);
  }, [report.data]);

  const reload = report.reload;
  useEffect(() => {
    if (refreshKey > 0) {
      waitRef.current = { since: null, started: Date.now() };
      setPoll(true);
      reload();
    }
  }, [refreshKey, reload]);

  if (report.error) return <ErrorNote error={report.error} />;
  if (!report.data) return <p className="muted">Loading the evidence…</p>;
  const r = report.data;
  if (r.role !== "submission") return null;

  const style = r.runs.find((x) => x.pipeline === "consistency") ?? null;
  const ai = r.runs.find((x) => x.pipeline === "interchangeability") ?? null;
  const doc = r.runs.find((x) => x.pipeline === "document_signals" || x.pipeline === "signals") ?? null;
  const others = r.runs.filter((x) => x !== style && x !== ai && x !== doc);

  async function loadText() {
    if (text !== null) return text;
    const t = (await api<{ text: string }>(`/documents/${documentId}/text`)).text;
    setText(t);
    setEssayOpen(true);
    return t;
  }

  async function show(key: string, spans: Span[]) {
    try {
      await loadText();
      setEssayOpen(true);
      setSelected((s) => (s?.key === key ? null : { key, spans }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function reanalyse() {
    setBusy(true);
    setError(null);
    try {
      await api(`/cases/${r.case!.id}/reanalyse`, { method: "POST" });
      waitRef.current = { since: r.case?.updated_at ?? null, started: Date.now() };
      setPoll(true);
      report.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const groups: [string, AnalysisRun | null][] = [["Writing style", style], ["AI comparison", ai], ["The file itself", doc]];

  return (
    <div className="stack">
      <Summary r={r} style={style} ai={ai} canManage={canManage} busy={busy} onReanalyse={reanalyse} onSendViva={onSendViva} />
      <ErrorNote error={error} />

      {r.case && r.case.status !== "analysing" && (
        <div className="split reading">
          <div className="main-col">
            {groups.map(([title, run]) => run && (
              <RunEvidence key={title} title={title} run={run} selectedKey={selected?.key ?? null} onShow={show} />
            ))}
            {others.map((run) => (
              <RunEvidence key={run.id} title={run.label} run={run} selectedKey={selected?.key ?? null} onShow={show} />
            ))}
          </div>
          <div className="side-col sticky">
            <EssayPanel text={essayOpen ? text : null} selected={selected}
              onOpen={() => (text !== null ? setEssayOpen(true) : loadText().catch((e: Error) => setError(e.message)))}
              onClose={() => { setEssayOpen(false); setSelected(null); }} />
          </div>
        </div>
      )}

      {r.runs.length > 0 && (
        <details className="card">
          <summary>Technical details: scores, thresholds and versions</summary>
          <div className="stack-sm" style={{ marginTop: 16 }}>
            {r.combined && <CombinedDetails c={r.combined} />}
            {r.runs.map((run) => <RunDetails key={run.id} run={run} />)}
            {r.total_runs && r.total_runs > r.runs.length ? (
              <p className="muted small">Earlier runs are kept ({r.total_runs} in total).</p>
            ) : null}
          </div>
        </details>
      )}
    </div>
  );
}

function Summary({ r, style, ai, canManage, busy, onReanalyse, onSendViva }: {
  r: AnalysisReport; style: AnalysisRun | null; ai: AnalysisRun | null; canManage: boolean; busy: boolean;
  onReanalyse: () => void; onSendViva?: () => void;
}) {
  const c = r.case;
  const s = styleSentence(style && { status: style.status, outcome: style.outcome, website_status: style.details.website_status });
  const a = aiSentence(ai && { status: ai.status, outcome: ai.outcome });
  const flagged = r.combined?.status === "review_recommended";

  let sentence: string;
  let tone: "attn" | "calm" = "calm";
  if (!c) sentence = "The checks start once the file has been read.";
  else if (c.status === "analysing") sentence = "The checks are running. This page updates by itself.";
  else if (c.status === "failed") sentence = "The checks couldn't finish.";
  else if (c.status === "withdrawn") sentence = "This case was withdrawn because the essay was removed.";
  else if (flagged) {
    tone = "attn";
    sentence = s.flagged && a.flagged ? "Both checks flagged this essay. A person should look at the evidence."
      : s.flagged ? s.text : a.text;
  } else if (c.status === "awaiting_review") sentence = "Some texts need checking before the writing style check can run.";
  else if (c.status === "awaiting_baseline") sentence = "Writing style is waiting for more of this student's past essays.";
  else if (r.combined?.status === "no_flag") sentence = "Neither check flagged this essay.";
  else sentence = "There isn't enough information to compare yet.";

  const lines: [string, Tone, string][] = [];
  if (c && c.status !== "analysing") {
    lines.push(["Writing style", s.flagged ? "attn" : style?.status === "completed" ? "ok" : "calm", s.text]);
    lines.push(["AI comparison", a.flagged ? "attn" : ai?.status === "completed" ? "ok" : "calm", a.text]);
  }
  const conf = style?.status === "completed" && style.confidence;
  const demo = style?.profile?.source === "demonstration";

  return (
    <section className={`card summary tone-${tone}`} style={{ marginBottom: 0 }} role="status">
      <div className="say">
        <p className="sentence">{sentence}</p>
        {lines.length > 0 && (
          <ul>
            {lines.map(([k, t, v]) => <li key={k}><Status value="x" tone={t} label={v} wrap /></li>)}
          </ul>
        )}
        {conf && (
          <p className="muted" style={{ margin: 0 }}>
            {demo ? "Demonstration only: compared with a research author's papers, not this student's writing."
              : `${conf[0].toUpperCase()}${conf.slice(1)} confidence. ${String(style!.details.confidence_reason ?? "")}`}
          </p>
        )}
        {(style?.status === "completed" || ai?.status === "completed") && (
          <p className="hint" style={{ margin: 0 }}>
            Both checks are experimental. A flag means a person should look; it is never a finding of misconduct.
          </p>
        )}
      </div>
      <div className="act-col">
        <h2>What next</h2>
        {flagged && onSendViva && <button className="btn" onClick={onSendViva}>Plan a viva</button>}
        {c?.status === "awaiting_review" && canManage && <Link href={`/check/${c.id}`} className="btn">Check the texts</Link>}
        {c?.status === "awaiting_baseline" && <p className="muted" style={{ margin: 0 }}>Upload more past essays from the course page.</p>}
        {!flagged && c?.status === "evidence_ready" && <p className="muted" style={{ margin: 0 }}>Nothing is needed. You can still plan a viva if you have other concerns.</p>}
        {c && canManage && c.status !== "analysing" && c.status !== "withdrawn" && (
          <button className="btn secondary" onClick={onReanalyse} disabled={busy}>{busy ? "Starting…" : "Run the checks again"}</button>
        )}
        {c && c.status !== "analysing" && <p className="hint" style={{ margin: 0 }}>Last updated {fmtDate(c.updated_at)}.</p>}
      </div>
    </section>
  );
}

function RunEvidence({ title, run, selectedKey, onShow }: {
  title: string; run: AnalysisRun; selectedKey: string | null; onShow: (key: string, spans: Span[]) => void;
}) {
  const d = run.details;
  const items = run.evidence.filter((e) => !METHOD_KINDS.has(e.kind));
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {(run.pipeline === "consistency" || run.pipeline === "interchangeability") && run.status === "completed" && (
          <Status value="x" tone="calm" label="Experimental" />
        )}
      </div>
      {run.status === "not_eligible" && (
        <>
          <p><strong>{notScoredTitle[String(d.website_status)] ?? "Not scored"}.</strong> {run.reason}</p>
          {d.quality && d.website_status !== "insufficient_evidence" && (
            <p className="muted small">
              This essay: {qualityLabel[d.quality.candidate] ?? d.quality.candidate}. Past essays:{" "}
              {qualityLabel[d.quality.references] ?? d.quality.references}.
            </p>
          )}
        </>
      )}
      {(run.status === "unavailable" || run.status === "failed") && <p className="muted">{run.reason ?? "This check didn't run."}</p>}
      {run.status === "completed" && items.length === 0 && (
        <p className="muted">{run.pipeline === "consistency" ? "No single feature stood out." : "Nothing notable."}</p>
      )}
      {items.map((ev) => {
        const key = `${run.id}:${ev.ordinal}`;
        return <Evidence key={key} ev={ev} on={selectedKey === key} onShow={() => onShow(key, ev.spans)} />;
      })}
    </section>
  );
}

function Evidence({ ev, on, onShow }: { ev: EvidenceItem; on: boolean; onShow: () => void }) {
  const hasNums = ev.observed !== null && ev.baseline_mean !== null;
  return (
    <div className={on ? "ev on" : "ev"}>
      <div className="ev-head">
        <h3>{heading(ev)}</h3>
        {ev.z_score !== null && <span className="muted small">{Math.abs(ev.z_score).toFixed(1)} spreads {ev.z_score > 0 ? "higher" : "lower"}</span>}
      </div>
      {hasNums && (
        <div className="nums">
          <div className="num this"><b>{num(ev.observed!)}</b><span>This essay</span></div>
          <div className="num"><b>{num(ev.baseline_mean!)}</b><span>Past essays{ev.baseline_sd !== null ? `, usually ± ${num(ev.baseline_sd)}` : ""}</span></div>
        </div>
      )}
      {hasNums ? (
        <details>
          <summary className="small">Exact finding</summary>
          <p className="exact">{ev.statement}</p>
        </details>
      ) : (
        <p className="exact" style={{ marginTop: 0 }}>{ev.statement}</p>
      )}
      {ev.spans.length > 0 && (
        <div>
          <button className="chip-btn" aria-pressed={on} onClick={onShow}>
            {on ? "Hide in essay" : `Show in essay (${plural(ev.spans.length, "passage")})`}
          </button>
        </div>
      )}
    </div>
  );
}

function EssayPanel({ text, selected, onOpen, onClose }: {
  text: string | null; selected: Selection; onOpen: () => void; onClose: () => void;
}) {
  if (text === null) {
    return (
      <section className="card essay">
        <div className="essay-empty">
          <h2 style={{ margin: 0 }}>The essay</h2>
          <p className="muted" style={{ margin: 0 }}>Open the essay to see the passages each finding refers to.</p>
          <button className="btn secondary" onClick={onOpen}>Show the essay</button>
          <p className="hint" style={{ margin: 0 }}>Opening the essay is recorded in the activity log.</p>
        </div>
      </section>
    );
  }
  const n = selected?.spans.length ?? 0;
  return (
    <section className="card essay">
      <div className="essay-head">
        <div>
          <h2 style={{ margin: 0, fontSize: 19 }}>The essay</h2>
          <span className="muted small">
            {n ? `${plural(n, "passage")} highlighted` : "Choose “Show in essay” on a finding"}
            {selected?.spans.some((s) => s.status === "approximate") && " (edges are approximate)"}
          </span>
        </div>
        <button className="btn secondary sm" onClick={onClose}>Hide the essay</button>
      </div>
      <HighlightedText text={text} spans={selected?.spans ?? []} />
      <p className="essay-foot">Text as Viskopic read it from the file. Opening it is recorded in the activity log.</p>
    </section>
  );
}

/** The model team's "either flags" summary, word for word. A routing hint for a person, never a finding. */
function CombinedDetails({ c }: { c: CombinedReport }) {
  return (
    <div>
      <h3>Combined result ({c.version})</h3>
      <p className="small">{c.summary}</p>
      {c.coverage_notes.length > 0 && <ul className="small muted">{c.coverage_notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      <p className="muted small">{c.interpretation_warning} Review is recommended when either check flags; scores are never averaged.</p>
    </div>
  );
}

function RunDetails({ run }: { run: AnalysisRun }) {
  const d = run.details;
  const method = run.evidence.filter((e) => METHOD_KINDS.has(e.kind));
  const scored = run.status === "completed" && run.score !== null;
  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <h3>{run.label}</h3>
      {scored && (
        <dl className="meta">
          <dt>Score</dt><dd className="mono">{formatScore(run.score)}</dd>
          <dt>Threshold</dt><dd className="mono">{formatScore(run.threshold)}</dd>
          {run.outcome && <><dt>Model label</dt><dd>{outcomeLabel[run.outcome] ?? run.outcome}</dd></>}
          {run.confidence && <><dt>Confidence</dt><dd>{run.confidence}{d.confidence_reason ? `: ${String(d.confidence_reason)}` : ""}</dd></>}
          {run.pipeline === "interchangeability" && Array.isArray(d.families) && <><dt>Comparison essays</dt><dd>{(d.families as string[]).join(", ")}</dd></>}
        </dl>
      )}
      {d.website_status === "scored_demo_unreviewed" && (
        <p className="note small">Unreviewed demonstration: nobody has checked the language and extraction of these texts.</p>
      )}
      {method.map((e) => <p key={e.ordinal} className="exact">{e.statement}</p>)}
      {d.measurements && <Measurements m={d.measurements} />}
      {d.interpretation_warning && <p className="muted small">{d.interpretation_warning}</p>}
      <p className="muted small">{run.pipeline_version}, {fmtDate(run.created_at)}</p>
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
      <dl className="meta">
        {rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd className="mono">{v}</dd></Fragment>)}
      </dl>
      <p className="muted small">
        The centred score is not a percentage or a probability, and can be negative. Distances are descriptive and
        don&apos;t change the label.
      </p>
    </>
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
    <div className="essay-body" ref={ref} tabIndex={0}>
      {parts.map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <Fragment key={i}>{p.text}</Fragment>))}
    </div>
  );
}
