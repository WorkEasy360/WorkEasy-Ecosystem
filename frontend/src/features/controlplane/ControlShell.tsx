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
import { ForcedPasswordChange } from "./ForcedPasswordChange";
import { AdminTools } from "./AdminTopbar";
import { adminNav } from "./adminNav";
import { usePortalRouter } from "@/lib/portalRouter";

export function useSignOut() {
  const router = usePortalRouter();
  // A failed sign-out must not look like a successful one: navigating to
  // /login anyway would resume the still-live session for whoever sits down
  // at this browser next.
  const [failed, setFailed] = useState(false);
  const signOutTo = (then: string) => {
    setFailed(false);
    logout().then(
      () => router.replace(then),
      () => setFailed(true),
    );
  };
  const signOut = () => signOutTo("/login");
  const banner = failed ? (
    <p role="alert" className={consoleStyles.banner}>
      You are still signed in: signing out didn&apos;t complete. Try again, or close this browser.
    </p>
  ) : null;
  return { signOut, signOutTo, banner };
}

// --- Super Admin -----------------------------------------------------------------

/** Exported so a screen can be rendered under a chosen permission set in tests. */
export const AdminContext = createContext<{ permissions: string[] }>({ permissions: [] });
export const useAdmin = () => useContext(AdminContext);
export const can = (permissions: string[], key: string) => permissions.includes(key);

type AdminState =
  | { status: "loading" }
  | { status: "denied" }
  | { status: "error" }
  | { status: "ready"; permissions: string[] };

export { adminNav };

/** "You don't have administrator access" - the answer for a signed-in account
 * the server refused (no staff identity, or no StaffGrant in force). It names
 * no permission and shows nothing from the console. All portals share one
 * sign-in, so this usually means another account (a partner's, a customer's)
 * is signed in: the first action switches to the administrator sign-in and
 * comes back to the page that was asked for. */
export function AdminAccessDenied({
  email,
  onSwitchAccount,
  banner,
}: {
  email?: string;
  onSwitchAccount: () => void;
  banner?: React.ReactNode;
}) {
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
          <p>
            {email ? (
              <>
                You are signed in as <strong>{email}</strong>, which is not an administrator account.
              </>
            ) : (
              "This account is not an administrator account."
            )}{" "}
            Sign in with your administrator account to open the Admin Console.
          </p>
        </div>
        {banner}
        <div className={loginStyles.deniedActions}>
          <button type="button" className="w360-btn w360-btn-primary" onClick={onSwitchAccount}>
            Sign in as administrator
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={asUser}>
            Continue as User
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
  const { signOut, signOutTo, banner } = useSignOut();

  useEffect(() => {
    if (session.status !== "ready") return;
    // Sign in as an administrator, then come back to this page. The mode and
    // the page are in the address only; the console still asks the server
    // below whether this person may enter.
    const signIn = () => router.replace(adminSignInUrl(`${pathname ?? "/admin"}${window.location.search}`));
    if (!session.session.authenticated) {
      signIn();
      return;
    }
    adminApi
      .session()
      .then((s) => setState({ status: "ready", permissions: s.permissions }))
      .catch((err: unknown) => {
        // 401: the session ended (signing out on any portal ends it on all of
        // them) - that is "sign in again", not "no administrator access".
        if (err instanceof ApiError && err.status === 401) signIn();
        else setState({ status: err instanceof ApiError && err.status === 403 ? "denied" : "error" });
      });
  }, [session, router, pathname]);

  if (session.status === "loading" || state.status === "loading") {
    return <StatusScreen>Loading the control plane…</StatusScreen>;
  }
  if (state.status === "denied") {
    // Signed in, but the server says this account may not use the console.
    // Nothing from the console has been loaded; the person may carry on into
    // their own workspace with the same sign-in.
    return (
      <AdminAccessDenied
        email={session.status === "ready" ? session.session.email : undefined}
        onSwitchAccount={() => signOutTo(adminSignInUrl(`${pathname ?? "/admin"}${window.location.search}`))}
        banner={banner}
      />
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
    { label: "My organizations", href: portalHref(portals, "app", "/organizations/select") },
    ...(who?.consoles?.partner ? [{ label: "Partner portal", href: portalHref(portals, "partner", "/partner") }] : []),
    { label: "Sign out", onSelect: signOut },
  ];
  return (
    <AdminContext.Provider value={{ permissions: state.permissions }}>
      <SidebarShell
        area="Super Admin"
        variant="admin"
        navTitle="Admin panel"
        groups={adminNav(state.permissions)}
        tools={<AdminTools permissions={state.permissions} />}
        account={<AccountMenu name={who?.name || who?.email || ""} entries={entries} showName />}
        banner={banner}
      >
        {children}
      </SidebarShell>
    </AdminContext.Provider>
  );
}

// --- Partner portal --------------------------------------------------------------

/** The signed-in partner and, inside a workspace (ADR-047), the address prefix its
 * pages live under (`/partner/reseller`). Without `base` the generic panel's
 * `/partner/<id>` applies. Display only: it decides no permission. */
export interface PartnerContextValue {
  partner: PortalPartner | null;
  base?: string;
}

export const PartnerContext = createContext<PartnerContextValue>({ partner: null });
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
/** A staff member viewing a partner's portal as a guest (All Partners → Guest
 * login): said on every page, with the way out. The server makes it read-only
 * and ends it by itself; this only explains it. Exiting ends the guest view
 * (and this portal's session) on the server, then returns to the partner's
 * page in the Super Admin console. */
export function GuestViewBanner({ partner, guest }: { partner: PortalPartner; guest: NonNullable<PortalPartner["guest"]> }) {
  const portals = usePortals();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const ends = new Date(guest.expires_at);
  async function exit() {
    setBusy(true);
    setFailed(false);
    try {
      await partnerApi.endGuestView();
    } catch {
      // Not ended: stay here and say so, rather than leave the view running
      // behind a page that looks like it was left.
      setBusy(false);
      setFailed(true);
      return;
    }
    window.location.assign(portalHref(portals, "admin", `/admin/partners/${encodeURIComponent(partner.partner_id)}`));
  }
  return (
    <div role="status" className={`w360-badge w360-badge-info ${consoleStyles.banner} ${consoleStyles.notice}`}>
      Viewing Partner Portal as {partner.display_name}. Read-only
      {Number.isNaN(ends.getTime()) ? "" : `, ends at ${ends.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}: you cannot change
      anything here.{" "}
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void exit()} disabled={busy}>
        Exit Guest Session
      </button>
      {failed && <span role="alert"> The guest session could not be ended. Try again.</span>}
    </div>
  );
}

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
            {partner.guest && <GuestViewBanner partner={partner} guest={partner.guest} />}
            {partner.status !== "active" && (
              <p role="status" className={`w360-badge w360-badge-warning ${consoleStyles.banner} ${consoleStyles.notice}`}>
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
