"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { StatusScreen } from "@/components/StatusScreen";
import {
  AccountMenu,
  SidebarShell,
  type AccountMenuEntry,
  type SidebarGroup,
} from "@/components/sidebar/SidebarShell";
import { logout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { adminApi, partnerApi, type PortalPartner } from "@/lib/api/controlPlane";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import consoleStyles from "@/features/console/Console.module.css";
import { adminSignInUrl } from "@/features/auth/signInMode";
import loginStyles from "@/features/auth/LoginScreen.module.css";
import { PARTNER_MENU, type Can } from "@/features/partnerpanel/menu";
import { centerItems } from "./partnerCenterNav";
import { ForcedPasswordChange } from "./ForcedPasswordChange";
import { StaffMfaGate } from "./StaffMfaGate";
import { usePortalRouter } from "@/lib/portalRouter";

function useSignOut() {
  const router = usePortalRouter();
  // A failed sign-out must not look like a successful one: navigating to
  // /login anyway would resume the still-live session for whoever sits down
  // at this browser next.
  const [failed, setFailed] = useState(false);
  const signOut = () => {
    setFailed(false);
    logout().then(
      () => router.replace("/login"),
      () => setFailed(true),
    );
  };
  const banner = failed ? (
    <p role="alert" className={consoleStyles.banner}>
      You are still signed in: signing out didn&apos;t complete. Try again, or close this browser.
    </p>
  ) : null;
  return { signOut, banner };
}

// --- Super Admin -----------------------------------------------------------------

/** Exported so a screen can be rendered under a chosen permission set in tests. */
export const AdminContext = createContext<{ permissions: string[] }>({ permissions: [] });
export const useAdmin = () => useContext(AdminContext);
export const can = (permissions: string[], key: string) => permissions.includes(key);

type AdminState =
  | { status: "loading" }
  | { status: "verify" }
  | { status: "denied" }
  | { status: "error" }
  | { status: "ready"; permissions: string[] };

/** The Super Admin's navigation (Phase 8 extension), in the agency-panel
 * shape: audience, billing, panel customization, security. Each entry appears
 * only when the staff member holds the grant its page needs. */
export function adminNav(p: string[]): SidebarGroup[] {
  return [
    { items: [{ href: "/admin", label: "Dashboard", icon: "dashboard", exact: true }] },
    {
      title: "Audience",
      items: [
        { href: "/admin/users", label: "Users", icon: "users", when: can(p, "platform.staff_user.view") },
        { href: "/admin/customers", label: "Customers", icon: "building", when: can(p, "platform.staff_customer.view") },
        {
          // One "Partners" entry opens the Partner Control Center, whose own
          // navigation holds the dashboard, the partner views (All, Resellers,
          // White-label, Affiliates - the last three are filters on
          // capabilities, not kinds of account) and the programme's money
          // screens. It stays highlighted on every page under /admin/partners.
          href: centerItems(p)[0]?.href ?? "/admin/partners",
          label: "Partners",
          icon: "handshake",
          when: centerItems(p).length > 0,
          match: "/admin/partners",
        },
      ],
    },
    {
      title: "Commerce",
      items: [
        { href: "/admin/orders", label: "Orders", icon: "receipt", when: can(p, "platform.staff_payment.view") },
        {
          href: "/admin/subscriptions",
          label: "Subscriptions",
          icon: "card",
          when: can(p, "platform.staff_customer.view"),
        },
        {
          href: "/admin/entitlements",
          label: "Entitlements",
          icon: "key",
          when: can(p, "platform.staff_customer.view"),
        },
      ],
    },
    {
      title: "Billing",
      items: [
        { href: "/admin/plans", label: "Plans & add-ons", icon: "tag", when: can(p, "platform.staff_catalog.view") },
        {
          href: "/admin/pricing",
          label: "Currency & pricing",
          icon: "currency",
          when: can(p, "platform.staff_catalog.view"),
        },
        { href: "/admin/payments", label: "Payments", icon: "card", when: can(p, "platform.staff_payment.view") },
      ],
    },
    {
      title: "Panel customization",
      items: [
        {
          href: "/admin/products",
          label: "Product manager",
          icon: "puzzle",
          when: can(p, "platform.staff_catalog.view"),
        },
        {
          href: "/admin/communication",
          label: "Communication",
          icon: "mail",
          when: can(p, "platform.staff_communication.view"),
        },
        { href: "/admin/ai", label: "AI", icon: "sparkles", when: can(p, "platform.staff_ai.view") },
        {
          href: "/admin/settings",
          label: "Platform settings",
          icon: "settings",
          when: can(p, "platform.staff_settings.view"),
        },
      ],
    },
    {
      title: "Security & support",
      items: [
        { href: "/admin/security", label: "Staff access", icon: "key", when: can(p, "platform.staff_security.view") },
        { href: "/admin/audit", label: "Audit", icon: "audit", when: can(p, "platform.staff_audit.view") },
        {
          href: "/admin/diagnostics",
          label: "Diagnostics",
          icon: "diagnostics",
          when: can(p, "platform.staff_support.view"),
        },
      ],
    },
  ];
}

/** "You don't have administrator access" - the answer for a signed-in account
 * the server refused (no staff identity, or no StaffGrant in force). It names
 * no permission and shows nothing from the console. */
export function AdminAccessDenied({ onSignOut, banner }: { onSignOut: () => void; banner?: React.ReactNode }) {
  const router = usePortalRouter();
  const portals = usePortals();
  // Another portal in multi-portal mode (ADR-043): a full navigation there.
  const asUser = () =>
    portals ? window.location.assign(portalHref(portals, "app", "/dashboard")) : router.replace("/dashboard?mode=user");
  // Its own page rather than StatusScreen: only the message is the live
  // region, so the buttons are not read out as part of an alert.
  return (
    <main id="w360-main" className={`w360-container ${loginStyles.main}`}>
      <div className={`w360-card ${loginStyles.card} ${loginStyles.denied}`}>
        <div role="alert" aria-live="assertive">
          <h1>You don&apos;t have administrator access.</h1>
          <p>This account can&apos;t open the Workeasy360 Admin Console. You can continue to your own organization and products.</p>
        </div>
        {banner}
        <div className={loginStyles.deniedActions}>
          <button type="button" className="w360-btn w360-btn-primary" onClick={asUser}>
            Continue as User
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </div>
    </main>
  );
}

/** The control-plane layout: a separate shell from any organization's, with
 * navigation derived from the permissions the staff member actually holds. */
export function AdminShell({ children }: { children: React.ReactNode }) {
  const router = usePortalRouter();
  const portals = usePortals();
  const pathname = usePathname();
  const session = useSession();
  const [state, setState] = useState<AdminState>({ status: "loading" });
  // Bumped after a successful verification to ask the backend again.
  const [attempt, setAttempt] = useState(0);
  const { signOut, banner } = useSignOut();

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      // Sign in as an administrator, then come back to this page. The mode
      // and the page are in the address only; the console still asks the
      // server below whether this person may enter.
      router.replace(adminSignInUrl(`${pathname ?? "/admin"}${window.location.search}`));
      return;
    }
    adminApi
      .session()
      .then((s) => setState({ status: "ready", permissions: s.permissions }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.code === "mfa_required") {
          setState({ status: "verify" });
          return;
        }
        setState({ status: err instanceof ApiError && (err.status === 403 || err.status === 401) ? "denied" : "error" });
      });
  }, [session, router, attempt, pathname]);

  if (session.status === "loading" || state.status === "loading") {
    return <StatusScreen>Loading the control plane…</StatusScreen>;
  }
  if (state.status === "verify") {
    return (
      <StaffMfaGate
        mfaEnrolled={session.status === "ready" && Boolean(session.session.mfa_enrolled)}
        onVerified={() => {
          setState({ status: "loading" });
          setAttempt((n) => n + 1);
        }}
      />
    );
  }
  if (state.status === "denied") {
    // Signed in, but the server says this account may not use the console.
    // Nothing from the console has been loaded; the person may carry on into
    // their own workspace with the same sign-in.
    return <AdminAccessDenied onSignOut={signOut} banner={banner} />;
  }
  if (state.status === "error") {
    return (
      <StatusScreen role="alert">
        <p>The control plane could not be reached right now.</p>
      </StatusScreen>
    );
  }
  const who = session.status === "ready" ? session.session : null;
  const entries: AccountMenuEntry[] = [
    { label: "My organizations", href: portalHref(portals, "app", "/organizations/select") },
    ...(who?.consoles?.partner ? [{ label: "Partner portal", href: portalHref(portals, "partner", "/partner") }] : []),
    { label: "Sign out", onSelect: signOut },
  ];
  return (
    <AdminContext.Provider value={{ permissions: state.permissions }}>
      <SidebarShell
        area="Super Admin"
        context={<span className={consoleStyles.contextPill}>Super Admin</span>}
        groups={adminNav(state.permissions)}
        account={<AccountMenu name={who?.name || who?.email || ""} entries={entries} />}
        banner={banner}
      >
        {children}
      </SidebarShell>
    </AdminContext.Provider>
  );
}

// --- Partner portal --------------------------------------------------------------

const PartnerContext = createContext<{ partner: PortalPartner | null }>({ partner: null });
export const usePartner = () => useContext(PartnerContext);

export function partnerNav(partner: PortalPartner): SidebarGroup[] {
  // The server resolves what this person may do (their role over the partner's
  // capability grants, by the partner's status: partners/resolver.py) and sends
  // it as ``permissions``. A page that could only refuse is not offered; an
  // ended partner reads its money and nothing else, a draft nothing at all.
  // The menu itself - the approved set of top-level entries and the permission
  // each needs - is the one list in ``partnerpanel/menu.ts`` (ADR-046).
  // Navigation convenience only: every request is authorized again.
  const held = new Set(partner.permissions ?? []);
  const can: Can = (...keys) => keys.some((k) => held.has(`platform.partner_${k}`));
  const base = `/partner/${partner.partner_id}`;
  return PARTNER_MENU.map((group) => ({
    title: group.title,
    items: group.entries.map((entry) => ({
      href: `${base}${entry.landing?.(can) ?? entry.path}`,
      label: entry.label,
      icon: entry.icon,
      exact: entry.path === "" || undefined,
      // Current under the entry's whole section, not just the page its link opens.
      match: entry.path === "" ? undefined : `${base}${entry.match ?? entry.path}`,
      when: entry.needs(can),
    })),
  }));
}

/** What each non-active status means for the person signed in (see partners/resolver.py). */
const STATUS_NOTICE: Record<string, string> = {
  draft: "This partner account isn't active yet. Nothing can be done until it is.",
  suspended: "This partner account is suspended. History stays visible; new actions are unavailable.",
  terminated:
    "This partner account has ended. Its commission and payout history stays visible; nothing else is available.",
};

/** The portal layout for one partner. Navigation comes from the partner's
 * **capabilities** - there is no partner type anywhere in this file. */
export function PartnerShell({ partnerId, children }: { partnerId: string; children: React.ReactNode }) {
  const router = usePortalRouter();
  const portals = usePortals();
  const session = useSession();
  const { signOut, banner } = useSignOut();
  const [state, setState] = useState<
    { status: "loading" } | { status: "missing" } | { status: "error" } | { status: "ready"; partner: PortalPartner }
  >({ status: "loading" });

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace("/login");
      return;
    }
    partnerApi
      .portal()
      .then(({ partners }) => {
        const found = partners.find((p) => p.partner_id === partnerId);
        setState(found ? { status: "ready", partner: found } : { status: "missing" });
      })
      .catch(() => setState({ status: "error" }));
  }, [session, router, partnerId]);

  if (session.status === "loading" || state.status === "loading") {
    return <StatusScreen>Loading the partner portal…</StatusScreen>;
  }
  if (session.status === "ready" && session.session.password_change_required) {
    return <ForcedPasswordChange />;
  }
  if (state.status === "missing") {
    return (
      <StatusScreen role="alert">
        <p>You don&apos;t have access to this partner account, or it no longer exists.</p>
      </StatusScreen>
    );
  }
  if (state.status === "error") {
    return (
      <StatusScreen role="alert">
        <p>The partner portal could not be reached right now.</p>
      </StatusScreen>
    );
  }
  const { partner } = state;
  const who = session.status === "ready" ? session.session : null;
  const entries: AccountMenuEntry[] = [
    { label: "Switch partner account", href: "/partner" },
    { label: "My organizations", href: portalHref(portals, "app", "/organizations/select") },
    ...(who?.consoles?.staff ? [{ label: "Super Admin console", href: portalHref(portals, "admin", "/admin") }] : []),
    { label: "Sign out", onSelect: signOut },
  ];
  return (
    <PartnerContext.Provider value={{ partner }}>
      <SidebarShell
        area={`Partner ${partner.display_name}`}
        context={<span className={consoleStyles.contextPill}>Partner · {partner.display_name}</span>}
        groups={partnerNav(partner)}
        account={<AccountMenu name={who?.name || who?.email || ""} entries={entries} />}
        banner={
          <>
            {banner}
            {partner.status !== "active" && (
              <p role="status" className={`w360-badge w360-badge-warning ${consoleStyles.banner}`}>
                {STATUS_NOTICE[partner.status] ?? `This partner account is ${partner.status}.`}
              </p>
            )}
          </>
        }
      >
        {children}
      </SidebarShell>
    </PartnerContext.Provider>
  );
}
