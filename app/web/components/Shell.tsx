"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { AuthProvider, useAuth } from "@/lib/auth";
import { roleLabel } from "@/lib/hooks";

function Nav() {
  const { me, logout } = useAuth();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const operator = me!.kind === "platform_operator";
  const perms = me!.permissions;

  useEffect(() => setOpen(false), [path]);

  const links: [string, string][] = operator
    ? [["/platform", "Institutions"]]
    : [
        ["/", "Overview"],
        ["/courses", "Courses"],
        ["/students", "Students"],
        ...(perms.manage_people ? ([["/people", "People and roles"]] as [string, string][]) : []),
        ...(perms.is_owner
          ? ([
              ["/departments", "Departments"],
              ["/settings", "Settings and audit"],
            ] as [string, string][])
          : []),
      ];

  const primaryRole = operator ? "Platform operator" : roleLabel[me!.roles[0]?.role] ?? "No role yet";

  return (
    <aside className={open ? "sidebar open" : "sidebar"}>
      <div className="sidebar-top">
        <Link href={operator ? "/platform" : "/"} className="brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.svg" alt="" width={26} height={26} />
          <span>Viskopic</span>
        </Link>
        <button
          className="menu-toggle"
          aria-expanded={open}
          aria-controls="main-nav"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Close" : "Menu"}
        </button>
      </div>
      <nav id="main-nav">
        {links.map(([href, label]) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <Link key={href} href={href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="whoami">
        <p className="label">{primaryRole}</p>
        <p className="email">{me!.email}</p>
        <button className="link" onClick={logout}>
          Sign out
        </button>
      </div>
    </aside>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <div className="layout">
        <Nav />
        <main className="main">{children}</main>
      </div>
    </AuthProvider>
  );
}
