"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Logo } from "@/components/ui";
import { AuthProvider, useAuth } from "@/lib/auth";
import { roleLabel } from "@/lib/hooks";

function TopBar() {
  const { me, logout } = useAuth();
  const path = usePathname();
  const operator = me!.kind === "platform_operator";
  const perms = me!.permissions;

  const links: [string, string][] = operator
    ? [["/platform", "Universities"]]
    : [
        ["/", "Cases"],
        ["/students", "Students"],
        ["/courses", "Courses"],
        ...(perms.manage_people ? ([["/people", "People"]] as [string, string][]) : []),
        ...(perms.is_owner
          ? ([
              ["/departments", "Departments"],
              ["/settings", "Settings"],
            ] as [string, string][])
          : []),
      ];

  const role = operator ? "Viskopic operator" : roleLabel[me!.roles[0]?.role] ?? "No role yet";
  const isActive = (href: string) =>
    href === "/" ? path === "/" || path.startsWith("/documents") || path.startsWith("/vivas") || path.startsWith("/check")
      : path.startsWith(href);

  return (
    <header className={operator ? "topbar operator-bar" : "topbar"}>
      <div className="topbar-in">
        <Link href={operator ? "/platform" : "/"} className="brand">
          <Logo />
          <span>Viskopic</span>
        </Link>
        <nav className="topnav" aria-label="Main">
          {links.map(([href, label]) => (
            <Link key={href} href={href} className={isActive(href) ? "active" : undefined}
              aria-current={isActive(href) ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
        <div className="me">
          <span><b>{me!.email}</b>{role}</span>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </div>
    </header>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  // The student viva page is reached by an emailed link and has no staff sign-in.
  if (path.startsWith("/viva/")) return <>{children}</>;
  // Focused task screens bring their own bar with a way back and their progress.
  const focused = path.startsWith("/check/");
  const wide = path.startsWith("/documents/") || path.startsWith("/vivas/");
  return (
    <AuthProvider>
      {focused ? children : (
        <>
          <TopBar />
          <main className={wide ? "main wide" : "main"}>{children}</main>
        </>
      )}
    </AuthProvider>
  );
}
