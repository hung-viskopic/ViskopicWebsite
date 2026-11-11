"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ErrorNote, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { CaseTexts } from "@/lib/types";
import { plural } from "@/lib/words";

const languages: [string, string, string][] = [
  ["english", "Yes, English prose", "Sentences and paragraphs in English."],
  ["non_english", "No, another language", "Most of it isn't English."],
  ["mixed", "Mixed languages", "Large parts in more than one language."],
  ["uncertain", "I'm not sure", ""],
];

/**
 * A focused page for checking that each text in a case was read correctly.
 * Each saved check is bound on the server to the exact stored text; nothing here can create one from a browser flag alone.
 */
export default function CheckTexts() {
  const { caseId } = useParams<{ caseId: string }>();
  const { api } = useAuth();
  const router = useRouter();
  const res = useResource<CaseTexts>(`/cases/${caseId}/texts`);
  const [index, setIndex] = useState(0);
  const [text, setText] = useState<string | null>(null);
  const [language, setLanguage] = useState("");
  const [extraction, setExtraction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const docs = res.data?.documents ?? [];
  const doc = docs[index];

  useEffect(() => {
    if (!res.data) return;
    const first = res.data.documents.findIndex((d) => d.needs_check);
    setIndex(first >= 0 ? first : 0);
  }, [res.data]);

  useEffect(() => {
    if (!doc) return;
    setText(null);
    setLanguage("");
    setExtraction("");
    api<{ text: string }>(`/documents/${doc.id}/text`).then((t) => setText(t.text)).catch((e: Error) => setError(e.message));
  }, [doc, api]);

  if (res.error) return <main className="main"><ErrorNote error={res.error} /></main>;
  if (!res.data) return <main className="main muted">Loading…</main>;
  const c = res.data.case;
  const left = docs.filter((d) => d.needs_check).length;
  const done = docs.length - left;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`/documents/${doc.id}/quality-review`, { method: "POST", body: JSON.stringify({ language, extraction }) });
      const after = await api<CaseTexts>(`/cases/${caseId}/texts`);
      res.reload();
      const next = after.documents.findIndex((d, i) => d.needs_check && i !== index);
      if (next < 0) router.push(`/documents/${c.document_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="task-bar">
        <div className="task-bar-in">
          <div>
            <strong>Check texts for {c.student_pseudonym}</strong>
            <span className="muted"> · {c.assignment_title ?? c.original_filename}</span>
          </div>
          <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
            <span className="muted">{done} of {docs.length} checked</span>
            <div className="progress" style={{ width: 160 }}><span style={{ width: `${docs.length ? (done / docs.length) * 100 : 0}%` }} /></div>
            <Link href={`/documents/${c.document_id}`} className="btn secondary sm">Stop for now</Link>
          </div>
        </div>
      </div>
      <main className="main wide">
        {docs.length === 0 ? (
          <section className="card narrow"><h1>Nothing to check</h1><p className="lede">None of these texts is long enough for the writing style check.</p></section>
        ) : (
          <div className="split">
            <div className="main-col stack-sm">
              <div className="card-head" style={{ marginBottom: 0 }}>
                <h1 style={{ fontSize: 26, margin: 0 }}>
                  {doc.role === "submission" ? "The essay being checked" : "Past essay"}: {doc.original_filename}
                </h1>
                <span className="muted">{doc.word_count.toLocaleString()} words{doc.provenance === "supervised" ? " · written in an exam" : ""}</span>
              </div>
              <div className="check-text" tabIndex={0} aria-label="Text as Viskopic read it">
                {text ?? "Loading the text…"}
              </div>
              <p className="hint">This is the text exactly as Viskopic read it from the file. Opening it is recorded in the activity log.</p>
            </div>
            <aside className="side-col sticky stack-sm">
              <section className="card stack-sm" style={{ marginBottom: 0 }}>
                {doc.review && !doc.needs_check && (
                  <Status value="x" tone="ok" label="Already checked" />
                )}
                {doc.review && !doc.review.current && <p className="note">The text changed since it was last checked. Check it again.</p>}
                <fieldset className="options">
                  <legend>Is it written in English?</legend>
                  {languages.map(([v, l, h]) => (
                    <label key={v} className="option">
                      <input type="radio" name="language" value={v} checked={language === v} onChange={() => setLanguage(v)} />
                      <span>{l}{h && <small>{h}</small>}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className="options">
                  <legend>Did the text come through intact?</legend>
                  <label className="option">
                    <input type="radio" name="extraction" value="pass" checked={extraction === "pass"} onChange={() => setExtraction("pass")} />
                    <span>Yes, it reads normally<small>No garbled characters or missing pages.</small></span>
                  </label>
                  <label className="option">
                    <input type="radio" name="extraction" value="fail" checked={extraction === "fail"} onChange={() => setExtraction("fail")} />
                    <span>No, something is wrong<small>Garbled text, missing pages, or tables and references mixed into the prose.</small></span>
                  </label>
                </fieldset>
                <ErrorNote error={error} />
                <button className="btn wide" disabled={busy || !language || !extraction || text === null} onClick={save}>
                  {busy ? "Saving…" : left > (doc.needs_check ? 1 : 0) ? "Save and go to the next text" : "Save and finish"}
                </button>
                <p className="hint">{plural(left, "text")} left to check.</p>
              </section>
              {docs.length > 1 && (
                <nav className="qnav" aria-label="Texts in this case">
                  {docs.map((d, i) => (
                    <button key={d.id} className={i === index ? "on" : ""} onClick={() => setIndex(i)}>
                      <b className={d.needs_check ? "open" : ""}>{i + 1}</b>
                      <span>{d.role === "submission" ? "Essay being checked" : "Past essay"}<small>{d.original_filename}</small></span>
                    </button>
                  ))}
                </nav>
              )}
            </aside>
          </div>
        )}
      </main>
    </>
  );
}
