"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { StatusScreen } from "@/components/StatusScreen";
import { AccountMenu, SidebarShell, type AccountMenuEntry, type SidebarGroup } from "@/components/sidebar/SidebarShell";
import { GuestViewBanner, PartnerContext, useSignOut } from "@/features/controlplane/ControlShell";
import { ForcedPasswordChange } from "@/features/controlplane/ForcedPasswordChange";
import { partnerApi, type PortalPartner } from "@/lib/api/controlPlane";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import consoleStyles from "@/features/console/Console.module.css";
import {
  CONTAINS,
  WORKSPACES,
  entriesFor,
  experienceFromSlug,
  firstAllowedPath,
  isExperienceKey,
  isLocked,
  lockedEntryFor,
  pageAllowed,
  servedPaths,
  tabbedEntryFor,
  unlockLevel,
  workspaceBase,
  workspaceHome,
  type Can,
  type ExperienceKey,
  type WorkspaceDef,
  type WorkspaceEntry,
} from "./experiences";
import { LevelTools, UpgradeDialog } from "./levels";
import { EntryTabs, usePanel } from "./PanelKit";
import { pageFor } from "./workspacePages";
import styles from "./PartnerPanel.module.css";

/**
 * One partner workspace (ADR-047): the shell, the navigation and the page.
 *
 * **The server decides which workspaces exist.** The shell asks the portal API
 * (`/api/v1/partner/portal`) which partner accounts the signed-in person belongs to
 * and which workspaces each one's capabilities open. This one opens only for an
 * account whose server-derived `workspaces` include it; for anyone else - an
 * affiliate who types `/partner/reseller/users` - it shows a refusal and loads
 * nothing else. Nothing in the address, the browser's storage or a cookie says
 * what a person may do.
 *
 * Inside it, an entry is drawn only when the person's server-resolved
 * permissions make its page useful (`experiences.ts`), a page that is not part of
 * the workspace is refused, and every request the page makes is authorized again by
 * the backend, which is what actually protects the data.
 */

const SWITCHER_ORDER: ExperienceKey[] = ["affiliate", "reseller", "white_label"];

const STATUS_NOTICE: Record<string, string> = {
  draft: "This partner account isn't active yet. Nothing can be done until it is.",
  suspended: "This partner account is suspended. History stays visible; new actions are unavailable.",
  terminated: "This partner account has ended. Its commission and payout history stays visible; nothing else is available.",
};

const permissionCheck = (partner: PortalPartner): Can => {
  const held = new Set(partner.permissions ?? []);
  return (...keys) => keys.some((k) => held.has(`platform.partner_${k}`));
};

/** The sidebar for one workspace and one person (2026-11-03: the unified
 * dashboard's fourteen entries). An entry the level does not include is drawn
 * locked and opens `onLocked`; one the person's role cannot use is not drawn. */
export function workspaceNav(
  def: WorkspaceDef,
  partner: PortalPartner,
  onLocked?: (entry: WorkspaceEntry) => void,
): SidebarGroup[] {
  const can = permissionCheck(partner);
  const base = workspaceBase(def.key);
  return def.groups.map((group) => ({
    title: group.title,
    items: group.entries.map((entry) => {
      const locked = isLocked(def, entry);
      return {
        href: `${base}/${entry.landing?.(can) ?? entry.path}`,
        label: entry.label,
        icon: entry.icon,
        // A workspace serves only the pages its entries list, so each entry is
        // current on exactly its own - never on another entry's deeper address.
        exactPaths: [entry.path, ...(entry.also ?? [])].map((p) => `${base}/${p}`),
        when: locked ? true : entry.needs(can),
        locked,
        onSelect: locked && onLocked ? () => onLocked(entry) : undefined,
      };
    }),
  }));
}

function Refusal({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main id="w360-main" className="w360-container">
      <div className={styles.denied} role="alert">
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

type State =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; partners: PortalPartner[] };

export function WorkspaceShell({ experience, children }: { experience: ExperienceKey; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const [lockedEntry, setLockedEntry] = useState<WorkspaceEntry | null>(null);
  const portals = usePortals();
  const session = useSession();
  const { signOut, banner } = useSignOut();
  const [state, setState] = useState<State>({ status: "loading" });
  const def = WORKSPACES[experience];

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace("/login");
      return;
    }
    partnerApi
      .portal()
      .then(({ partners }) => setState({ status: "ready", partners }))
      .catch(() => setState({ status: "error" }));
  }, [session, router]);

  // The partner accounts whose grants open this workspace, as the server says.
  const candidates = useMemo(
    () => (state.status === "ready" ? state.partners.filter((p) => (p.workspaces ?? []).some((w) => w.key === experience)) : []),
    [state, experience],
  );

  // One dashboard (2026-11-03): a partner works at its own level. The address
  // of a level it has grown out of (an upgraded affiliate's old bookmark)
  // opens the same page at its level, which carries every lower page.
  const only = candidates.length === 1 ? candidates[0] : null;
  const higher =
    only && isExperienceKey(only.level) && only.level !== experience && CONTAINS[only.level].includes(experience) ? only.level : null;
  useEffect(() => {
    if (!higher) return;
    const rest = pathname.slice(workspaceBase(experience).length).replace(/^\/+/, "");
    router.replace(`${workspaceBase(higher)}/${rest || "dashboard"}`);
  }, [higher, experience, pathname, router]);

  if (session.status === "loading" || state.status === "loading" || higher) {
    return <StatusScreen>Loading the partner portal…</StatusScreen>;
  }
  if (session.status === "ready" && session.session.password_change_required) return <ForcedPasswordChange />;
  if (state.status === "error") {
    return (
      <StatusScreen role="alert">
        <p>The partner portal could not be reached right now.</p>
      </StatusScreen>
    );
  }
  if (candidates.length > 1) {
    // The address names a workspace, not a partner. Two accounts of this person
    // hold it, so choosing one here would mean guessing - and acting for the wrong
    // partner. Each opens its own panel, which carries the partner in the address.
    return (
      <main id="w360-main" className="w360-container">
        <div className={styles.denied}>
          <h1>Which {def.label} account?</h1>
          <p>More than one of your partner accounts has the {def.label} workspace. Choose the one you mean.</p>
          <ul>
            {candidates.map((c) => (
              <li key={c.partner_id}>
                <Link href={`/partner/${c.partner_id}`}>{c.display_name}</Link>
              </li>
            ))}
          </ul>
        </div>
      </main>
    );
  }
  const partner = candidates[0];
  if (!partner) {
    // The person may belong to partner accounts, just not to one with this
    // workspace: say where theirs opens, and nothing about this one.
    const own = state.partners.find((p) => isExperienceKey(p.landing));
    return (
      <Refusal title={`You don't have access to the ${def.label} workspace.`}>
        <p>This account is not a member of a partner that holds it.</p>
        <p>
          {own && isExperienceKey(own.landing) ? (
            <Link className="w360-btn w360-btn-primary" href={workspaceHome(own.landing)}>
              Open your {WORKSPACES[own.landing].label} workspace
            </Link>
          ) : (
            <Link className="w360-btn w360-btn-secondary" href="/partner">
              Go to your partner accounts
            </Link>
          )}
        </p>
      </Refusal>
    );
  }

  const who = session.status === "ready" ? session.session : null;
  // ADR-053: the switcher reads in the order a partner grows - Affiliate,
  // Reseller, White-label. Presentation only; the server decides which exist.
  // 2026-11-03: one panel - a workspace this one contains (the Affiliate one of an
  // upgraded Reseller) is not a second place to go; its pages are here.
  const workspaces = (partner.workspaces ?? [])
    .filter((w): w is { key: ExperienceKey; label: string } => isExperienceKey(w.key))
    .filter((w) => !CONTAINS[experience].includes(w.key))
    .sort((a, b) => SWITCHER_ORDER.indexOf(a.key) - SWITCHER_ORDER.indexOf(b.key));
  const entries: AccountMenuEntry[] = [
    ...(workspaces.length > 1
      ? workspaces.filter((w) => w.key !== experience).map((w) => ({ label: `Switch to ${w.label}`, href: workspaceHome(w.key) }))
      : []),
    { label: "Switch partner account", href: "/partner" },
    { label: "My organizations", href: portalHref(portals, "app", "/organizations/select") },
    ...(who?.consoles?.staff ? [{ label: "Admin console", href: portalHref(portals, "admin", "/admin") }] : []),
    { label: "Sign out", onSelect: signOut },
  ];
  return (
    <PartnerContext.Provider value={{ partner, base: workspaceBase(experience) }}>
      <SidebarShell
        area={`${def.label} workspace`}
        context={
          <span className={consoleStyles.contextPill}>
            {def.label} · {partner.display_name}
          </span>
        }
        navTitle="Partner panel"
        groups={workspaceNav(def, partner, setLockedEntry)}
        tools={<LevelTools level={experience} />}
        account={<AccountMenu name={who?.name || who?.email || ""} entries={entries} />}
        banner={
          <>
            {banner}
            {partner.guest && <GuestViewBanner partner={partner} guest={partner.guest} />}
            {workspaces.length > 1 && (
              <nav aria-label="Workspace" className={styles.switcher}>
                <span className={styles.muted}>Workspace:</span>
                {workspaces.map((w) => (
                  <Link key={w.key} href={workspaceHome(w.key)} aria-current={w.key === experience ? "page" : undefined}>
                    {w.label}
                  </Link>
                ))}
              </nav>
            )}
            {partner.status !== "active" && (
              <p role="status" className={`w360-badge w360-badge-warning ${consoleStyles.banner} ${consoleStyles.notice}`}>
                {STATUS_NOTICE[partner.status] ?? `This partner account is ${partner.status}.`}
              </p>
            )}
          </>
        }
      >
        {children}
        {lockedEntry && (
          <UpgradeDialog
            current={experience}
            feature={{ label: lockedEntry.label, pitch: lockedEntry.pitch, level: unlockLevel(lockedEntry) }}
            onClose={() => setLockedEntry(null)}
          />
        )}
      </SidebarShell>
    </PartnerContext.Provider>
  );
}

/** The page of the workspace named by the address. */
export function WorkspaceContent({ experience, path }: { experience: ExperienceKey; path: string }) {
  const router = useRouter();
  const { partner, can, base } = usePanel();
  const def = WORKSPACES[experience];
  const clean = path.replace(/^\/+|\/+$/g, "");

  // Where this address really is: the home of the workspace, a friendly alias of a
  // page, or - when the person cannot read the dashboard (an ended partner reads
  // its money and nothing else) - the first page they can.
  let redirect: string | null = null;
  if (clean === "") redirect = `${base}/dashboard`;
  else if (def.aliases[clean] !== undefined) redirect = `${base}/${def.aliases[clean]}`;
  else if (clean === "dashboard" && !pageAllowed(def, "dashboard", can)) {
    const first = firstAllowedPath(def, can);
    redirect = first ? `${base}/${first}` : null;
  }
  useEffect(() => {
    if (redirect) router.replace(redirect);
  }, [redirect, router]);

  if (redirect) return <p aria-live="polite">Opening…</p>;

  const locked = lockedEntryFor(def, clean);
  if (locked) return <LockedPage experience={experience} entry={locked} />;
  if (!servedPaths(def).has(clean) || entriesFor(def, clean).length === 0) {
    return (
      <div className={styles.denied} role="alert">
        <h1>That page is not part of the {def.label} workspace.</h1>
        <p>
          <Link href={`${base}/dashboard`}>Back to the {def.label} dashboard</Link>
        </p>
      </div>
    );
  }
  if (!pageAllowed(def, clean, can)) {
    return (
      <div className={styles.denied} role="alert">
        <h1>You don&apos;t have access to this page.</h1>
        <p>
          Your role in {partner?.display_name ?? "this partner"} doesn&apos;t include it. Ask an administrator of the partner account if you
          need it.
        </p>
        <p>
          <Link href={`${base}/dashboard`}>Back to the dashboard</Link>
        </p>
      </div>
    );
  }
  const page = pageFor(experience, clean);
  if (!page) {
    return (
      <div className={styles.denied} role="alert">
        <h1>That page does not exist.</h1>
      </div>
    );
  }
  // A consolidated entry (2026-11-02): its pages are tabs of one page.
  const owner = tabbedEntryFor(def, clean);
  if (owner?.tabs) {
    const tabs = owner.tabs
      .filter((tab) => !tab.needs || tab.needs(can))
      .map((tab) => ({
        href: `${base}/${tab.path}`,
        label: tab.label,
        current: [tab.path, ...(tab.also ?? [])].map((p) => `${base}/${p}`),
      }));
    return <EntryTabs tabs={tabs}>{page}</EntryTabs>;
  }
  return page;
}

/** A page of an entry the partner's level does not include: what it is, and
 * the way to unlock it - never the page itself. */
function LockedPage({ experience, entry }: { experience: ExperienceKey; entry: WorkspaceEntry }) {
  const [open, setOpen] = useState(false);
  const level = unlockLevel(entry);
  return (
    <div className={styles.denied}>
      <h1>
        <span aria-hidden="true">🔒 </span>
        {entry.label} is part of the {WORKSPACES[level].label} level
      </h1>
      {entry.pitch && <p>{entry.pitch}</p>}
      <p>
        You are a {WORKSPACES[experience].level.name}. Upgrade on the same account to unlock it - nothing you have earned is lost.
      </p>
      <p>
        <button type="button" className="w360-btn w360-btn-primary" onClick={() => setOpen(true)}>
          See upgrade options
        </button>
      </p>
      {open && (
        <UpgradeDialog
          current={experience}
          feature={{ label: entry.label, pitch: entry.pitch, level }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

/** What each `app/partner/<workspace>/[[...slug]]` route renders. */
export function Workspace({ slug, segments }: { slug: string; segments?: string[] }) {
  const experience = experienceFromSlug(slug);
  if (!experience) return null;
  return (
    <WorkspaceShell experience={experience}>
      <WorkspaceContent experience={experience} path={(segments ?? []).join("/")} />
    </WorkspaceShell>
  );
}
