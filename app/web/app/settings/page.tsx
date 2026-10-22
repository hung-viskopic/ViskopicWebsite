"use client";

import { useState } from "react";
import { ActionForm, Empty, ErrorNote, Field, PageHeader } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";

type Institution = { name: string; slug: string; kms_key_ref: string; created_at: string };
type SupportGrant = { id: string; reason: string; expires_at: string; revoked_at: string | null; granted_by_email: string; created_at: string };
type AuditEntry = { id: number; actor_label: string; action: string; target_type: string | null; target_id: string | null; details: Record<string, unknown>; at: string };

export default function Settings() {
  const { api } = useAuth();
  const inst = useResource<Institution>("/institution");
  const grants = useResource<SupportGrant[]>("/support-grants");
  const log = useResource<AuditEntry[]>("/audit?limit=200");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function grant(f: FormData) {
    await api("/support-grants", { method: "POST", body: JSON.stringify({ reason: f.get("reason"), hours: Number(f.get("hours")) }) });
    grants.reload();
    log.reload();
  }

  async function revoke(id: string) {
    try {
      await api(`/support-grants/${id}`, { method: "DELETE" });
      grants.reload();
      log.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const now = Date.now();
  const entries = (log.data ?? []).filter((e) => !filter || e.action.includes(filter) || e.actor_label.includes(filter));

  return (
    <>
      <PageHeader title="Settings and audit" />
      <div className="grid">
        <section className="card">
          <h2>{inst.data?.name ?? "Institution"}</h2>
          <dl className="meta">
            <dt>Identifier</dt><dd className="mono">{inst.data?.slug}</dd>
            <dt>Encryption key</dt><dd className="mono">{inst.data?.kms_key_ref}</dd>
            <dt>Created</dt><dd>{fmtDate(inst.data?.created_at)}</dd>
          </dl>
          <p className="muted small section-gap">
            Student identities, files and text are encrypted with your institution&apos;s own key, which you can revoke.
          </p>
        </section>

        <section className="card">
          <h2>Support access</h2>
          <p className="muted small">
            Let Viskopic staff see job status and errors for a limited time. They never see documents or students.
          </p>
          <ActionForm onSubmit={grant} submitLabel="Grant support access">
            <Field label="Reason"><input name="reason" required minLength={5} placeholder="Uploads failing since Monday" /></Field>
            <Field label="Duration">
              <select name="hours" defaultValue="24">
                <option value="2">2 hours</option><option value="24">24 hours</option><option value="72">3 days</option><option value="168">7 days</option>
              </select>
            </Field>
          </ActionForm>
          <ErrorNote error={error} />
          {grants.data?.map((g) => {
            const active = !g.revoked_at && new Date(g.expires_at).getTime() > now;
            return (
              <div key={g.id} className="grant-row">
                <span>
                  {active ? <strong>Active</strong> : <span className="muted">Ended</span>} {g.reason}
                  <span className="sub">Until {fmtDate(g.expires_at)}</span>
                </span>
                {active && <button className="link" onClick={() => revoke(g.id)}>Revoke</button>}
              </div>
            );
          })}
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Audit log</h2>
          <input className="search" placeholder="Filter by action or person" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <ErrorNote error={log.error} />
        {entries.length === 0 && <Empty>No entries.</Empty>}
        {entries.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Details</th></tr></thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td>{fmtDate(e.at)}</td>
                    <td>{e.actor_label}</td>
                    <td className="mono">{e.action}</td>
                    <td className="mono muted audit-details">
                      {Object.keys(e.details).length ? JSON.stringify(e.details) : e.target_type ? `${e.target_type} ${e.target_id}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
