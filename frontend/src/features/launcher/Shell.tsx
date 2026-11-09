"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { logout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import {
  ACCESS_POLICY_DENIALS,
  consoleApi,
  type AccessPolicyDenial,
  type ConsoleAccess,
} from "@/lib/api/console";
import { StatusScreen } from "@/components/StatusScreen";
import { AccountMenu, SidebarShell, type AccountMenuEntry } from "@/components/sidebar/SidebarShell";
import { BrandFooter, DashboardWelcome } from "@/features/controlplane/brandDisplay";
import { useOrganizationBranding } from "@/features/controlplane/useBranding";
import { OrgAccessContext } from "@/features/console/OrgAccess";
import { PolicyGate } from "@/features/console/PolicyGate";
import consoleStyles from "@/features/console/Console.module.css";
import { usePortalRouter } from "@/lib/portalRouter";
import { organizationNav, organizationTabs } from "./orgNav";

type OrgState =
  | { status: "loading" }
  | { status: "not-a-member" }
  | { status: "error" }
  | { status: "policy"; reason: AccessPolicyDenial }
  | { status: "ready"; access: ConsoleAccess };

/**
 * The organization console shell: session and membership are confirmed (and
 * the organization's own access policy satisfied) before any page renders, so
 * no page below repeats those checks. Navigation is derived from the member's
 * permissions (`GET /orgs/<slug>/access`); the backend authorizes every call
 * regardless.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const router = usePortalRouter();
  const params = useParams<{ orgSlug: string }>();
  const orgSlug = params.orgSlug;
  const pathname = usePathname();
  const brand = useOrganizationBranding(orgSlug);
  const session = useSession();
  const portals = usePortals();
  const [org, setOrg] = useState<OrgState>({ status: "loading" });
  const [tick, setTick] = useState(0);
  // A failed sign-out must not look like a successful one.
  const [signOutFailed, setSignOutFailed] = useState(false);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  // A signed-out visitor goes to sign-in, whatever the access call answered.
  const signedOut = session.status === "ready" && !session.session.authenticated;
  useEffect(() => {
    if (signedOut) router.replace("/login");
  }, [signedOut, router]);

  // Access is requested alongside the session rather than after it (2026-10
  // audit): waiting cost a whole round trip before any page could start
  // loading. Asking early reveals nothing - the backend authorizes the call on
  // its own, and a signed-out caller gets the same uniform 404 as a stranger.
  useEffect(() => {
    let cancelled = false;
    consoleApi
      .access(orgSlug)
      .then((access) => {
        if (!cancelled) setOrg({ status: "ready", access });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && (ACCESS_POLICY_DENIALS as readonly string[]).includes(err.code)) {
          setOrg({ status: "policy", reason: err.code as AccessPolicyDenial });
        } else if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
          setOrg({ status: "not-a-member" });
        } else {
          setOrg({ status: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [orgSlug, tick]);

  // Nothing renders until the session is known to be signed in: a signed-out
  // visitor sees this screen while the redirect above runs, never the
  // access call's "not a member" answer.
  if (session.status !== "ready" || signedOut || org.status === "loading") {
    return <StatusScreen>Loading Workeasy360…</StatusScreen>;
  }
  if (org.status === "policy") {
    return (
      <PolicyGate
        reason={org.reason}
        onResolved={reload}
      />
    );
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

  const { access } = org;
  const can = (permission: string) => access.permissions.includes(permission);
  // A consolidated entry's pages are tabs above whichever of them is open.
  const tabs = organizationTabs(orgSlug, can, pathname);
  const who = session.status === "ready" ? session.session : null;
  const consoles = who?.consoles;
  const entries: AccountMenuEntry[] = [
    { label: "Profile", href: `/o/${orgSlug}/settings/profile` },
    { label: "My Security", href: `/o/${orgSlug}/settings/security` },
    { label: "Sign-in Preferences", href: `/o/${orgSlug}/settings/sign-in-preferences` },
    ...(consoles?.staff ? [{ label: "Admin console", href: portalHref(portals, "admin", "/admin") }] : []),
    ...(consoles?.partner ? [{ label: "Partner portal", href: portalHref(portals, "partner", "/partner") }] : []),
    { label: "Switch organization", href: "/organizations/select" },
    {
      label: "Sign out",
      onSelect: () => {
        setSignOutFailed(false);
        logout().then(
          () => router.replace("/login"),
          () => setSignOutFailed(true),
        );
      },
    },
  ];

  return (
    <OrgAccessContext.Provider value={{ slug: orgSlug, access, reload, memberName: who?.name || undefined }}>
      <SidebarShell
        area="Organization"
        // The header says the short name where the partner set one (it is meant for tight places).
        brandName={brand ? brand.portal.short_name.trim() || brand.brand_name : undefined}
        brandLogoUrl={brand?.logo_url}
        context={
          <button
            type="button"
            className={consoleStyles.contextButton}
            onClick={() => router.push("/organizations/select")}
            aria-label={`Current organization: ${access.organization.display_name}. Switch organization.`}
          >
            {access.organization.display_name}
            <span aria-hidden="true"> ▾</span>
          </button>
        }
        groups={organizationNav(orgSlug, can, pathname)}
        account={<AccountMenu name={who?.name || who?.email || ""} entries={entries} />}
        banner={
          <>
            {signOutFailed && (
              <p role="alert" className={consoleStyles.banner}>
                You are still signed in: signing out didn&apos;t complete. Try again, or close this browser.
              </p>
            )}
            {!access.organization.write_allowed && (
              <p role="status" className={`w360-badge w360-badge-warning ${consoleStyles.banner}`}>
                This organization is read-only ({access.organization.status}). You can view and export data.
              </p>
            )}
            {/* The partner's welcome message greets customers on their dashboard (plain text). */}
            {pathname === `/o/${orgSlug}/dashboard` && <DashboardWelcome brand={brand} />}
          </>
        }
      >
        {tabs.length > 0 && (
          <nav aria-label="Sections" className={consoleStyles.tabs}>
            {tabs.map((tab) => (
              <Link key={tab.href} href={tab.href} aria-current={tab.current ? "page" : undefined}>
                {tab.label}
              </Link>
            ))}
          </nav>
        )}
        {children}
        <BrandFooter brand={brand} />
      </SidebarShell>
    </OrgAccessContext.Provider>
  );
}
