"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { logout, switchAccount } from "@/lib/api/auth";
import { forgetAccountState, leaveTo } from "@/lib/accountState";
import { ApiError } from "@/lib/api/client";
import {
  ACCESS_POLICY_DENIALS,
  ASSIGNABLE_ROLES,
  consoleApi,
  type AccessPolicyDenial,
  type ConsoleAccess,
} from "@/lib/api/console";
import { StatusScreen } from "@/components/StatusScreen";
import { SidebarShell } from "@/components/sidebar/SidebarShell";
import { Icon } from "@/components/sidebar/icons";
import { AdminAccount } from "@/features/controlplane/AdminTopbar";
import type { MenuItem } from "@/features/controlplane/kit";
import { BrandFooter, DashboardWelcome } from "@/features/controlplane/brandDisplay";
import { useOrganizationBranding } from "@/features/controlplane/useBranding";
import { OrgAccessContext } from "@/features/console/OrgAccess";
import { PolicyGate } from "@/features/console/PolicyGate";
import consoleStyles from "@/features/console/Console.module.css";
import topbar from "@/features/controlplane/AdminTopbar.module.css";
import { usePortalRouter } from "@/lib/portalRouter";
import { organizationNav, organizationPlace, organizationTabs } from "./orgNav";
import { OrgBell, OrgHelpLink, OrgSearch, OrgSwitcher } from "./OrgTopbar";

type OrgState =
  | { status: "loading" }
  | { status: "not-a-member" }
  | { status: "error" }
  | { status: "policy"; reason: AccessPolicyDenial }
  | { status: "ready"; access: ConsoleAccess };

/** The member's role here, as people read it. */
export function memberRoleLabel(access: ConsoleAccess): string {
  if (access.membership.is_owner) return "Owner";
  const key = access.membership.roles[0];
  return ASSIGNABLE_ROLES.find((r) => r.key === key)?.label ?? "Member";
}

/** The sidebar's foot (the owner's reference image): the in-console Store, which
 * already knows this organization and what it holds. */
function StoreCard({ slug }: { slug: string }) {
  return (
    <div className={consoleStyles.storeCard}>
      <span className={consoleStyles.storeCardIcon} aria-hidden="true">
        <Icon name="store" size={22} />
      </span>
      <p className={consoleStyles.storeCardTitle}>Explore More Business Apps</p>
      <p className={consoleStyles.storeCardText}>Add more WorkEasy360 products to your organization.</p>
      <Link href={`/o/${slug}/store`} className={consoleStyles.storeCardLink}>
        Visit WorkEasy360 Store <Icon name="chevronRight" size={16} />
      </Link>
    </div>
  );
}

/**
 * The User Admin Panel's shell (the organization console): session and
 * membership are confirmed (and the organization's own access policy satisfied)
 * before any page renders, so no page below repeats those checks. Navigation is
 * derived from the member's permissions (`GET /orgs/<slug>/access`); the backend
 * authorizes every call regardless.
 *
 * 2026-11-11 (the owner's specification and reference image): the Admin and
 * Partner consoles' look - the teal frame, the seven menus with their sub-menus,
 * the icon rail, the drawer below 1024px - and a top bar with the organization
 * switcher, search, notifications, help and the account menu.
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

  // The consoles' one design system (globals.css :root[data-console]): the type
  // scale, control heights, card radius and shadow - on <html>, so dialogs drawn
  // on <body> have them too. The browser tab names the page and the console.
  useEffect(() => {
    document.documentElement.dataset.console = "user";
    return () => {
      delete document.documentElement.dataset.console;
    };
  }, []);

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
    return <PolicyGate reason={org.reason} onResolved={reload} />;
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
  // An entry's pages are tabs above whichever of them is open.
  const tabs = organizationTabs(orgSlug, can, pathname);
  const place = organizationPlace(orgSlug, can, pathname);
  const who = session.status === "ready" ? session.session : null;
  const consoles = who?.consoles;
  // The unified-login brief 19: My Profile, the current organization (the
  // menu's header names it), Switch Organization, Switch Account, Account
  // Security, Logout. Leaving an account forgets what it left in this browser
  // and loads the next page afresh, so nothing of it survives in memory or in
  // Back (lib/accountState.ts).
  const leaveAccount = (step: () => Promise<string>) => {
    setSignOutFailed(false);
    step().then(
      (next) => {
        forgetAccountState();
        leaveTo(next);
      },
      () => setSignOutFailed(true),
    );
  };
  const entries: MenuItem[] = [
    { label: "My Profile", icon: "user", href: `/o/${orgSlug}/settings/profile` },
    { label: "Switch Organization", icon: "building", href: "/organizations/select" },
    {
      label: "Switch Account",
      icon: "users",
      onSelect: () => leaveAccount(() => switchAccount().then(({ redirect_url }) => redirect_url)),
    },
    { label: "Account & Security", icon: "shield", href: `/o/${orgSlug}/settings/security` },
    ...(consoles?.staff ? [{ label: "Admin console", icon: "external", href: portalHref(portals, "admin", "/admin") }] : []),
    ...(consoles?.partner ? [{ label: "Partner portal", icon: "external", href: portalHref(portals, "partner", "/partner") }] : []),
    {
      label: "Logout",
      icon: "logout",
      separator: true,
      onSelect: () => leaveAccount(() => logout().then(() => "/login")),
    },
  ];

  return (
    <OrgAccessContext.Provider value={{ slug: orgSlug, access, reload, memberName: who?.name || undefined }}>
      <SidebarShell
        area="User Admin"
        variant="admin"
        accordion
        railKey="w360.org.rail"
        // The header says the short name where the partner set one (it is meant for tight places).
        brandName={brand ? brand.portal.short_name.trim() || brand.brand_name : undefined}
        brandLogoUrl={brand?.logo_url}
        navTitle={
          <>
            <Icon name="users" size={16} /> User Admin
          </>
        }
        context={
          <span className={consoleStyles.topContext}>
            <OrgSwitcher slug={orgSlug} name={access.organization.display_name} />
            {place && (
              // Where the open page sits, while the menu is a drawer (below 1024px).
              <span className={consoleStyles.pageLabel}>{place.section}</span>
            )}
          </span>
        }
        tools={
          <>
            <OrgSearch slug={orgSlug} can={can} />
            <div className={topbar.spacer} />
            <OrgBell slug={orgSlug} />
            <OrgHelpLink slug={orgSlug} />
          </>
        }
        account={
          <AdminAccount
            name={who?.name ?? ""}
            email={who?.email ?? ""}
            role={`${memberRoleLabel(access)} · ${access.organization.display_name}`}
            entries={entries}
          />
        }
        groups={organizationNav(orgSlug, can, pathname)}
        navFooter={<StoreCard slug={orgSlug} />}
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
