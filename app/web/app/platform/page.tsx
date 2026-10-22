"use client";

import { useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";

type Institution = {
  id: string;
  name: string;
  slug: string;
  status: string;
  created_at: string;
  user_count: number;
  document_count: number;
  failed_jobs: number;
  owner_count: number;
};

type SupportJob = { job_id: number; status: string; attempts: number; last_error: string | null; file_type: string; updated_at: string };

export default function Platform() {
  const { me, api } = useAuth();
  const list = useResource<Institution[]>(me!.kind === "platform_operator" ? "/platform/institutions" : null);
  const [support, setSupport] = useState<{ id: string; rows?: SupportJob[]; error?: string } | null>(null);

  if (me!.kind !== "platform_operator") return <p className="error">Platform operators only.</p>;

  async function create(f: FormData) {
    await api("/platform/institutions", {
      method: "POST",
      body: JSON.stringify({ name: f.get("name"), slug: f.get("slug"), owner_email: f.get("owner_email") }),
    });
    list.reload();
  }

  async function setStatus(id: string, status: string) {
    await api(`/platform/institutions/${id}/status`, { method: "POST", body: JSON.stringify({ status }) });
    list.reload();
  }

  async function inviteOwner(id: string) {
    const email = prompt("Email of the additional institution owner");
    if (!email) return;
    try {
      await api(`/platform/institutions/${id}/owners`, { method: "POST", body: JSON.stringify({ email }) });
      list.reload();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  async function viewSupport(id: string) {
    try {
      setSupport({ id, rows: await api<SupportJob[]>(`/platform/institutions/${id}/support/jobs`) });
    } catch (e) {
      setSupport({ id, error: (e as Error).message });
    }
  }

  return (
    <>
      <PageHeader title="Institutions" />
      <p className="muted">
        You can create institutions and invite owners. Student data is never visible here, and job health only while an
        institution has granted support access.
      </p>

      <section className="card">
        <h2>New institution</h2>
        <ActionForm onSubmit={create} submitLabel="Create and invite owner" inline>
          <Field label="Name"><input name="name" required placeholder="University of Bristol" /></Field>
          <Field label="Slug"><input name="slug" required pattern="[a-z0-9-]{2,40}" placeholder="bristol" /></Field>
          <Field label="Owner email"><input name="owner_email" type="email" required /></Field>
        </ActionForm>
      </section>

      <section className="card">
        <ErrorNote error={list.error} />
        {list.data?.length === 0 && <Empty>No institutions yet.</Empty>}
        {list.data && list.data.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Institution</th><th>Status</th><th>Owners</th><th>Users</th><th>Documents</th><th>Failed jobs</th><th>Created</th><th /></tr>
              </thead>
              <tbody>
                {list.data.map((i) => (
                  <tr key={i.id}>
                    <td><strong>{i.name}</strong><span className="sub mono">{i.slug}</span></td>
                    <td><Status value={i.status} /></td>
                    <td>{i.owner_count}</td>
                    <td>{i.user_count}</td>
                    <td>{i.document_count}</td>
                    <td>{i.failed_jobs}</td>
                    <td>{fmtDate(i.created_at)}</td>
                    <td className="actions">
                      {i.owner_count < 2 && <button className="btn secondary" onClick={() => inviteOwner(i.id)}>Invite owner</button>}
                      <button className="btn secondary" onClick={() => viewSupport(i.id)}>Job health</button>
                      {i.status === "active" ? (
                        <button className="btn danger" onClick={() => setStatus(i.id, "suspended")}>Suspend</button>
                      ) : (
                        <button className="btn secondary" onClick={() => setStatus(i.id, "active")}>Reactivate</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {support && (
        <section className="card">
          <h2>Job health</h2>
          <ErrorNote error={support.error ?? null} />
          {support.rows?.length === 0 && <Empty>No jobs.</Empty>}
          {support.rows && support.rows.length > 0 && (
            <div className="table-wrap">
            <table>
              <thead><tr><th>Job</th><th>Status</th><th>Attempts</th><th>Type</th><th>Error</th><th>Updated</th></tr></thead>
              <tbody>
                {support.rows.map((j) => (
                  <tr key={j.job_id}>
                    <td className="mono">{j.job_id}</td><td><Status value={j.status} /></td><td>{j.attempts}</td>
                    <td>{j.file_type}</td><td>{j.last_error ?? "-"}</td><td>{fmtDate(j.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </section>
      )}
    </>
  );
}
