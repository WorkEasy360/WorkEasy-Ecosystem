"use client";

import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
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
import { AdminAccount, AdminTools, roleLabel } from "./AdminTopbar";
import { ADMIN_PAGE_TITLES, adminNav } from "./adminNav";
import { ModuleNav } from "./ModuleNav";
import { Icon } from "@/components/sidebar/icons";
import { ToastProvider } from "./kit/Confirm";
import { ADMIN_TITLE_SUFFIX } from "./kit/Layout";
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

// --- Admin ---------------------------------------------------------------------

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

  // The console's own type scale, controls and page gutter (globals.css,
  // :root[data-console="admin"]) - on <html>, so dialogs drawn on <body> have them too.
  useEffect(() => {
    document.documentElement.dataset.console = "admin";
    return () => {
      delete document.documentElement.dataset.console;
    };
  }, []);

  // The browser tab names the page: "Users · WorkEasy360 Admin". A record's own
  // page (a user, a partner) names itself from its page header.
  useEffect(() => {
    const title = pathname ? ADMIN_PAGE_TITLES[pathname.replace(/\/$/, "") || "/admin"] : undefined;
    if (title) document.title = `${title} · ${ADMIN_TITLE_SUFFIX}`;
  }, [pathname]);

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

  // The server refuses every other call until a temporary password is replaced, so this comes
  // first: otherwise that refusal would read as "you may not use the console".
  if (session.status === "ready" && session.session.password_change_required) {
    return <ForcedPasswordChange />;
  }
  if (session.status === "loading" || state.status === "loading") {
    return <StatusScreen>Loading the admin console…</StatusScreen>;
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
        <p>The admin console could not be reached right now.</p>
      </StatusScreen>
    );
  }
  const who = session.status === "ready" ? session.session : null;
  const external = (href: string) => /^https?:\/\//.test(href);
  const link = (label: string, icon: string, href: string) => ({ label, icon, href, external: external(href) });
  const entries = [
    link("Sign-in preferences", "user", portalHref(portals, "app", "/settings/sign-in")),
    link("My organizations", "building", portalHref(portals, "app", "/organizations/select")),
    ...(who?.consoles?.partner ? [link("Partner portal", "handshake", portalHref(portals, "partner", "/partner"))] : []),
    link("Help & support", "help", portalHref(portals, "app", "/contact")),
    { label: "Sign out", icon: "logout", onSelect: signOut, separator: true },
  ];
  return (
    <AdminContext.Provider value={{ permissions: state.permissions }}>
      <ToastProvider>
        <SidebarShell
          area="Admin Portal"
          variant="admin"
          navTitle={
            <>
              <Icon name="shield" size={16} /> Admin Portal
            </>
          }
          railKey="w360.admin.rail"
          groups={adminNav(state.permissions, pathname ?? "")}
          tools={<AdminTools permissions={state.permissions} />}
          account={
            <AdminAccount
              name={who?.name ?? ""}
              email={who?.email ?? ""}
              role={roleLabel(state.permissions)}
              entries={entries}
            />
          }
          banner={banner}
        >
          <ModuleNav permissions={state.permissions} />
          {children}
        </SidebarShell>
      </ToastProvider>
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

/** Where "Exit Guest Mode" goes: the Admin page the server named, only if it is
 * one of the Admin's partner pages on the Admin's own origin; else the partner's
 * own page in the Admin. */
export function guestReturnUrl(returnUrl: string | undefined, portals: ReturnType<typeof usePortals>, partnerId: string): string {
  const fallback = portalHref(portals, "admin", `/admin/partners/${encodeURIComponent(partnerId)}`);
  if (!returnUrl) return fallback;
  try {
    const base = portals ? portals.admin : window.location.origin;
    const target = new URL(returnUrl, base);
    const home = new URL(base);
    if (target.origin !== home.origin || !/^\/admin\/partners(\/|$)/.test(target.pathname)) return fallback;
    // Single host: a path on this origin, like every other link here.
    return portals ? target.toString() : `${target.pathname}${target.search}`;
  } catch {
    return fallback;
  }
}

/** Guest Mode (Login as Guest, ADR-062): a Super Admin viewing a partner's own
 * dashboard. Said on every page - which partner, of which type, who is looking,
 * until when - with a prominent way out. The server makes it read-only and ends
 * it by itself; this only explains it. Exiting ends the guest view (and this
 * portal's session) on the server, then returns to the Admin page it came from,
 * where the Admin's own session is untouched. */
export function GuestViewBanner({ partner, guest }: { partner: PortalPartner; guest: NonNullable<PortalPartner["guest"]> }) {
  const portals = usePortals();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const ends = new Date(guest.expires_at);
  // The partner's type as the server words it: the workspace it lands in.
  const kind = partner.workspaces?.find((w) => w.key === (partner.level ?? partner.landing))?.label ?? "";
  const viewer = guest.viewer ? guest.viewer.name || guest.viewer.email : "";
  async function exit() {
    setBusy(true);
    setFailed(false);
    let answer: { return_url?: string };
    try {
      answer = await partnerApi.endGuestView();
    } catch {
      // Not ended: stay here and say so, rather than leave the view running
      // behind a page that looks like it was left.
      setBusy(false);
      setFailed(true);
      return;
    }
    window.location.assign(guestReturnUrl(answer.return_url, portals, partner.partner_id));
  }
  return (
    <div role="status" aria-label="Guest Session" className={`w360-badge w360-badge-info ${consoleStyles.banner} ${consoleStyles.notice}`}>
      {/* ADR-063: the specification's wording, then what it means. */}
      <strong>Guest Session — Viewing Partner: {partner.display_name}</strong>
      {kind && ` (${kind})`}. Read only
      {viewer && `, viewing as ${viewer}`}
      {Number.isNaN(ends.getTime()) ? "" : `, until ${ends.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}. Nothing can be changed
      here, and every page you open is recorded.{" "}
      <button type="button" className="w360-btn w360-btn-primary" onClick={() => void exit()} disabled={busy}>
        Exit Guest Session
      </button>
      {failed && <span role="alert"> The guest session could not be ended. Try again.</span>}
    </div>
  );
}

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
    ...(who?.consoles?.staff ? [{ label: "Admin console", href: portalHref(portals, "admin", "/admin") }] : []),
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
                {/* A draft partner's menu is empty: this link is the way in to its application. */}
                {partner.status === "draft" && (
                  <Link href={`/partner/${partner.partner_id}/kyc`} className="w360-btn w360-btn-secondary">
                    Complete your application
                  </Link>
                )}
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
