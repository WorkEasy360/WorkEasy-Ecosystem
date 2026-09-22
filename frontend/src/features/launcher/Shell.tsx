"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useParams, useRouter } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { getOrganizationDetail } from "@/lib/api/organizations";
import { logout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { StatusScreen } from "@/components/StatusScreen";
import styles from "./Shell.module.css";

const NAV_LINKS = (orgSlug: string) => [
  { href: `/o/${orgSlug}/apps`, label: "Apps" },
  { href: `/o/${orgSlug}/settings/members`, label: "Members" },
  { href: `/o/${orgSlug}/settings/products`, label: "Products" },
];

type OrgState =
  | { status: "loading" }
  | { status: "not-a-member" }
  | { status: "error" }
  | { status: "ready"; displayName: string };

/**
 * The unified platform shell (Task 3): branding, organization identity,
 * platform-level navigation only - no product navigation ever lives here
 * (Task 3, Task 22). Every child page renders inside this once the session
 * and organization membership are confirmed, so no page below has to repeat
 * that check itself.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ orgSlug: string }>();
  const orgSlug = params.orgSlug;
  const session = useSession();
  const [org, setOrg] = useState<OrgState>({ status: "loading" });
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace("/login");
      return;
    }
    getOrganizationDetail(orgSlug)
      .then((detail) => setOrg({ status: "ready", displayName: detail.display_name }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
          setOrg({ status: "not-a-member" });
        } else {
          setOrg({ status: "error" });
        }
      });
  }, [session, router, orgSlug]);

  if (session.status === "loading" || org.status === "loading") {
    return <StatusScreen>Loading Workeasy360…</StatusScreen>;
  }
  if (org.status === "not-a-member") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>You don&apos;t have access to this organization, or it no longer exists.</p>
          <Link className="w360-btn w360-btn-secondary" href="/organizations/select">
            Choose an organization
          </Link>
        </div>
      </StatusScreen>
    );
  }
  if (org.status === "error") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>Workeasy360 could not be reached right now.</p>
          <button className="w360-btn w360-btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </StatusScreen>
    );
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={`w360-container ${styles.headerInner}`}>
          <div className={styles.brandGroup}>
            <span className={styles.brand}>Workeasy360</span>
            <button
              type="button"
              className={styles.orgSwitcher}
              onClick={() => router.push("/organizations/select")}
              aria-label={`Current organization: ${org.displayName}. Switch organization.`}
            >
              {org.displayName}
              <span aria-hidden="true"> ▾</span>
            </button>
          </div>

          <nav aria-label="Platform" className={styles.nav}>
            {NAV_LINKS(orgSlug).map((link) => {
              const active = pathname.startsWith(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={styles.navLink}
                  aria-current={active ? "page" : undefined}
                  data-active={active || undefined}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>

          <div className={styles.userMenuWrap}>
            <button
              type="button"
              className={styles.userButton}
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              {session.status === "ready" && session.session.authenticated
                ? initials(session.session.name || session.session.email || "?")
                : "?"}
            </button>
            {menuOpen && (
              <div className={styles.userMenu} role="menu">
                <Link
                  href={`/o/${orgSlug}/settings/profile`}
                  role="menuitem"
                  className={styles.userMenuItem}
                  onClick={() => setMenuOpen(false)}
                >
                  Profile
                </Link>
                <Link
                  href={`/o/${orgSlug}/settings/organization`}
                  role="menuitem"
                  className={styles.userMenuItem}
                  onClick={() => setMenuOpen(false)}
                >
                  Organization settings
                </Link>
                <button
                  role="menuitem"
                  className={styles.userMenuItem}
                  onClick={() => {
                    setMenuOpen(false);
                    logout().finally(() => router.replace("/login"));
                  }}
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <main id="w360-main" className="w360-container" style={{ paddingBlock: "var(--w360-space-6)" }}>
        {children}
      </main>
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}
