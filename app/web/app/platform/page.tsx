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
  const [inviting, setInviting] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

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

  async function inviteOwner(id: string, f: FormData) {
    await api(`/platform/institutions/${id}/owners`, { method: "POST", body: JSON.stringify({ email: f.get("email") }) });
    setInviting(null);
    list.reload();
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
      <PageHeader title="Universities" meta="You can add universities and invite their owners. You never see student data here; job health only while a university has granted support access.">
        {!adding && <button className="btn" onClick={() => setAdding(true)}>Add a university</button>}
      </PageHeader>

      {adding && (
        <section className="card">
          <h2>Add a university</h2>
          <ActionForm onSubmit={async (f) => { await create(f); setAdding(false); }} submitLabel="Add and invite the owner" inline>
            <Field label="Name"><input name="name" required placeholder="University of Bristol" /></Field>
            <Field label="Short name for links" hint="Lower-case letters, numbers and dashes"><input name="slug" required pattern="[a-z0-9-]{2,40}" placeholder="bristol" /></Field>
            <Field label="Owner's email"><input name="owner_email" type="email" required /></Field>
          </ActionForm>
          <button className="link" onClick={() => setAdding(false)}>Cancel</button>
        </section>
      )}

      <ErrorNote error={list.error} />
      <section className="group">
        {list.data?.length === 0 && <div className="row"><Empty>No universities yet.</Empty></div>}
        {list.data?.map((i) => (
          <div key={i.id} className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 28px", alignItems: "center", justifyContent: "space-between" }}>
              <div className="grow" style={{ flex: "1 1 360px" }}>
                <span className="who">{i.name}</span> <span className="muted mono small">{i.slug}</span>
                <span className="sub">
                  {i.owner_count} owner{i.owner_count === 1 ? "" : "s"} · {i.user_count} users · {i.document_count} documents
                  {i.failed_jobs > 0 ? ` · ${i.failed_jobs} failed jobs` : ""} · added {fmtDate(i.created_at)}
                </span>
              </div>
              {i.status !== "active" && <Status value={i.status} />}
              <div className="page-actions">
                {i.owner_count < 2 && inviting !== i.id && <button className="btn secondary sm" onClick={() => setInviting(i.id)}>Invite another owner</button>}
                <button className="btn secondary sm" onClick={() => viewSupport(i.id)}>Job health</button>
                {i.status === "active" ? (
                  <button className="btn danger sm" onClick={() => setStatus(i.id, "suspended")}>Suspend</button>
                ) : (
                  <button className="btn secondary sm" onClick={() => setStatus(i.id, "active")}>Reactivate</button>
                )}
              </div>
            </div>
            {inviting === i.id && (
              <div className="inline-form">
                <ActionForm onSubmit={(f) => inviteOwner(i.id, f)} submitLabel="Send invitation" inline>
                  <Field label="Email of the other owner"><input name="email" type="email" required autoFocus /></Field>
                </ActionForm>
                <button className="link" style={{ alignSelf: "flex-start" }} onClick={() => setInviting(null)}>Cancel</button>
              </div>
            )}
          </div>
        ))}
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
