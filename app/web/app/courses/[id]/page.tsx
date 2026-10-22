"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { Assignment, Course, DocumentRow, Student } from "@/lib/types";

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>();
  const { me, api } = useAuth();
  const course = useResource<Course>(`/courses/${id}`);
  const students = useResource<Student[]>(`/courses/${id}/enrolments`);
  const assignments = useResource<Assignment[]>(`/courses/${id}/assignments`);
  const docs = useResource<DocumentRow[]>(`/documents?course_id=${id}`, 4000);
  const [actionError, setActionError] = useState<string | null>(null);

  if (course.error) return <ErrorNote error={course.error} />;
  if (!course.data) return <p className="muted">Loading…</p>;
  const c = course.data;
  const manage = c.access === "manage";
  const canArchive =
    me!.permissions.is_owner || (me!.permissions.admin_department_ids ?? []).includes(c.department_id);
  const activeStudents = (students.data ?? []).filter((s) => s.enrolment_status === "active" && s.status === "active");

  async function addStudent(f: FormData) {
    await api("/students", {
      method: "POST",
      body: JSON.stringify({ external_ref: f.get("external_ref"), display_name: f.get("display_name") || null, enrol_course_id: id }),
    });
    students.reload();
  }

  async function enrolExisting(f: FormData) {
    const s = await api<Student>(`/students/lookup?ref=${encodeURIComponent(String(f.get("ref")))}`);
    await api(`/courses/${id}/enrolments`, { method: "POST", body: JSON.stringify({ student_id: s.id }) });
    students.reload();
  }

  async function addAssignment(f: FormData) {
    await api(`/courses/${id}/assignments`, {
      method: "POST",
      body: JSON.stringify({ title: f.get("title"), genre: f.get("genre") || null, prompt: f.get("prompt") || null }),
    });
    assignments.reload();
  }

  async function upload(f: FormData) {
    if (!f.get("assignment_id")) f.delete("assignment_id");
    f.set("course_id", id);
    await api("/documents", { method: "POST", body: f });
    docs.reload();
  }

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
      <PageHeader title={`${c.code} ${c.title}`} meta={c.department_name}>
        <Status value={c.status} />
        {canArchive && (
          <button
            className="btn secondary"
            onClick={() =>
              run(
                () => api(`/courses/${id}`, { method: "PATCH", body: JSON.stringify({ status: c.status === "active" ? "archived" : "active" }) }),
                course.reload,
              )
            }
          >
            {c.status === "active" ? "Archive course" : "Restore course"}
          </button>
        )}
      </PageHeader>
      <ErrorNote error={actionError} />
      {c.status !== "active" && <p className="note">This course is archived. New students and uploads are blocked.</p>}

      {manage && c.status === "active" && (
        <section className="card">
          <h2>Upload a document</h2>
          <p className="muted small">
            Baseline is the student&apos;s own past writing. Supervised work (exam conditions) carries more weight.
            PDF, DOCX or TXT, up to 10 MB.
          </p>
          {activeStudents.length === 0 ? (
            <Empty>Enrol a student first.</Empty>
          ) : (
            <ActionForm onSubmit={upload} submitLabel="Upload" inline>
              <Field label="Student">
                <select name="student_id" required>
                  {activeStudents.map((s) => (
                    <option key={s.id} value={s.id}>{s.pseudonym} ({s.display_name ?? s.external_ref})</option>
                  ))}
                </select>
              </Field>
              <Field label="Type">
                <select name="role" required defaultValue="baseline">
                  <option value="baseline">Baseline (past work)</option>
                  <option value="submission">Submission to check</option>
                </select>
              </Field>
              <Field label="Written">
                <select name="provenance" required defaultValue="unsupervised">
                  <option value="unsupervised">Unsupervised (coursework)</option>
                  <option value="supervised">Supervised (exam conditions)</option>
                </select>
              </Field>
              <Field label="Assignment (optional)">
                <select name="assignment_id" defaultValue="">
                  <option value="">None</option>
                  {assignments.data?.map((a) => <option key={a.id} value={a.id}>{a.title}</option>)}
                </select>
              </Field>
              <Field label="File"><input type="file" name="file" required accept=".pdf,.docx,.txt" /></Field>
            </ActionForm>
          )}
        </section>
      )}

      <section className="card">
        <h2>Documents</h2>
        <ErrorNote error={docs.error} />
        {docs.data?.length === 0 && <Empty>No documents yet.</Empty>}
        {docs.data && docs.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>File</th><th>Student</th><th>Type</th><th>Written</th><th>Words</th><th>Status</th><th>Uploaded</th></tr></thead>
              <tbody>
                {docs.data.map((d) => (
                  <tr key={d.id}>
                    <td><Link href={`/documents/${d.id}`}>{d.original_filename}</Link>{d.assignment_title && <span className="sub">{d.assignment_title}</span>}</td>
                    <td><Link href={`/students/${d.student_id}`}>{d.student_pseudonym}</Link></td>
                    <td className="cap">{d.role}</td>
                    <td className="cap">{d.provenance}</td>
                    <td>{d.word_count?.toLocaleString() ?? "-"}</td>
                    <td><Status value={d.status} />{d.last_error && <><br /><small className="error">{d.last_error}</small></>}</td>
                    <td>{fmtDate(d.uploaded_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid">
        <section className="card">
          <h2>Students</h2>
          <ErrorNote error={students.error} />
          {students.data?.length === 0 && <Empty>No students enrolled.</Empty>}
          {students.data && students.data.length > 0 && (
            <div className="table-wrap">
            <table>
              <thead><tr><th>Student</th><th>Baseline</th><th>Enrolment</th>{manage && <th />}</tr></thead>
              <tbody>
                {students.data.map((s) => (
                  <tr key={s.id}>
                    <td><Link href={`/students/${s.id}`}>{s.pseudonym}</Link><span className="sub">{s.display_name ?? s.external_ref}</span></td>
                    <td>{s.baseline_docs} essays</td>
                    <td><Status value={s.enrolment_status ?? "active"} /></td>
                    {manage && (
                      <td>
                        <button
                          className="link"
                          onClick={() =>
                            run(
                              () => api(`/enrolments/${s.enrolment_id}`, { method: "PATCH", body: JSON.stringify({ status: s.enrolment_status === "active" ? "withdrawn" : "active" }) }),
                              students.reload,
                            )
                          }
                        >
                          {s.enrolment_status === "active" ? "Withdraw" : "Re-enrol"}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
          {manage && c.status === "active" && (
            <>
              <h3 className="section-gap">Add a new student</h3>
              <ActionForm onSubmit={addStudent} submitLabel="Add and enrol">
                <Field label="University student number"><input name="external_ref" required /></Field>
                <Field label="Name (optional)" hint="Stored encrypted. Shown as a pseudonym elsewhere.">
                  <input name="display_name" />
                </Field>
              </ActionForm>
              <h3 className="section-gap">Enrol an existing student</h3>
              <ActionForm onSubmit={enrolExisting} submitLabel="Find and enrol" inline>
                <Field label="Student number"><input name="ref" required /></Field>
              </ActionForm>
            </>
          )}
        </section>

        <section className="card">
          <h2>Assignments</h2>
          {assignments.data?.length === 0 && <Empty>No assignments yet.</Empty>}
          {assignments.data?.map((a) => (
            <div key={a.id} className="assignment">
              <strong>{a.title}</strong> {a.genre && <span className="chip">{a.genre.replaceAll("_", " ")}</span>}
              {a.prompt && <p className="muted small">{a.prompt}</p>}
            </div>
          ))}
          {manage && (
            <>
              <h3 className="section-gap">New assignment</h3>
              <ActionForm onSubmit={addAssignment} submitLabel="Add assignment">
                <Field label="Title"><input name="title" required /></Field>
                <Field label="Genre">
                  <select name="genre" defaultValue="essay">
                    {["essay", "report", "lab_report", "reflection", "dissertation", "other"].map((g) => (
                      <option key={g} value={g}>{g.charAt(0).toUpperCase() + g.slice(1).replaceAll("_", " ")}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Prompt (optional)" hint="The question students answered.">
                  <textarea name="prompt" rows={3} />
                </Field>
              </ActionForm>
            </>
          )}
        </section>
      </div>
    </>
  );
}
