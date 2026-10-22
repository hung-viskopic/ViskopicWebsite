"use client";

import Keycloak from "keycloak-js";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export const config = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  keycloakUrl: process.env.NEXT_PUBLIC_KEYCLOAK_URL ?? "http://localhost:18080",
  realm: process.env.NEXT_PUBLIC_KEYCLOAK_REALM ?? "viskopic",
  clientId: process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT ?? "viskopic-web",
};

export type RoleAssignment = {
  id: string;
  role: string;
  scope_type: "institution" | "department" | "course";
  scope_id: string;
  scope_name: string | null;
};

export type Me = {
  kind: "user" | "platform_operator";
  email: string | null;
  user_id?: string;
  institution_id?: string;
  roles: RoleAssignment[];
  permissions: { is_owner?: boolean; manage_people?: boolean; admin_department_ids?: string[]; platform?: boolean };
};

type AuthState = {
  me: Me | null;
  error: string | null;
  api: <T = unknown>(path: string, init?: RequestInit) => Promise<T>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
      .join("; ");
  }
  return "request failed";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const kc = useRef<Keycloak | null>(null);
  const [ready, setReady] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const api = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const k = kc.current;
    if (!k) throw new ApiError(401, "not signed in");
    await k.updateToken(30).catch(() => k.login());
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${k.token}`);
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const res = await fetch(`${config.apiUrl}/api${path}`, { ...init, headers });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new ApiError(res.status, describe(body.detail));
    }
    return res.json() as Promise<T>;
  }, []);

  useEffect(() => {
    if (kc.current) return;
    const k = new Keycloak({ url: config.keycloakUrl, realm: config.realm, clientId: config.clientId });
    kc.current = k;
    k.init({ onLoad: "login-required", pkceMethod: "S256", checkLoginIframe: false })
      .then(() => setReady(true))
      .catch(() => setError("Could not reach the sign-in service. Check that Keycloak is running."));
  }, []);

  useEffect(() => {
    if (!ready) return;
    api<Me>("/me")
      .then(setMe)
      .catch((e: ApiError) => setError(e.message));
  }, [ready, api]);

  const logout = useCallback(() => kc.current?.logout({ redirectUri: window.location.origin }), []);

  if (error) {
    return (
      <div className="center-screen">
        <div className="card narrow">
          <h1>Can&apos;t open Viskopic</h1>
          <p>{error}</p>
          {kc.current?.authenticated && (
            <button className="btn" onClick={logout}>
              Sign out
            </button>
          )}
        </div>
      </div>
    );
  }
  if (!me) return <div className="center-screen muted">Signing in…</div>;
  return <AuthContext.Provider value={{ me, error, api, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
