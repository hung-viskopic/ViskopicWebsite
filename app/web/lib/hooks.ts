"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "./auth";

/** Load an API resource; returns data, error and a reload function. */
export function useResource<T>(path: string | null, intervalMs?: number) {
  const { api } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!path) return;
    api<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [api, path]);

  useEffect(() => {
    reload();
    if (!intervalMs) return;
    const t = setInterval(reload, intervalMs);
    return () => clearInterval(t);
  }, [reload, intervalMs]);

  return { data, error, reload };
}

export const roleLabel: Record<string, string> = {
  institution_owner: "Institution owner",
  department_admin: "Department admin",
  reviewer: "Reviewer",
  viewer: "Viewer",
  appeals_reviewer: "Appeals reviewer",
};

export function fmtDate(s?: string | null) {
  if (!s) return "-";
  return new Date(s).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function fmtBytes(n: number) {
  return n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}
