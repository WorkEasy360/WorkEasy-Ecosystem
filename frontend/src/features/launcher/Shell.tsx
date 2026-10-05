"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSession } from "@/lib/auth/useSession";
import { logout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import {
  ACCESS_POLICY_DENIALS,
  consoleApi,
  type AccessPolicyDenial,
  type ConsoleAccess,
} from "@/lib/api/console";
import { StatusScreen } from "@/components/StatusScreen";
import {
  AccountMenu,
  SidebarShell,
  type AccountMenuEntry,
  type SidebarGroup,
} from "@/components/sidebar/SidebarShell";
import { useOrganizationBranding } from "@/features/controlplane/useBranding";
import { OrgAccessContext } from "@/features/console/OrgAccess";
import { PolicyGate } from "@/features/console/PolicyGate";
import consoleStyles from "@/features/console/Console.module.css";

type OrgState =
  | { status: "loading" }
  | { status: "not-a-member" }
  | { status: "error" }
  | { status: "policy"; reason: AccessPolicyDenial }
  | { status: "ready"; access: ConsoleAccess };

/** The organization console's navigation (Phase 8 extension, Phase 9): the
 * customer's own pages first - Dashboard, My products, Explore products,
 * billing, orders - then what an administrator manages. Each entry appears
 * only when the member holds the permission its page needs. */
export function organizationNav(slug: string, can: (permission: string) => boolean): SidebarGroup[] {
  const base = `/o/${slug}`;
  const billing = can("platform.billing.view") || can("commercial.subscription.view");
  return [
    {
      items: [
        { href: `${base}/dashboard`, label: "Dashboard", icon: "dashboard" },
        { href: `${base}/apps`, label: "My products", icon: "apps" },
        { href: `${base}/explore`, label: "Explore products", icon: "store" },
        { href: `${base}/settings/billing`, label: "Subscriptions & billing", icon: "card", when: billing },
        { href: `${base}/orders`, label: "Orders", icon: "receipt", when: billing },
      ],
    },
    {
      title: "Organization & team",
      items: [
        { href: `${base}/settings/organization`, label: "Organization", icon: "building" },
        { href: `${base}/settings/members`, label: "Members", icon: "users", when: can("platform.member.view") },
        {
          href: `${base}/settings/invitations`,
          label: "Invitations",
          icon: "mail",
          when: can("platform.member.invite"),
        },
        { href: `${base}/settings/profile`, label: "Profile", icon: "users" },
      ],
    },
    {
      title: "App management",
      items: [
        {
          href: `${base}/settings/products`,
          label: "Product access",
          icon: "puzzle",
          when: can("platform.seat.assign"),
        },
        {
          href: `${base}/settings/integrations`,
          label: "Integrations",
          icon: "network",
          when: can("platform.connection.view"),
        },
        {
          href: `${base}/settings/automations`,
          label: "Automations",
          icon: "bolt",
          when: can("platform.automation.view"),
        },
        { href: `${base}/settings/ai`, label: "AI", icon: "sparkles", when: can("platform.ai.view") },
        {
          href: `${base}/settings/analytics`,
          label: "Analytics",
          icon: "chart",
          when: can("platform.analytics.view"),
        },
      ],
    },
    {
      title: "Security",
      items: [
        {
          href: `${base}/settings/security/authentication`,
          label: "Authentication",
          icon: "lock",
          when: can("platform.security.view"),
        },
        {
          href: `${base}/settings/security/network`,
          label: "Network access",
          icon: "network",
          when: can("platform.security.view"),
        },
        { href: `${base}/settings/security`, label: "My security", icon: "shield", exact: true },
      ],
    },
    {
      title: "Audit",
      items: [
        { href: `${base}/settings/audit`, label: "Audit log", icon: "audit", when: can("platform.audit.view") },
        {
          href: `${base}/settings/sign-ins`,
          label: "Sign-in activity",
          icon: "history",
          when: can("platform.audit.view"),
        },
      ],
    },
    {
      items: [{ href: `${base}/help`, label: "Support", icon: "diagnostics" }],
    },
  ];
}

/**
 * The organization console shell: session and membership are confirmed (and
 * the organization's own access policy satisfied) before any page renders, so
 * no page below repeats those checks. Navigation is derived from the member's
 * permissions (`GET /orgs/<slug>/access`); the backend authorizes every call
 * regardless.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const params = useParams<{ orgSlug: string }>();
  const orgSlug = params.orgSlug;
  const brand = useOrganizationBranding(orgSlug);
  const session = useSession();
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
        mfaEnrolled={session.status === "ready" && Boolean(session.session.mfa_enrolled)}
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
  const who = session.status === "ready" ? session.session : null;
  const consoles = who?.consoles;
  const entries: AccountMenuEntry[] = [
    { label: "Profile", href: `/o/${orgSlug}/settings/profile` },
    { label: "My security", href: `/o/${orgSlug}/settings/security` },
    ...(consoles?.staff ? [{ label: "Super Admin console", href: "/admin" }] : []),
    ...(consoles?.partner ? [{ label: "Partner portal", href: "/partner" }] : []),
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
        brandName={brand?.brand_name}
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
        groups={organizationNav(orgSlug, can)}
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
          </>
        }
      >
        {children}
      </SidebarShell>
    </OrgAccessContext.Provider>
  );
}
