"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorNote, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { CaseList, CaseRow, Course, NextStep } from "@/lib/types";
import { aiSentence, confidenceSentence, plural, shortDate, styleSentence } from "@/lib/words";

/** The home page: cases grouped by what a person should do next. */
export default function Home() {
  const { me } = useAuth();
  const router = useRouter();
  const operator = me!.kind === "platform_operator";
  useEffect(() => {
    if (operator) router.replace("/platform");
  }, [operator, router]);

  const [course, setCourse] = useState<string>("");
  const list = useResource<CaseList>(operator ? null : `/cases${course ? `?course_id=${course}` : ""}`, 10000);
  const courses = useResource<Course[]>(operator ? null : "/courses");

  const by = useMemo(() => {
    const g: Partial<Record<NextStep, CaseRow[]>> = {};
    for (const c of list.data?.cases ?? []) (g[c.next_step] ??= []).push(c);
    return g;
  }, [list.data]);

  if (operator) return null;
  if (me!.roles.length === 0) {
    return (
      <section className="card narrow">
        <h1>You don&apos;t have a role yet</h1>
        <p className="lede">Ask your department&apos;s admin or your university&apos;s owner to give you access to a course.</p>
      </section>
    );
  }

  const n = (k: NextStep) => by[k]?.length ?? 0;
  const needYou = n("review_evidence") + n("check_texts") + n("rate_viva") + n("viva_problem") + n("failed");
  const waiting = [...(by.viva_waiting ?? []), ...(by.await_baseline ?? []), ...(by.analysing ?? [])];
  const quiet = [...(by.no_flag ?? []), ...(by.viva_rated ?? []), ...(by.withdrawn ?? [])];
  const parts = [
    n("review_evidence") && `${plural(n("review_evidence"), "essay")} ${n("review_evidence") === 1 ? "looks" : "look"} unusual`,
    n("check_texts") && `${plural(n("check_texts"), "case")} ${n("check_texts") === 1 ? "needs" : "need"} texts checked`,
    n("rate_viva") && `${plural(n("rate_viva"), "viva")} ${n("rate_viva") === 1 ? "is" : "are"} ready to rate`,
  ].filter(Boolean) as string[];

  return (
    <div className="stack">
      <section className="page-header" style={{ marginBottom: 0 }}>
        <div className="stack-sm" style={{ maxWidth: 760 }}>
          <h1>
            {list.data === null ? "Cases" : needYou === 0 ? "Nothing needs you right now" : `${plural(needYou, "case")} ${needYou === 1 ? "needs" : "need"} you`}
          </h1>
          {list.data && (
            <p className="lede">
              {parts.length > 0 ? `${parts.join(". ")}.` : "New essays appear here once they've been read and checked."}
              {waiting.length > 0 && ` ${plural(waiting.length, "more case")} ${waiting.length === 1 ? "is" : "are"} waiting for someone else.`}
            </p>
          )}
        </div>
        <Link href="/courses" className="btn secondary">Upload essays</Link>
      </section>

      {(courses.data?.length ?? 0) > 1 && (
        <div role="group" aria-label="Show cases from" className="chips" style={{ alignItems: "center" }}>
          <span className="label" style={{ margin: "0 6px 0 0" }}>Show</span>
          <button className={course === "" ? "chip-btn on" : "chip-btn"} aria-pressed={course === ""} onClick={() => setCourse("")}>All my courses</button>
          {courses.data!.map((c) => (
            <button key={c.id} className={course === c.id ? "chip-btn on" : "chip-btn"} aria-pressed={course === c.id}
              onClick={() => setCourse(c.id)}>{c.code}</button>
          ))}
        </div>
      )}

      <ErrorNote error={list.error} />

      <Group title="Look at the evidence" hint="A check flagged these. You decide whether a viva is needed." rows={by.review_evidence} />
      <Group title="Check the text was read correctly" hint="The writing style check only runs on texts a person has checked." rows={by.check_texts} />
      <Group title="Rate a viva" hint="The student has answered. Read the answers and rate each one." rows={by.rate_viva} />
      <Group title="A viva needs your attention" hint="The email didn't arrive, or the link ran out before the student started." rows={by.viva_problem} />
      <Group title="Something went wrong" hint="The checks couldn't finish. Open the case to run them again." rows={by.failed} />
      <Group title="Waiting for someone else" hint="Nothing for you to do yet." rows={waiting} />

      {quiet.length > 0 && (
        <details className="card">
          <summary style={{ fontSize: "var(--fs-md)" }}>No flag, rated or closed ({quiet.length})</summary>
          <div style={{ marginTop: 12 }}>
            {quiet.map((c) => <Row key={c.id} c={c} />)}
          </div>
        </details>
      )}

      {list.data && list.data.cases.length === 0 && (
        <section className="card stack-sm">
          <h2>No essays are being checked yet</h2>
          <p className="lede">Upload students&apos; past essays first, so the writing style check has something to compare with. Then upload the essays you want checked.</p>
          <div><Link href="/courses" className="btn">Choose a course</Link></div>
        </section>
      )}

      <p className="hint" style={{ maxWidth: "70ch" }}>
        A flag means a person should look. It is never a finding of misconduct, and nothing here records an outcome.
      </p>
    </div>
  );
}

function Group({ title, hint, rows }: { title: string; hint: string; rows?: CaseRow[] }) {
  if (!rows?.length) return null;
  return (
    <section className="group">
      <div className="group-head"><h2>{title}</h2><p>{hint}</p></div>
      {rows.map((c) => <Row key={c.id} c={c} />)}
    </section>
  );
}

function Row({ c }: { c: CaseRow }) {
  const essay = c.assignment_title ?? c.original_filename;
  const [line, action] = describe(c);
  return (
    <div className="row">
      <div className="grow">
        <span className="who">{c.student_pseudonym}</span>{" "}
        <span className="muted">{c.viva ? "has a viva on" : "wrote"} {essay}, {c.course_code}</span>
        <p>{line}</p>
      </div>
      {action}
    </div>
  );
}

function describe(c: CaseRow): [React.ReactNode, React.ReactNode] {
  const open = (label: string, primary = true, href = `/documents/${c.document_id}`) =>
    <Link href={href} className={primary ? "btn" : "btn secondary"}>{label}</Link>;
  const style = styleSentence(c.consistency);
  const ai = aiSentence(c.ai_comparison);
  const conf = confidenceSentence(c);
  switch (c.next_step) {
    case "review_evidence": {
      const first = style.flagged ? style : ai;
      const other = style.flagged ? ai : style;
      return [<><Status value="x" tone="attn" label={first.text} wrap /> {other.text} {conf}</>, open("Open case")];
    }
    case "check_texts":
      return [<><Status value="x" tone="act" label={`${plural(c.texts_to_check, "text")} to check`} />{" "}
        {c.texts_to_check > 1 ? "The essay and the student's past essays." : ""} About 2 minutes each.</>,
        open(`Check ${plural(c.texts_to_check, "text")}`, true, `/check/${c.id}`)];
    case "rate_viva":
      return [<><Status value="x" tone="act" label={`${c.viva!.questions} answers, ${c.viva!.rated} rated.`} />{" "}
        Finished {shortDate(c.viva!.submitted_at)}.</>, open(c.viva!.rated ? "Continue rating" : "Rate the answers", true, `/vivas/${c.viva!.id}`)];
    case "viva_problem":
      return [<Status value="x" tone="err" label={c.viva!.status === "expired" ? "The link expired before the student started." : "The invitation email couldn't be delivered."} wrap />,
        open("See the invitation", true, `/documents/${c.document_id}?tab=viva`)];
    case "viva_waiting": {
      const v = c.viva!;
      const where = v.started_at ? "Started, not finished." : v.opened_at ? `Opened the link ${shortDate(v.opened_at)}, not started.` : "Not opened yet.";
      return [<><Status value="x" tone="calm" label={where} /> Due {shortDate(v.deadline_at)}.</>,
        open("See the invitation", false, `/documents/${c.document_id}?tab=viva`)];
    }
    case "await_baseline":
      return [<><Status value="x" tone="calm" label="Waiting for more past essays." /> {ai.text}</>,
        open("See the student", false, `/students/${c.student_id}`)];
    case "analysing":
      return [<Status value="x" tone="calm" label="The checks are running." />, open("Open case", false)];
    case "failed":
      return [<Status value="x" tone="err" label="The checks couldn't finish." />, open("Open case")];
    case "withdrawn":
      return [<Status value="x" tone="calm" label="Withdrawn: the essay was removed." />, null];
    case "viva_rated":
      return [<Status value="x" tone="ok" label="Viva rated." />, open("Open case", false)];
    default:
      return [<>{style.text} {ai.text}</>, open("Open case", false)];
  }
}
