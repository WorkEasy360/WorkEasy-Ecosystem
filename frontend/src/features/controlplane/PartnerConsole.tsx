"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import {
  partnerConsoleApi,
  type ListCompletion,
  type OverviewCard,
  type ResellerCommission,
  type ResellerRow,
  type SetupStatus,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { GUEST_PERMISSION, ManageGuestAccess, currentReturnPath, guestAvailable, guestOutcomeMessage, loginAsGuest } from "./GuestAccess";
import { ExperienceLabels, PrimaryType, TypeBadges } from "./labels";
import { LoadGate, Status, autoReason, money, useAction, useLoad, usePaged } from "./ui";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { CATEGORY_PATH, categoryHref, isCategory, type PartnerCategory } from "./partnerCategories";
import {
  Alert,
  Avatar,
  Badge,
  ButtonLink,
  DataTable,
  EmptyState,
  FilterBar,
  IdCell,
  Page,
  PageHeader,
  SearchInput,
  SectionHeader,
  Select,
  StatCard,
  Tooltip,
  exactTime,
  formatDate,
  formatRelative,
  kit,
  useConfirm,
  type Column,
  type MenuItem,
  type StatTone,
} from "./kit";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The Partners area (ADR-048): the Partner overview and All partners.
 *
 * Nothing here decides what a partner "is": the type badges come from the
 * server, computed from the capabilities the partner holds, and a partner
 * holding several shows several. The Partner type filter is a server-side filter
 * on those same grants, so a combined partner is in every view it belongs to.
 * Nothing here computes money or completion either - every figure is the
 * server's.
 */

// --- shared bits ----------------------------------------------------------------------

export { ExperienceLabels };

/** Per-currency ledger figures: what the server summed, never added across currencies. */
export function CommissionSummary({ rows }: { rows: ResellerCommission[] }) {
  if (rows.length === 0) return <span className={styles.muted}>No commission yet</span>;
  return (
    <div className={local.stackTight}>
      {rows.map((r) => (
        <span key={r.currency}>
          {money(r.balance_minor, r.currency)} earned
          <span className={styles.muted}> · {money(r.unpaid_minor, r.currency)} unpaid</span>
        </span>
      ))}
    </div>
  );
}

const SETUP_TEXT: Record<SetupStatus, string> = {
  setup_required: "Setup required",
  ready: "Ready",
  active: "Active",
  suspended: "Suspended",
  terminated: "Terminated",
};

const SETUP_TONE = { setup_required: "warning", ready: "info" } as const;

/** A partner's state as the console names it: a draft is "Setup required" until
 * its profile is complete, then "Ready" (for staff to activate). */
export function SetupStatusBadge({ value }: { value: SetupStatus }) {
  if (value !== "setup_required" && value !== "ready") return <Status value={value} />;
  return (
    <Badge tone={SETUP_TONE[value]} dot>
      {SETUP_TEXT[value]}
    </Badge>
  );
}

/** The profile completion as words first ("35% · Incomplete") with a bar that
 * only reinforces them. */
export function CompletionMeter({ value, label }: { value: Pick<ListCompletion, "percent" | "complete">; label?: string }) {
  return (
    <div className={local.meter}>
      <span>
        {value.percent}% <span className={styles.muted}>· {value.complete ? "Complete" : "Incomplete"}</span>
        {label && <span className="w360-visually-hidden"> {label}</span>}
      </span>
      <span className={local.meterTrack} aria-hidden="true">
        <span className={`${value.complete ? local.meterDone : local.meterFill} ${local[`w${Math.round(Math.max(0, Math.min(100, value.percent)) / 10) * 10}`]}`} />
      </span>
    </div>
  );
}

/** The open upgrade request's state, in words (ADR-049). */
const UPGRADE_TEXT: Record<string, string> = {
  requested: "Requested",
  profile_required: "Profile required",
  pending_approval: "Pending approval",
};
const upgradeText = (status: string): string => UPGRADE_TEXT[status] ?? status.replace(/_/g, " ");
const upgradeTarget = (key: string): string => (key === "white_label" ? "White-label" : key === "reseller" ? "Reseller" : key);

// --- the overview -----------------------------------------------------------------------

/** Where each card's number can be inspected: All partners, filtered. */
const CARD_LINKS: Record<string, string> = {
  total_partners: "/admin/partners/all",
  active_partners: "/admin/partners/all?status=active",
  // ADR-062: each type card opens its own page (Partners → Reseller, White Label,
  // Affiliate), whose list is exactly what the card counts.
  resellers: CATEGORY_PATH.reseller,
  white_label: CATEGORY_PATH.white_label,
  affiliates: CATEGORY_PATH.affiliate,
  suspended_partners: "/admin/partners/all?status=suspended",
  profiles_incomplete: "/admin/partners/all?profile=incomplete",
};

const CARD_NOTES: Record<string, string> = {
  total_partners: "Every partner account, in any state.",
  active_partners: "Partners in the active state.",
  resellers: "Can sell and manage customers.",
  white_label: "Can use their own brand and domain.",
  affiliates: "Can refer customers.",
  suspended_partners: "Partners paused by WorkEasy360.",
  profiles_incomplete: "Profiles that still need something.",
};

const CARD_ICONS: Record<string, [string, StatTone]> = {
  total_partners: ["handshake", "green"],
  active_partners: ["checkCircle", "green"],
  resellers: ["store", "blue"],
  white_label: ["palette", "purple"],
  affiliates: ["gift", "orange"],
  suspended_partners: ["pause", "red"],
  profiles_incomplete: ["alert", "orange"],
};

function OverviewCardView({ card }: { card: OverviewCard }) {
  const [icon, tone] = CARD_ICONS[card.key] ?? ["handshake", "green"];
  return (
    <StatCard
      href={CARD_LINKS[card.key] ?? "/admin/partners/all"}
      label={card.label}
      value={card.count}
      icon={icon}
      tone={tone}
      note={CARD_NOTES[card.key]}
    />
  );
}

/** The Partners area's one way to create a partner (also in the top bar's New menu).
 * On a type's own page the form opens with that type chosen. */
export function NewPartnerButton({ category }: { category?: PartnerCategory } = {}) {
  const { permissions } = useAdmin();
  if (!can(permissions, "platform.staff_partner.manage")) return null;
  return (
    <ButtonLink variant="primary" icon="plus" href={category ? `/admin/partners/new?type=${category}` : "/admin/partners/new"}>
      New partner
    </ButtonLink>
  );
}

/** A partner in a table: avatar, name (its page) and a second line. */
function PartnerCell({ id, name, sub }: { id: string; name: string; sub?: React.ReactNode }) {
  return (
    <span className={kit.person}>
      <Avatar name={name} />
      <span className={kit.cellStack}>
        <Link href={`/admin/partners/${id}`} className={kit.cellLink}>
          {name}
        </Link>
        {sub && <span className={kit.cellSub}>{sub}</span>}
      </span>
    </span>
  );
}

interface RecentPartner {
  id: string;
  display_name: string;
  created_at: string;
  experience: { key: string; label: string }[];
  profile_completion: Pick<ListCompletion, "percent" | "complete" | "setup_status">;
}

const RECENT_COLUMNS: Column<RecentPartner>[] = [
  { key: "partner", header: "Partner", render: (p) => <PartnerCell id={p.id} name={p.display_name} />, value: (p) => p.display_name },
  { key: "type", header: "Type", render: (p) => <TypeBadges labels={p.experience} /> },
  {
    key: "profile",
    header: "Profile",
    render: (p) => <CompletionMeter value={p.profile_completion} label={`profile of ${p.display_name}`} />,
    value: (p) => p.profile_completion.percent,
  },
  { key: "status", header: "Status", render: (p) => <SetupStatusBadge value={p.profile_completion.setup_status} /> },
  { key: "created", header: "Created", render: (p) => formatDate(p.created_at), value: (p) => p.created_at },
];

/** Partners → Partner overview: factual counts and the newest partners. */
export function PartnersOverview() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => partnerConsoleApi.overview(), []);
  const manage = can(permissions, "platform.staff_partner.manage");

  return (
    <Page>
      <PageHeader title="Partner overview" subtitle="How many partners there are, by type and state." primary={<NewPartnerButton />} />
      <LoadGate load={load} what="the partner overview">
        {(d) => (
          <>
            {(d.upgrades_pending ?? 0) > 0 && (
              <Alert
                tone="info"
                title={`${d.upgrades_pending} upgrade ${d.upgrades_pending === 1 ? "request is" : "requests are"} waiting for a decision.`}
                actions={
                  <ButtonLink href="/admin/partners/all" size="sm" variant="primary">
                    Open all partners
                  </ButtonLink>
                }
              >
                Partners with a request carry an &ldquo;Upgrade&rdquo; badge.
              </Alert>
            )}
            <section aria-labelledby="partner-kpis">
              <h2 id="partner-kpis" className="w360-visually-hidden">
                Key figures
              </h2>
              <div className={local.statGrid4}>
                {d.cards.map((card) => (
                  <OverviewCardView key={card.key} card={card} />
                ))}
              </div>
            </section>
            <section aria-labelledby="recent-partners" className={styles.stack}>
              <SectionHeader
                id="recent-partners"
                title="Recent partners"
                description={`As of ${formatDate(d.as_of)}.`}
                actions={
                  <ButtonLink href="/admin/partners/all" size="sm">
                    View all partners
                  </ButtonLink>
                }
              />
              <DataTable
                caption="Recent partners"
                columns={RECENT_COLUMNS}
                rows={d.recent_partners as RecentPartner[]}
                rowKey={(p) => p.id}
                rowHref={(p) => `/admin/partners/${p.id}`}
                pageSize={0}
                maxHeight={false}
                empty={
                  <EmptyState
                    icon="handshake"
                    title="No partners yet"
                    hint="Partners you create appear here."
                    action={manage ? { label: "New partner", href: "/admin/partners/new" } : undefined}
                  />
                }
              />
            </section>
          </>
        )}
      </LoadGate>
    </Page>
  );
}

// --- All partners ------------------------------------------------------------------------------

const TYPE_OPTIONS = [
  { value: "", label: "All types" },
  { value: "reseller", label: "Reseller" },
  { value: "white_label", label: "White-label" },
  { value: "affiliate", label: "Affiliate" },
];
const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "suspended", label: "Suspended" },
  { value: "draft", label: "Draft" },
  { value: "terminated", label: "Terminated" },
];
const PROFILE_OPTIONS = [
  { value: "", label: "Any profile" },
  { value: "complete", label: "Complete" },
  { value: "incomplete", label: "Incomplete" },
];

const KYC_OPTIONS = [
  { value: "", label: "Any KYC" },
  { value: "ready", label: "Approved, not yet active" },
  { value: "approved", label: "KYC approved" },
  { value: "not_approved", label: "KYC not approved" },
  { value: "legacy", label: "Active before KYC" },
];

/** Can this draft be activated as far as KYC goes? The server checks again. */
const kycAllowsActivation = (r: ResellerRow) => !r.kyc || !r.kyc.required || r.kyc.status === "approved";

const pick = (raw: string | null, options: { value: string }[]) => (options.some((o) => o.value === raw) ? (raw as string) : "");

/** The reason recorded when a partner's status is changed from its row. Nobody
 * has to type one (the owner's call, 2026-10-27); the server still requires a
 * reason, so the action says where it came from unless a reason was written. */
const SWITCH_REASON = {
  activate: "Activated from All Partners",
  reactivate: "Reactivated from All Partners",
  suspend: "Suspended from All Partners",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The partner's balance: its commission ledger per currency, as the server
 * summed it (never added across currencies). */
function wallet(r: ResellerRow) {
  if (r.commission.length === 0) return money(0, r.payout_currency);
  return r.commission.map((c) => money(c.balance_minor, c.currency)).join(" · ");
}

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** The KYC status in a few words, for a badge. */
function kycWords(status: string): string {
  return (
    {
      draft: "not sent",
      submitted: "submitted",
      under_review: "under review",
      correction_required: "correction required",
      approved: "approved",
      rejected: "rejected",
    }[status] ?? status
  );
}

type CreatedInvite = { state: "queued" | "deferred" | "not_sent"; local: boolean } | null;

/** What the create answer said about the invitation, carried on the address. */
function inviteFromParam(raw: string | null, mail: string | null): CreatedInvite {
  if (raw !== "queued" && raw !== "deferred" && raw !== "not_sent") return null;
  return { state: raw, local: mail !== "smtp" };
}

/** After New partner: the partner is a draft, and the invitation is described as
 * exactly what happened - queued for email, held until activation, not sent, or
 * written to a development server's log - never as delivered. */
function CreatedBanner({ partnerId, invite }: { partnerId: string; invite: CreatedInvite }) {
  const setup = <Link href={`/admin/partners/${partnerId}/profile`}>Complete setup</Link>;
  const people = <Link href={`/admin/partners/${partnerId}?tab=users`}>the partner&apos;s page</Link>;
  if (invite?.state === "not_sent") {
    return (
      <Alert tone="warning" title="Partner created, but no invitation was sent.">
        Open {people} and resend the invitation. The partner is a draft with no partner rights.
      </Alert>
    );
  }
  if (invite?.state === "queued" && invite.local) {
    return (
      <Alert tone="warning" title="Partner created. Email is not configured on this server.">
        The invitation was queued, but this server prints email to its log instead of sending it, so it will not reach the partner&apos;s
        inbox. The partner is a draft until its KYC is approved and an Admin activates it. {setup}.
      </Alert>
    );
  }
  if (invite?.state === "queued") {
    return (
      <Alert tone="success" title="Partner created. Invitation queued for email.">
        It has not been confirmed as sent yet: {people} shows when the mail server accepts it, or if it fails. The partner can sign in to send
        its KYC; it stays a draft until the KYC is approved and an Admin activates it. {setup}.
      </Alert>
    );
  }
  if (invite?.state === "deferred") {
    return (
      <Alert tone="success" title="Partner created.">
        Its invitation is emailed when the partner is activated (KYC approval is switched off on this server). {setup}.
      </Alert>
    );
  }
  return (
    <Alert tone="success" title="Partner created successfully.">
      It is a draft with no partner rights. {setup}, then activate it from its row menu once its KYC is approved.
    </Alert>
  );
}

const CATEGORY_TITLE: Record<PartnerCategory, string> = {
  affiliate: "Affiliate partners",
  reseller: "Reseller partners",
  white_label: "White Label partners",
};

const CATEGORY_SUBTITLE: Record<PartnerCategory, string> = {
  affiliate: "Every partner whose type is Affiliate, with its status and balance.",
  reseller: "Every partner whose type is Reseller, with its status and balance.",
  white_label: "Every partner whose type is White-label, with its status and balance.",
};

const CATEGORY_NOUN: Record<PartnerCategory, string> = {
  affiliate: "affiliate partners",
  reseller: "reseller partners",
  white_label: "white-label partners",
};

/** Partners → All partners: one table, filtered by the server (search, partner
 * type, status, profile). The filters are kept in the address so a filtered
 * view can be linked to and survives a reload. A partner's actions are in its
 * row's menu; a status change asks to confirm.
 *
 * ADR-062: Partners → Affiliate, Reseller and White Label are this same table
 * with `category` fixed - the server lists only the partners of that type, each
 * partner under exactly one. "Partner type" moves between those pages (and back
 * to All partners), so the filter always means the type the Type column shows. */
export function AllPartnersList({ category }: { category?: PartnerCategory } = {}) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/admin/partners/all";
  const [filters, setFilters] = useState({
    q: (params.get("q") ?? "").slice(0, 80),
    type: category ?? "",
    status: pick(params.get("status"), STATUS_OPTIONS),
    profile: pick(params.get("profile"), PROFILE_OPTIONS),
    kyc: pick(params.get("kyc"), KYC_OPTIONS),
  });
  const manage = can(permissions, "platform.staff_partner.manage");
  const canGuest = can(permissions, GUEST_PERMISSION);
  const canKyc = can(permissions, "platform.staff_kyc.view");
  const [guestAccess, setGuestAccess] = useState<ResellerRow | null>(null);
  // Login as Guest came back with an outcome (a used or expired link, or refused).
  const [guestOutcome] = useState(() => guestOutcomeMessage(params.get("guest")));
  const portals = usePortals();

  // An old address (`/admin/partners/all?type=reseller`, a bookmark or the
  // overview's old link) opens the type's own page, keeping the other filters.
  // Only there: on New partner, `?type=` chooses the form's type.
  const legacyType = !category && pathname === "/admin/partners/all" ? params.get("type") : null;
  useEffect(() => {
    if (legacyType && isCategory(legacyType)) {
      router.replace(categoryHref(legacyType, Object.fromEntries(params.entries())));
    }
  }, [legacyType, params, router]);
  const confirm = useConfirm();
  // New partner lands here with the new partner's id: the banner says where its
  // setup continues.
  const createdParam = params.get("created") ?? "";
  const [created] = useState(UUID_RE.test(createdParam) ? createdParam : "");
  // How the invitation went, as the create answer said (never claimed when not sent).
  const [createdInvite] = useState(() => inviteFromParam(params.get("invite"), params.get("mail")));

  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.resellers({
        scope: "all",
        q: filters.q || undefined,
        category: category || undefined,
        status: filters.status || undefined,
        profile: filters.profile || undefined,
        kyc: filters.kyc || undefined,
        cursor,
      }),
    [filters.q, category, filters.status, filters.profile, filters.kyc],
  );
  const { act, busy, feedback } = useAction(() => paged.reload());

  function apply(next: typeof filters) {
    const { type, ...rest } = next;
    if (type !== (category ?? "")) {
      // Partner type is the page: Affiliate, Reseller, White Label or All partners.
      router.push(categoryHref(isCategory(type) ? type : null, rest));
      return;
    }
    setFilters(next);
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(rest)) if (value) query.set(key, value);
    const text = query.toString();
    router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
  }

  const panelUrl = (r: ResellerRow) => {
    const path = `/partner/${r.id}`;
    return portals ? portalHref(portals, "partner", path) : `${window.location.origin}${path}`;
  };

  const changeStatus = (r: ResellerRow, target: "active" | "suspended") => {
    const kind = target === "suspended" ? "suspend" : r.status === "draft" ? "activate" : "reactivate";
    const verb = kind === "suspend" ? "Suspend" : kind === "activate" ? "Activate" : "Reactivate";
    const where = "All partners";
    confirm.ask({
      title: `${verb} ${r.display_name}?`,
      message:
        kind === "suspend"
          ? "The partner and its people lose access to the partner portal until it is reactivated. Its customers are not touched."
          : "The partner's administrator can sign in to the partner portal.",
      confirmLabel: `${verb} partner`,
      danger: kind === "suspend",
      reason: true,
      actionName: verb,
      where,
      onConfirm: (reason) => {
        const text = reason === autoReason(verb, where) ? SWITCH_REASON[kind] : reason;
        void act(
          () => adminApi.setPartnerStatus(r.id, target, text, r.version),
          kind === "suspend" ? `${r.display_name} suspended.` : kind === "activate" ? `${r.display_name} activated.` : `${r.display_name} reactivated.`,
        );
      },
    });
  };

  // Login as Guest (ADR-062): the server opens a read-only, time-limited view of
  // exactly this partner and the browser goes to its dashboard.
  const guest = (r: ResellerRow) =>
    act(
      () => loginAsGuest(r.id, category ? CATEGORY_TITLE[category] : "All Partners", currentReturnPath(), portals),
      `Opening ${r.display_name}'s dashboard in Guest Mode…`,
    );

  const rowMenu = (r: ResellerRow): MenuItem[] => {
    const url = panelUrl(r);
    return [
      { label: "View partner", icon: "user", href: `/admin/partners/${r.id}` },
      { label: r.profile_completion.complete ? "Edit setup" : "Complete setup", icon: "settings", href: `/admin/partners/${r.id}/profile` },
      { label: "Manage KYC", icon: "shield", href: `/admin/partners/applications/${r.id}`, when: canKyc },
      { label: "Open partner panel", icon: "external", href: url, external: true },
      { label: "Copy panel link", icon: "copy", onSelect: () => void navigator.clipboard?.writeText(url) },
      {
        // A draft has no dashboard yet; the server refuses it too.
        label: guestAvailable(r.status) ? "Login as Guest" : "Login as Guest (once active)",
        icon: "login",
        onSelect: () => void guest(r),
        when: canGuest,
        disabled: busy || !guestAvailable(r.status),
      },
      { label: "Manage Guest Access", icon: "eye", onSelect: () => setGuestAccess(r) },
      { label: "Suspend", icon: "pause", danger: true, separator: true, when: manage && r.status === "active", onSelect: () => changeStatus(r, "suspended") },
      { label: "Reactivate", icon: "play", separator: true, when: manage && r.status === "suspended", onSelect: () => changeStatus(r, "active") },
      {
        label:
          r.administrator === null
            ? "Activate (add an administrator first)"
            : !kycAllowsActivation(r)
              ? "Activate (KYC must be approved first)"
              : "Activate",
        icon: "play",
        separator: true,
        when: manage && r.status === "draft",
        // The server refuses to activate a partner nobody can act for, or a
        // draft whose KYC application is not approved.
        disabled: r.administrator === null || !kycAllowsActivation(r),
        onSelect: () => changeStatus(r, "active"),
      },
    ];
  };

  const columns: Column<ResellerRow>[] = [
    {
      key: "partner",
      header: "Partner",
      value: (r) => r.display_name,
      render: (r) => (
        <PartnerCell
          id={r.id}
          name={r.display_name}
          sub={
            <span className={local.badgeLine}>
              <span className={kit.trunc} title={r.administrator?.email ?? undefined}>
                {r.administrator?.email ?? "No administrator"}
              </span>
              <IdCell value={r.id} label={`Copy partner ID of ${r.display_name}`} />
            </span>
          }
        />
      ),
    },
    { key: "type", header: "Type", value: (r) => r.primary_experience, render: (r) => <PrimaryType value={r.primary_experience} /> },
    {
      key: "status",
      header: "Status",
      value: (r) => r.profile_completion.setup_status,
      render: (r) => (
        <span className={local.badgeLine}>
          <SetupStatusBadge value={r.profile_completion.setup_status} />
          {r.kyc?.legacy && (
            <Tooltip content="Active before KYC was required. It keeps its status; no KYC approval is on file.">
              <span tabIndex={0} className={`${kit.badge} ${kit["tone-neutral"]}`}>
                Active before KYC
              </span>
            </Tooltip>
          )}
          {r.kyc?.ready_to_activate && <span className={`${kit.badge} ${kit["tone-info"]}`}>KYC approved</span>}
          {r.status === "draft" && r.kyc?.required && !r.kyc.ready_to_activate && (
            <Link
              href={`/admin/partners/applications/${r.id}`}
              className={`${kit.badge} ${kit["tone-warning"]}`}
              aria-label={`KYC ${kycWords(r.kyc.status)} - ${r.display_name}`}
            >
              KYC {kycWords(r.kyc.status)}
            </Link>
          )}
          {r.upgrade && (
            <Link
              href={`/admin/partners/${r.id}?tab=users`}
              className={`${kit.badge} ${r.upgrade.status === "pending_approval" ? kit["tone-warning"] : kit["tone-neutral"]}`}
              aria-label={`Upgrade: ${upgradeText(r.upgrade.status)}, to ${upgradeTarget(r.upgrade.to)} - ${r.display_name}`}
            >
              Upgrade: {upgradeText(r.upgrade.status)}
            </Link>
          )}
        </span>
      ),
    },
    {
      key: "country",
      header: "Country",
      value: (r) => r.country,
      render: (r) => (r.country ? <abbr title={countryName(r.country)}>{r.country}</abbr> : "—"),
    },
    { key: "currency", header: "Currency", value: (r) => r.payout_currency, render: (r) => r.payout_currency },
    { key: "wallet", header: "Earnings", align: "right", value: (r) => r.commission[0]?.balance_minor ?? 0, render: (r) => wallet(r) },
    {
      key: "login",
      header: "Last login",
      value: (r) => r.administrator?.last_login_at ?? null,
      render: (r) =>
        r.administrator?.last_login_at ? (
          <Tooltip content={exactTime(r.administrator.last_login_at)}>
            <span tabIndex={0}>{formatRelative(r.administrator.last_login_at)}</span>
          </Tooltip>
        ) : (
          <span className={styles.muted}>Never</span>
        ),
    },
    { key: "joined", header: "Joined", value: (r) => r.created_at, render: (r) => formatDate(r.created_at) },
  ];

  const filtered = Boolean(filters.q || (!category && filters.type) || filters.status || filters.profile || filters.kyc);
  const select = (key: "type" | "status" | "profile" | "kyc", label: string, options: { value: string; label: string }[]) => (
    <Select label={label} value={filters[key]} options={options} onChange={(value) => apply({ ...filters, [key]: value })} />
  );

  return (
    <Page>
      <PageHeader
        title={category ? CATEGORY_TITLE[category] : "All partners"}
        subtitle={category ? CATEGORY_SUBTITLE[category] : "Every partner account, with its type, status and balance."}
        primary={<NewPartnerButton category={category} />}
      />
      {created && <CreatedBanner partnerId={created} invite={createdInvite} />}
      {guestOutcome && <Alert tone="warning" title={guestOutcome} />}
      <FilterBar
        label="Filter partners"
        onClear={filtered ? () => apply({ q: "", type: category ?? "", status: "", profile: "", kyc: "" }) : undefined}
      >
        <SearchInput label="Search by partner name or email" value={filters.q} onSearch={(q) => apply({ ...filters, q: q.slice(0, 80) })} />
        {select("type", "Partner type", TYPE_OPTIONS)}
        {select("status", "Status", STATUS_OPTIONS)}
        {select("profile", "Profile", PROFILE_OPTIONS)}
        {select("kyc", "KYC", KYC_OPTIONS)}
      </FilterBar>
      {feedback}
      <DataTable
        caption="Partners"
        columns={columns}
        rows={paged.rows}
        rowKey={(r) => r.id}
        rowHref={(r) => `/admin/partners/${r.id}`}
        rowLabel={(r) => r.display_name}
        rowMenu={rowMenu}
        loading={paged.state === "loading"}
        error={paged.state === "forbidden" ? "You don't have access to partners." : paged.state === "error" ? "Couldn't load partners." : null}
        onLoadMore={paged.more}
        hasMore={paged.hasMore}
        empty={
          <EmptyState
            icon="search"
            title={filtered ? "No partners match these filters." : category ? `No ${CATEGORY_NOUN[category]} yet.` : "No partners yet."}
            hint={filtered ? "Try another search or clear the filters." : "Partners you create appear here."}
            action={
              manage && !filtered
                ? { label: "New partner", href: category ? `/admin/partners/new?type=${category}` : "/admin/partners/new" }
                : undefined
            }
          />
        }
      />
      {confirm.dialog}
      {guestAccess && (
        <ManageGuestAccess
          partnerId={guestAccess.id}
          partnerName={guestAccess.display_name}
          partnerStatus={guestAccess.status}
          canGuest={canGuest}
          onClose={() => setGuestAccess(null)}
        />
      )}
    </Page>
  );
}
