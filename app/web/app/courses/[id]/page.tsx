"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { Assignment, Course, DocumentRow, Student } from "@/lib/types";
import { plural, shortDate } from "@/lib/words";

type Tab = "essays" | "students" | "assignments";

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>();
  const { me, api } = useAuth();
  const course = useResource<Course>(`/courses/${id}`);
  const students = useResource<Student[]>(`/courses/${id}/enrolments`);
  // Polls while AI comparison essays are being generated for an assignment.
  const assignments = useResource<Assignment[]>(`/courses/${id}/assignments`, 5000);
  const docs = useResource<DocumentRow[]>(`/documents?course_id=${id}`, 4000);
  const [tab, setTab] = useState<Tab>("essays");
  const [uploading, setUploading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (course.error) return <ErrorNote error={course.error} />;
  if (!course.data) return <p className="muted">Loading…</p>;
  const c = course.data;
  const manage = c.access === "manage";
  const open = c.status === "active";
  const canArchive =
    me!.permissions.is_owner || (me!.permissions.admin_department_ids ?? []).includes(c.department_id);
  const activeStudents = (students.data ?? []).filter((s) => s.enrolment_status === "active" && s.status === "active");

  async function run(fn: () => Promise<unknown>, after: () => void) {
    setActionError(null);
    try {
      await fn();
      after();
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  return (
    <>
      <Link href="/courses" className="back">← Courses</Link>
      <PageHeader title={`${c.code} ${c.title}`} meta={c.department_name}>
        {!open && <Status value={c.status} />}
        {manage && open && <button className="btn" onClick={() => setUploading(true)}>Upload essays</button>}
      </PageHeader>
      <ErrorNote error={actionError} />
      {!open && <p className="note">This course is archived. You can&apos;t add students or upload essays.</p>}

      <nav className="tabs" aria-label="Course sections">
        {(["essays", "students", "assignments"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} aria-current={tab === t ? "page" : undefined} onClick={() => setTab(t)}>
            {t === "essays" ? `Essays${docs.data ? ` (${docs.data.length})` : ""}`
              : t === "students" ? `Students${students.data ? ` (${activeStudents.length})` : ""}`
              : "Assignments"}
          </button>
        ))}
      </nav>

      {tab === "essays" && <Essays docs={docs.data} error={docs.error} />}
      {tab === "students" && (
        <Students c={c} manage={manage && open} list={students.data} error={students.error} reload={students.reload} run={run} />
      )}
      {tab === "assignments" && (
        <Assignments courseId={id} manage={manage} list={assignments.data} reload={assignments.reload} />
      )}

      {canArchive && (
        <p style={{ marginTop: 32 }}>
          <button className="link" onClick={() => run(
            () => api(`/courses/${id}`, { method: "PATCH", body: JSON.stringify({ status: open ? "archived" : "active" }) }),
            course.reload,
          )}>{open ? "Archive this course" : "Restore this course"}</button>
        </p>
      )}

      {uploading && (
        <UploadDrawer
          courseId={id}
          students={activeStudents}
          assignments={assignments.data ?? []}
          onClose={() => setUploading(false)}
          onUploaded={docs.reload}
        />
      )}
    </>
  );
}

function Essays({ docs, error }: { docs: DocumentRow[] | null; error: string | null }) {
  const [filter, setFilter] = useState<"all" | "submission" | "baseline">("all");
  const rows = (docs ?? []).filter((d) => filter === "all" || d.role === filter);
  return (
    <section className="group">
      <div className="group-head">
        <h2>Essays</h2>
        <div className="chips">
          {(["all", "submission", "baseline"] as const).map((f) => (
            <button key={f} className={filter === f ? "chip-btn on" : "chip-btn"} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "submission" ? "Being checked" : "Past essays"}
            </button>
          ))}
        </div>
      </div>
      <div style={{ padding: "0 28px" }}><ErrorNote error={error} /></div>
      {docs && rows.length === 0 && <div className="row"><Empty>No essays here yet.</Empty></div>}
      {rows.map((d) => (
        <div className="row" key={d.id}>
          <div className="grow">
            <span className="who">{d.student_pseudonym}</span>{" "}
            <span className="muted">{d.role === "submission" ? "essay being checked" : "past essay"}
              {d.provenance === "supervised" ? ", written in an exam" : ""}</span>
            <p>
              <Link href={`/documents/${d.id}`} className="link">{d.assignment_title ?? d.original_filename}</Link>
              <span className="muted"> · {d.word_count ? `${d.word_count.toLocaleString()} words · ` : ""}uploaded {shortDate(d.uploaded_at)}</span>
            </p>
            {d.last_error && <p className="error small">{d.last_error}</p>}
          </div>
          <Status value={d.status} />
        </div>
      ))}
    </section>
  );
}

function Students({ c, manage, list, error, reload, run }: {
  c: Course; manage: boolean; list: Student[] | null; error: string | null; reload: () => void;
  run: (fn: () => Promise<unknown>, after: () => void) => Promise<void>;
}) {
  const { api } = useAuth();
  async function addStudent(f: FormData) {
    await api("/students", {
      method: "POST",
      body: JSON.stringify({ external_ref: f.get("external_ref"), display_name: f.get("display_name") || null, enrol_course_id: c.id }),
    });
    reload();
  }
  async function enrolExisting(f: FormData) {
    const s = await api<Student>(`/students/lookup?ref=${encodeURIComponent(String(f.get("ref")))}`);
    await api(`/courses/${c.id}/enrolments`, { method: "POST", body: JSON.stringify({ student_id: s.id }) });
    reload();
  }
  return (
    <div className="split">
      <section className="group main-col">
        <div className="group-head"><h2>Students</h2><p>Past essays count toward each student&apos;s writing style baseline.</p></div>
        <div style={{ padding: "0 28px" }}><ErrorNote error={error} /></div>
        {list?.length === 0 && <div className="row"><Empty>No students enrolled.</Empty></div>}
        {list?.map((s) => (
          <div className="row" key={s.id}>
            <div className="grow">
              <Link href={`/students/${s.id}`} className="who link">{s.pseudonym}</Link>
              <span className="sub">{s.display_name ?? s.external_ref} · {plural(s.baseline_docs ?? 0, "past essay")}</span>
            </div>
            {s.enrolment_status !== "active" && <Status value={s.enrolment_status ?? "withdrawn"} />}
            {manage && (
              <button className="link" onClick={() => run(
                () => api(`/enrolments/${s.enrolment_id}`, { method: "PATCH", body: JSON.stringify({ status: s.enrolment_status === "active" ? "withdrawn" : "active" }) }),
                reload,
              )}>{s.enrolment_status === "active" ? "Remove from course" : "Re-enrol"}</button>
            )}
          </div>
        ))}
      </section>
      {manage && (
        <aside className="side-col">
          <section className="card">
            <h2>Add a student</h2>
            <ActionForm onSubmit={addStudent} submitLabel="Add student">
              <Field label="University student number"><input name="external_ref" required /></Field>
              <Field label="Name (optional)" hint="Stored encrypted. Everywhere else the student appears as a pseudonym.">
                <input name="display_name" />
              </Field>
            </ActionForm>
          </section>
          <section className="card">
            <h2>Already in Viskopic?</h2>
            <ActionForm onSubmit={enrolExisting} submitLabel="Find and add">
              <Field label="Student number"><input name="ref" required /></Field>
            </ActionForm>
          </section>
        </aside>
      )}
    </div>
  );
}

function Assignments({ courseId, manage, list, reload }: { courseId: string; manage: boolean; list: Assignment[] | null; reload: () => void }) {
  const { api } = useAuth();
  async function addAssignment(f: FormData) {
    await api(`/courses/${courseId}/assignments`, {
      method: "POST",
      body: JSON.stringify({ title: f.get("title"), genre: f.get("genre") || null, prompt: f.get("prompt") || null }),
    });
    reload();
  }
  return (
    <div className="split">
      <section className="card main-col">
        <h2>Assignments</h2>
        <p className="muted">The AI comparison check needs the assignment question. Only the question is sent to write comparison essays, never student work.</p>
        {list?.length === 0 && <Empty>No assignments yet.</Empty>}
        {list?.map((a) => (
          <div key={a.id} className="assignment">
            <strong>{a.title}</strong> {a.genre && <span className="chip">{a.genre.replaceAll("_", " ")}</span>}
            {a.prompt ? <p className="muted small">{a.prompt}</p> : <p className="muted small">No question added, so the AI comparison check can&apos;t run.</p>}
            <ComparisonSet a={a} manage={manage} onChange={reload} />
          </div>
        ))}
      </section>
      {manage && (
        <aside className="side-col card">
          <h2>Add an assignment</h2>
          <ActionForm onSubmit={addAssignment} submitLabel="Add assignment">
            <Field label="Title"><input name="title" required /></Field>
            <Field label="Kind of writing">
              <select name="genre" defaultValue="essay">
                {["essay", "report", "lab_report", "reflection", "dissertation", "other"].map((g) => (
                  <option key={g} value={g}>{g.charAt(0).toUpperCase() + g.slice(1).replaceAll("_", " ")}</option>
                ))}
              </select>
            </Field>
            <Field label="Assignment question" hint="The question students answered, word for word.">
              <textarea name="prompt" rows={4} />
            </Field>
          </ActionForm>
        </aside>
      )}
    </div>
  );
}

const comparisonLabel: Record<string, string> = {
  queued: "AI comparison essays queued",
  building: "Writing AI comparison essays",
  ready: "AI comparison essays ready",
  failed: "AI comparison essays failed",
};

/** Pipeline 2's comparison essays for one assignment: status, and (re)generate for managers. */
function ComparisonSet({ a, manage, onChange }: { a: Assignment; manage: boolean; onChange: () => void }) {
  const { api } = useAuth();
  const [error, setError] = useState<string | null>(null);
  if (!a.prompt) return null;
  const st = a.comparison_status;
  async function build() {
    setError(null);
    try {
      await api(`/assignments/${a.id}/comparison-set`, { method: "POST" });
      onChange();
    } catch (e) { setError((e as Error).message); }
  }
  return (
    <p className="small comparison-set">
      {st ? <Status value={st} label={comparisonLabel[st] ?? st} /> : "No AI comparison essays yet"}
      {st === "ready" && a.comparison_families && <span className="muted"> ({a.comparison_families.join(", ")})</span>}
      {st === "failed" && a.comparison_error && <span className="muted"> · {a.comparison_error}</span>}
      {manage && st !== "queued" && st !== "building" && (
        <> · <button className="link" onClick={build}>{st === "ready" ? "Write them again" : "Write them now"}</button></>
      )}
      <ErrorNote error={error} />
    </p>
  );
}

type Pending = {
  key: string; file: File; student_id: string; role: "baseline" | "submission";
  provenance: "supervised" | "unsupervised"; assignment_id: string;
  state: "ready" | "sending" | "done" | "error"; error?: string;
};

/** Upload several files at once; each becomes one POST /documents. */
function UploadDrawer({ courseId, students, assignments, onClose, onUploaded }: {
  courseId: string; students: Student[]; assignments: Assignment[]; onClose: () => void; onUploaded: () => void;
}) {
  const { api } = useAuth();
  const [files, setFiles] = useState<Pending[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);

  function add(list: FileList | null) {
    if (!list) return;
    const next = Array.from(list).map((file, i): Pending => ({
      key: `${Date.now()}-${i}-${file.name}`, file, student_id: students.length === 1 ? students[0].id : "",
      role: "baseline", provenance: "unsupervised", assignment_id: "", state: "ready",
    }));
    setFiles((f) => [...f, ...next]);
  }
  const set = (key: string, patch: Partial<Pending>) => setFiles((f) => f.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  const waiting = files.filter((p) => p.state === "ready" || p.state === "error");
  const missing = waiting.some((p) => !p.student_id);

  async function send() {
    setBusy(true);
    for (const p of waiting) {
      set(p.key, { state: "sending", error: undefined });
      const f = new FormData();
      f.set("file", p.file);
      f.set("course_id", courseId);
      f.set("student_id", p.student_id);
      f.set("role", p.role);
      f.set("provenance", p.provenance);
      if (p.assignment_id) f.set("assignment_id", p.assignment_id);
      try {
        await api("/documents", { method: "POST", body: f });
        set(p.key, { state: "done" });
      } catch (e) {
        set(p.key, { state: "error", error: (e as Error).message });
      }
    }
    setBusy(false);
    onUploaded();
  }

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <div className="card-head" style={{ marginBottom: 0 }}>
          <h2 id="upload-title">Upload essays</h2>
          <button className="link" onClick={onClose} disabled={busy}>Close</button>
        </div>
        {students.length === 0 ? (
          <Empty>Add a student to this course first.</Empty>
        ) : (
          <>
            <label className={over ? "dropzone over" : "dropzone"}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}>
              <strong>Drop files here, or choose them</strong>
              <span className="muted small">PDF, Word (.docx) or plain text, up to 10 MB each.</span>
              <input type="file" multiple accept=".pdf,.docx,.txt" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
            </label>
            <ul className="file-list">
              {files.map((p) => (
                <li key={p.key} className="file-card">
                  <h3 className="full">{p.file.name}</h3>
                  {p.state === "done" ? <p className="full"><Status value="x" tone="ok" label="Uploaded. It's being read now." /></p> : (
                    <>
                      <Field label="Student">
                        <select value={p.student_id} onChange={(e) => set(p.key, { student_id: e.target.value })} disabled={busy}>
                          <option value="">Choose…</option>
                          {students.map((s) => <option key={s.id} value={s.id}>{s.pseudonym} ({s.display_name ?? s.external_ref})</option>)}
                        </select>
                      </Field>
                      <Field label="This is">
                        <select value={p.role} onChange={(e) => set(p.key, { role: e.target.value as Pending["role"] })} disabled={busy}>
                          <option value="baseline">A past essay</option>
                          <option value="submission">An essay to check</option>
                        </select>
                      </Field>
                      <Field label="Written">
                        <select value={p.provenance} onChange={(e) => set(p.key, { provenance: e.target.value as Pending["provenance"] })} disabled={busy}>
                          <option value="unsupervised">As coursework</option>
                          <option value="supervised">In an exam</option>
                        </select>
                      </Field>
                      <Field label="Assignment">
                        <select value={p.assignment_id} onChange={(e) => set(p.key, { assignment_id: e.target.value })} disabled={busy}>
                          <option value="">None</option>
                          {assignments.map((a) => <option key={a.id} value={a.id}>{a.title}</option>)}
                        </select>
                      </Field>
                      {p.state === "error" && <p className="full error small">{p.error}</p>}
                      {p.state === "sending" && <p className="full muted small">Uploading…</p>}
                      {p.state !== "sending" && (
                        <button className="link full" style={{ justifySelf: "start" }} disabled={busy}
                          onClick={() => setFiles((f) => f.filter((x) => x.key !== p.key))}>Remove from list</button>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
            <p className="hint">Essays written in an exam make the writing style check more confident.</p>
            <button className="btn wide" disabled={busy || waiting.length === 0 || missing} onClick={send}>
              {busy ? "Uploading…" : waiting.length ? `Upload ${plural(waiting.length, "file")}` : "Upload"}
            </button>
            {missing && <p className="hint">Choose a student for every file.</p>}
          </>
        )}
      </aside>
    </div>
  );
}
