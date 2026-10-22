"use client";

import { useState } from "react";
import { ActionForm, ErrorNote, Field, Status } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtDate, useResource } from "@/lib/hooks";
import type { BaselineProfile as Profile, ProfilesResponse } from "@/lib/types";

type DemoProfile = { profile_id: string; reference_papers: number; maturity: string };

/** The student's consistency (Δ) profile: requirements, current version, history, demo link. */
export function BaselineProfile({ studentId, canManage }: { studentId: string; canManage: boolean }) {
  const { api } = useAuth();
  const res = useResource<ProfilesResponse>(`/students/${studentId}/profiles`, 5000);
  const demos = useResource<DemoProfile[]>(
    canManage && res.data?.consistency.demo_profiles_allowed ? "/analysis/demo-profiles" : null,
  );
  const [error, setError] = useState<string | null>(null);

  if (res.error) return <ErrorNote error={res.error} />;
  if (!res.data) return null;
  const { profiles, consistency: c } = res.data;
  const ready = profiles.find((p) => p.status === "ready");
  const building = profiles.find((p) => p.status === "queued" || p.status === "building");
  const lastFailed = profiles.find((p) => p.status === "failed");

  async function rebuild() {
    try {
      await api(`/students/${studentId}/profiles/rebuild`, { method: "POST" });
      res.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="card">
      <h2>Consistency profile</h2>
      <p className="muted small">
        Needs {c.min_documents} baseline documents of at least {c.min_words.toLocaleString()} words each. Built and
        updated automatically.
      </p>
      <div className="stats">
        <div className="stat">
          <p className="label">Usable documents</p>
          <strong>{c.eligible_documents} of {c.min_documents}</strong>
        </div>
        <div className="stat">
          <p className="label">Quality reviewed</p>
          <strong>{c.quality_passed_documents} of {c.eligible_documents}</strong>
        </div>
        <div className="stat">
          <p className="label">Current profile</p>
          {ready ? <ProfileSummary p={ready} /> : <span className="muted">None yet</span>}
        </div>
      </div>
      {c.quality_problem_documents > 0 && (
        <p className="note">
          {c.quality_problem_documents} baseline document{c.quality_problem_documents > 1 ? "s were" : " was"} reviewed as
          not English or badly extracted. The model won&apos;t score this student until those are removed or replaced.
        </p>
      )}
      {c.eligible_documents > c.quality_passed_documents && c.quality_problem_documents === 0 && (
        <p className="muted small">
          Each baseline document needs a quality review (open it and record one) before the model scores this
          student&apos;s submissions{c.unreviewed_scoring_allowed ? "; this local install scores unreviewed texts as a labelled demonstration meanwhile" : ""}.
        </p>
      )}
      {building && <p className="note info">Building version {building.version} from {building.reference_count} documents.</p>}
      {!building && lastFailed && (!ready || lastFailed.version > ready.version) && (
        <p className="note">
          Version {lastFailed.version} could not be built. {lastFailed.error}
          {canManage && c.eligible_documents >= c.min_documents && (
            <> <button className="link" onClick={rebuild}>Try again</button></>
          )}
        </p>
      )}
      <ErrorNote error={error} />

      {canManage && c.demo_profiles_allowed && demos.data && (
        <details className="demo">
          <summary>Use a demonstration profile</summary>
          <p className="muted small">
            Try the evidence report before a real baseline exists. Results compare against a research author&apos;s
            papers and say nothing about this student. Local development only.
          </p>
          <ActionForm
            inline
            submitLabel="Use for demo"
            onSubmit={async (f) => {
              await api(`/students/${studentId}/profiles/demonstration`, {
                method: "POST",
                body: JSON.stringify({ author_id: f.get("author_id") }),
              });
              res.reload();
            }}
          >
            <Field label="Research author">
              <select name="author_id" required>
                {demos.data.map((d) => (
                  <option key={d.profile_id} value={d.profile_id}>
                    {d.profile_id}, {d.reference_papers} papers ({d.maturity})
                  </option>
                ))}
              </select>
            </Field>
          </ActionForm>
        </details>
      )}

      {profiles.length > 0 && (
        <div className="table-wrap">
        <table className="compact">
          <thead><tr><th>Version</th><th>Source</th><th>Documents</th><th>Status</th><th>Built</th></tr></thead>
          <tbody>
            {profiles.map((p) => (
              <tr key={p.id}>
                <td>v{p.version}</td>
                <td>{p.source === "demonstration" ? `Demo (${p.demo_author_id})` : "Baseline"}</td>
                <td>{p.source === "enrolled" ? `${p.reference_count} (${p.supervised_count} supervised)` : "-"}</td>
                <td><Status value={p.status} /></td>
                <td>{fmtDate(p.built_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}

function ProfileSummary({ p }: { p: Profile }) {
  if (p.source === "demonstration") {
    return <span>v{p.version} <span className="status status-medium">Demo</span> {p.demo_author_id}</span>;
  }
  return (
    <span>
      v{p.version}, {p.reference_count} documents{p.summary.maturity ? ` (${p.summary.maturity})` : ""}
    </span>
  );
}
