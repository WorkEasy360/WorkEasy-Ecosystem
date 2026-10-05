"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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
import consoleStyles from "@/features/console/Console.module.css";
import { StaffMfaGate } from "./StaffMfaGate";

function useSignOut() {
  const router = useRouter();
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
      ],
    },
    {
      // Phase 10: one Partners section instead of a flat "Agencies & partners"
      // entry. Reseller and White-label are ways of looking at the same partner
      // accounts (their capabilities), not separate kinds of account.
      title: "Partners",
      items: [
        { href: "/admin/partners", label: "Overview", icon: "handshake", exact: true, when: can(p, "platform.staff_partner.view") },
        { href: "/admin/partners/resellers", label: "Resellers", icon: "store", when: can(p, "platform.staff_partner.view") },
        { href: "/admin/partners/white-label", label: "White-label", icon: "palette", when: can(p, "platform.staff_partner.view") },
        { href: "/admin/partners/customers", label: "Customers", icon: "building", when: can(p, "platform.staff_partner.view") },
        { href: "/admin/partners/products", label: "Products & Pricing", icon: "tag", when: can(p, "platform.staff_partner.view") },
        { href: "/admin/commissions", label: "Commissions", icon: "gift", when: can(p, "platform.staff_commission.view") },
        { href: "/admin/payouts", label: "Payouts", icon: "receipt", when: can(p, "platform.staff_commission.view") },
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

/** The control-plane layout: a separate shell from any organization's, with
 * navigation derived from the permissions the staff member actually holds. */
export function AdminShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const session = useSession();
  const [state, setState] = useState<AdminState>({ status: "loading" });
  // Bumped after a successful verification to ask the backend again.
  const [attempt, setAttempt] = useState(0);
  const { signOut, banner } = useSignOut();

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace("/login");
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
  }, [session, router, attempt]);

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
    return (
      <StatusScreen role="alert">
        <p>This area is for Workeasy360 staff.</p>
      </StatusScreen>
    );
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
    { label: "My organizations", href: "/organizations/select" },
    ...(who?.consoles?.partner ? [{ label: "Partner portal", href: "/partner" }] : []),
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
  // What the server will answer, by status (partners/resolver.py): an ended
  // partner can still read the money it is owed and nothing else, and one that
  // was never activated can do nothing. Offering a page that can only refuse is
  // not a courtesy.
  const ended = partner.status === "terminated";
  const inactive = partner.status === "draft";
  const grants = (capability: string) => partner.capabilities.includes(capability);
  const has = (capability: string) => grants(capability) && !ended && !inactive;
  const money = (capability: string) => grants(capability) && !inactive;
  const base = `/partner/${partner.partner_id}`;
  return [
    { items: [{ href: base, label: "Overview", icon: "dashboard", exact: true }] },
    {
      title: "Customers",
      items: [
        {
          href: `${base}/customers`,
          label: "Customers",
          icon: "building",
          when: has("customer.provision") || has("customer.manage") || has("subscription.sell") || has("subscription.manage"),
        },
        { href: `${base}/products`, label: "Products & plans", icon: "tag", when: has("pricing.view") || has("subscription.sell") },
      ],
    },
    {
      title: "Earnings",
      items: [
        { href: `${base}/referrals`, label: "Referrals", icon: "gift", when: has("referral.create") || has("referral.view") },
        { href: `${base}/commissions`, label: "Commissions", icon: "currency", when: money("commission.view") },
        { href: `${base}/payouts`, label: "Payouts", icon: "receipt", when: money("payout.view") },
      ],
    },
    {
      title: "Brand",
      items: [
        {
          href: `${base}/white-label`,
          label: "Set up white-label",
          icon: "palette",
          when: has("branding.manage") && has("domain.manage"),
        },
        { href: `${base}/branding`, label: "Branding", icon: "palette", when: has("branding.manage") },
        { href: `${base}/domains`, label: "Domains", icon: "network", when: has("domain.manage") },
      ],
    },
  ];
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
  const router = useRouter();
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
    { label: "My organizations", href: "/organizations/select" },
    ...(who?.consoles?.staff ? [{ label: "Super Admin console", href: "/admin" }] : []),
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
