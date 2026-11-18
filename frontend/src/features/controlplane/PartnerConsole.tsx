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
import { GUEST_PERMISSION, GUEST_RETURNED, ManageGuestAccess, guestAvailable, guestOutcomeMessage, useGuestLogin } from "./GuestAccess";
import { ExperienceLabels, PrimaryType, TypeBadges } from "./labels";
import { LoadGate, Status, autoReason, money, pagedTable, useAction, useLoad, usePaged } from "./ui";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { CATEGORY_MENU_LABEL, categoryHref, isCategory, lastPartnerList, rememberPartnerList, type PartnerCategory } from "./partnerCategories";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  ButtonLink,
  DataTable,
  DateRangeFilter,
  EmptyState,
  FilterBar,
  IdCell,
  Page,
  PageHeader,
  SearchInput,
  SectionHeader,
  Select,
  StatCard,
  StatusChips,
  Tooltip,
  Trunc,
  formatDate,
  isDay,
  kit,
  rangeLabel,
  useConfirm,
  useToast,
  type ActiveFilter,
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
  // ADR-062: each type card opens the list with its type chip chosen, which is
  // exactly what the card counts.
  resellers: categoryHref("reseller"),
  white_label: categoryHref("white_label"),
  affiliates: categoryHref("affiliate"),
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
        {/* A long name is cut with an ellipsis (whole in its tooltip), so the row's
            other columns and actions stay in view (admin-ux phase 3). */}
        <Link href={`/admin/partners/${id}`} className={kit.cellLink}>
          <Trunc>{name}</Trunc>
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

/** The type chips (admin-ux phase 1): All, then each category - one pressed. */
const TYPE_CHIPS: { value: "" | PartnerCategory; label: string }[] = [
  { value: "", label: "All" },
  { value: "affiliate", label: "Affiliate" },
  { value: "reseller", label: "Reseller" },
  { value: "white_label", label: "White-label" },
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

/** The created-date range from an address: real days only, never one that ends
 * before it starts (admin-ux phase 3). */
function createdRange(params: URLSearchParams): { from: string; to: string } {
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const range = { from: isDay(from) ? from : "", to: isDay(to) ? to : "" };
  return range.from && range.to && range.from > range.to ? { from: "", to: "" } : range;
}

const optionLabel = (options: { value: string; label: string }[], value: string) => options.find((o) => o.value === value)?.label ?? value;

/** The reason recorded when a partner's status is changed from its row. Nobody
 * has to type one (the owner's call, 2026-10-27); the server still requires a
 * reason, so the action says where it came from unless a reason was written. */
const SWITCH_REASON = {
  activate: "Activated from All Partners",
  reactivate: "Reactivated from All Partners",
  suspend: "Suspended from All Partners",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(undefined, { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** The KYC states as the specification names them (ADR-063). */
function kycTitle(status: string): string {
  return (
    {
      draft: "Draft",
      submitted: "Submitted",
      under_review: "Under Review",
      correction_required: "Correction Required",
      approved: "Approved",
      rejected: "Rejected",
    }[status] ?? status
  );
}

const KYC_TONE: Record<string, string> = {
  draft: "tone-neutral",
  submitted: "tone-warning",
  under_review: "tone-info",
  correction_required: "tone-warning",
  approved: "tone-success",
  rejected: "tone-danger",
};

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
  affiliate: "Every partner whose type is Affiliate, with its status.",
  reseller: "Every partner whose type is Reseller, with its status.",
  white_label: "Every partner whose type is White-label, with its status.",
};

const CATEGORY_NOUN: Record<PartnerCategory, string> = {
  affiliate: "affiliate partners",
  reseller: "reseller partners",
  white_label: "white-label partners",
};

/** Partners › Partners: one table, filtered by the server (search, partner type,
 * status, profile, KYC). The filters are kept in the address so a filtered view
 * can be linked to and survives a reload. A partner's actions are in its row's
 * menu; a status change asks to confirm.
 *
 * The partner type is a row of chips - All, Affiliate, Reseller, White-label
 * (`?type=`, 2026-11-18, admin-ux phase 1): the server lists only the partners of
 * that type (ADR-062), each partner under exactly one, so a chip always means the
 * type the Type column shows. The old type pages open this list with their chip
 * chosen. On New partner (the list under its dialog) `?type=` is the form's. */
export function AllPartnersList() {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/admin/partners/all";
  // The list owns the query everywhere but on New partner, where `?type=` is the form's.
  const onList = pathname !== "/admin/partners/new";
  const typeOf = (value: string | null): "" | PartnerCategory => (onList && isCategory(value) ? value : "");
  // Back from a guest session (`?guest=ended`): this list's own type, search and
  // filters, as this tab last showed them - the server keeps only the path. A list
  // this tab never showed keeps the type its address names.
  const returned = params.get("guest") === GUEST_RETURNED;
  const [restored] = useState(() => {
    if (!returned) return null;
    const href = lastPartnerList(pathname) ?? categoryHref(typeOf(params.get("type")) || null);
    return new URLSearchParams(href.split("?")[1] ?? "");
  });
  const source = restored ?? params;
  const [filters, setFilters] = useState({
    q: (source.get("q") ?? "").slice(0, 80),
    type: typeOf(source.get("type")),
    status: pick(source.get("status"), STATUS_OPTIONS),
    profile: pick(source.get("profile"), PROFILE_OPTIONS),
    kyc: pick(source.get("kyc"), KYC_OPTIONS),
    ...createdRange(source),
  });
  useEffect(() => {
    if (!restored) return;
    const text = restored.toString();
    router.replace(text ? `/admin/partners/all?${text}` : "/admin/partners/all", { scroll: false });
    // Once, on arrival: the address then carries the filters itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // What "Back to Partners" on a partner's page opens.
  const search = params.toString();
  useEffect(() => {
    if (!returned) rememberPartnerList(pathname, search ? `?${search}` : "");
  }, [pathname, search, returned]);
  const manage = can(permissions, "platform.staff_partner.manage");
  const canGuest = can(permissions, GUEST_PERMISSION);
  const canKyc = can(permissions, "platform.staff_kyc.view");
  const [guestAccess, setGuestAccess] = useState<ResellerRow | null>(null);
  // The row whose Login as Guest is being opened (its button shows a spinner).
  // Login as Guest came back with an outcome (a used or expired link, or refused).
  const [guestOutcome] = useState(() => guestOutcomeMessage(params.get("guest")));
  const portals = usePortals();

  // A type chosen from elsewhere (the command palette, a card on the overview)
  // while the list is open: the list follows the address.
  const addressType = typeOf(params.get("type"));
  const [seenType, setSeenType] = useState(addressType);
  if (addressType !== seenType) {
    setSeenType(addressType);
    if (!restored || addressType) {
      setFilters({
        q: (params.get("q") ?? "").slice(0, 80),
        type: addressType,
        status: pick(params.get("status"), STATUS_OPTIONS),
        profile: pick(params.get("profile"), PROFILE_OPTIONS),
        kyc: pick(params.get("kyc"), KYC_OPTIONS),
        ...createdRange(params),
      });
    }
  }
  const category = filters.type || undefined;
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
        from: filters.from || undefined,
        to: filters.to || undefined,
        cursor,
      }),
    [filters.q, category, filters.status, filters.profile, filters.kyc, filters.from, filters.to],
  );
  const { act, busy, feedback } = useAction(() => paged.reload());

  function apply(next: typeof filters) {
    setFilters(next);
    if (!onList) return;
    const { type, ...rest } = next;
    const href = categoryHref(isCategory(type) ? type : null, rest);
    setSeenType(isCategory(type) ? type : "");
    // A new type is a new list, kept in the history like a tab; a filter is not.
    if (type !== filters.type) router.push(href, { scroll: false });
    else router.replace(href, { scroll: false });
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
  // exactly this partner and the browser goes to its dashboard. A refusal is
  // said above the table (the server's own words), and the button is usable again.
  // ADR-063: Login as Guest asks why first; the dialog opens the session.
  const guestLogin = useGuestLogin(category ? CATEGORY_TITLE[category] : "All Partners");
  const toast = useToast();
  const guest = (r: ResellerRow) =>
    guestLogin.ask(r.id, r.display_name, {
      type: isCategory(r.category) ? CATEGORY_MENU_LABEL[r.category] : undefined,
      status: r.status,
    });

  const rowMenu = (r: ResellerRow): MenuItem[] => {
    const url = panelUrl(r);
    // View and Login as Guest are buttons in the row (the owner's decision, kept
    // in admin-ux phase 3); the menu holds the rest.
    return [
      { label: r.profile_completion.complete ? "Edit partner" : "Complete setup", icon: "settings", href: `/admin/partners/${r.id}/profile` },
      // ADR-063: the specification's row actions.
      { label: "Review KYC", icon: "shield", href: `/admin/partners/applications/${r.id}`, when: canKyc },
      {
        label: "Approve / Reject KYC",
        icon: "check",
        href: `/admin/partners/applications/${r.id}#decision`,
        when: canKyc && (r.kyc?.status === "submitted" || r.kyc?.status === "under_review"),
      },
      { label: "Manage Permissions", icon: "key", href: `/admin/partners/${r.id}?tab=users` },
      { label: "View Assigned Customers", icon: "users", href: `/admin/partners/${r.id}?tab=customers` },
      {
        label: "View Partner Wallet",
        icon: "card",
        href: `/admin/wallets?owner_type=partner&q=${encodeURIComponent(r.display_name)}`,
      },
      { label: "View Commission & Payout Records", icon: "currency", href: `/admin/partners/${r.id}?tab=commission` },
      { label: "Open partner panel", icon: "external", href: url, external: true },
      { label: "Copy panel link", icon: "copy", onSelect: () => void navigator.clipboard?.writeText(url) },
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

  // Partner Name, Type, Account Status, then the other ADR-063 columns - Partner
  // Email, KYC Status, Country, Assigned Customers and Created Date - and the
  // Actions column last (admin-ux phase 3): View and Login as Guest side by side,
  // then the row's menu, held at the right edge so they are always in view. No
  // Earnings: they stay on each partner's own page. A narrow table folds the
  // later columns into each row's details (`priority`).
  const columns: Column<ResellerRow>[] = [
    {
      key: "partner",
      header: "Partner Name",
      value: (r) => r.display_name,
      render: (r) => <PartnerCell id={r.id} name={r.display_name} sub={<IdCell value={r.id} label={`Copy partner ID of ${r.display_name}`} />} />,
    },
    {
      key: "type",
      header: "Type / Permissions",
      priority: "medium",
      value: (r) => r.primary_experience,
      render: (r) => (
        <span className={local.badgeWrap}>
          <PrimaryType value={r.primary_experience} />
          {r.experience.length > 1 && <span className={styles.muted}>+ {r.experience.filter((e) => e.key !== r.primary_experience).map((e) => e.label).join(", ")}</span>}
        </span>
      ),
    },
    {
      key: "status",
      header: "Account Status",
      // In the row's details on a phone, where the name and the actions take the width.
      priority: "high",
      value: (r) => r.status,
      render: (r) => (
        <span className={local.badgeWrap}>
          <Status value={r.status} />
          {r.status === "draft" && <SetupStatusBadge value={r.profile_completion.setup_status} />}
          {r.kyc?.ready_to_activate && <span className={`${kit.badge} ${kit["tone-info"]}`}>Ready to activate</span>}
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
      key: "email",
      header: "Partner Email",
      priority: "low",
      value: (r) => r.administrator?.email ?? "",
      render: (r) =>
        r.administrator?.email ? (
          <span className={kit.trunc} title={r.administrator.email}>
            {r.administrator.email}
          </span>
        ) : (
          <span className={styles.muted}>No administrator</span>
        ),
    },
    {
      key: "kyc",
      header: "KYC Status",
      priority: "high",
      // Never blank, so the column always shows (the table hides an all-empty one).
      value: (r) => r.kyc?.status ?? "unknown",
      render: (r) => (
        <span className={local.badgeLine}>
          {!r.kyc && <span className={styles.muted}>—</span>}
          {r.kyc && (
            <Link
              href={`/admin/partners/applications/${r.id}`}
              className={`${kit.badge} ${kit[KYC_TONE[r.kyc.status] ?? "tone-neutral"]}`}
              aria-label={`KYC ${kycWords(r.kyc.status)} - ${r.display_name}`}
            >
              {kycTitle(r.kyc.status)}
            </Link>
          )}
          {r.kyc?.legacy && (
            <Tooltip content="Active before KYC was required. No KYC approval is on file; the partner policy in Settings decides what it may do.">
              <span tabIndex={0} className={`${kit.badge} ${kit["tone-neutral"]}`}>
                Active before KYC
              </span>
            </Tooltip>
          )}
        </span>
      ),
    },
    {
      key: "country",
      header: "Country",
      priority: "low",
      // ADR-063: always shown (the table hides a column blank in every row, and
      // "—" counts as blank); a partner with no country sorts last.
      value: (r) => r.country || "ZZ",
      render: (r) => (r.country ? <abbr title={countryName(r.country)}>{r.country}</abbr> : "—"),
    },
    {
      key: "customers",
      header: "Assigned Customers",
      priority: "low",
      value: (r) => r.customers,
      render: (r) => (
        <Link href={`/admin/partners/${r.id}?tab=customers`} aria-label={`${r.customers} assigned customers of ${r.display_name}`}>
          {r.customers}
        </Link>
      ),
    },
    { key: "joined", header: "Created", priority: "low", value: (r) => r.created_at, render: (r) => formatDate(r.created_at) },
  ];

  // The row's frequent actions (Decision A, 2026-11-18): View and Login as Guest,
  // side by side, before the menu. Login as Guest only for staff who may start a
  // guest view - the server checks again; for a draft it is shown, and pressing it
  // says why it is not available.
  const rowActions = (r: ResellerRow) => (
    <>
      <ButtonLink size="sm" variant="secondary" icon="eye" href={`/admin/partners/${r.id}`} aria-label={`View ${r.display_name}`}>
        View
      </ButtonLink>
      {canGuest &&
        (guestAvailable(r.status) ? (
          <Button
            size="sm"
            variant="primary"
            icon="login"
            aria-label={`Login as Guest to ${r.display_name}`}
            loading={guestLogin.pending === r.id}
            disabled={busy || guestLogin.dialog !== null}
            onClick={() => void guest(r)}
          >
            Login as Guest
          </Button>
        ) : (
          <Tooltip content="Opens once the partner is active">
            <Button
              size="sm"
              variant="primary"
              icon="login"
              aria-label={`Login as Guest to ${r.display_name}`}
              aria-disabled="true"
              className={local.unavailable}
              onClick={() =>
                toast.info(`Login as Guest is not available for ${r.display_name} yet: it is a draft, so it has no partner portal. It opens once the partner is active.`)
              }
            >
              Login as Guest
            </Button>
          </Tooltip>
        ))}
    </>
  );

  const filtered = Boolean(filters.q || filters.status || filters.profile || filters.kyc || filters.from || filters.to);
  const cleared = { q: "", status: "", profile: "", kyc: "", from: "", to: "" };
  // The applied filters, as tags (the type is the chip row above, and the title).
  const active: ActiveFilter[] = [
    ...(filters.q ? [{ key: "q", label: "Search", value: `“${filters.q}”`, onRemove: () => apply({ ...filters, q: "" }) }] : []),
    ...(filters.status
      ? [{ key: "status", label: "Account status", value: optionLabel(STATUS_OPTIONS, filters.status), onRemove: () => apply({ ...filters, status: "" }) }]
      : []),
    ...(filters.profile
      ? [{ key: "profile", label: "Profile", value: optionLabel(PROFILE_OPTIONS, filters.profile), onRemove: () => apply({ ...filters, profile: "" }) }]
      : []),
    ...(filters.kyc ? [{ key: "kyc", label: "KYC", value: optionLabel(KYC_OPTIONS, filters.kyc), onRemove: () => apply({ ...filters, kyc: "" }) }] : []),
    ...(filters.from || filters.to
      ? [{ key: "created", label: "Created", value: rangeLabel(filters), onRemove: () => apply({ ...filters, from: "", to: "" }) }]
      : []),
  ];
  const select = (key: "status" | "profile" | "kyc", label: string, options: { value: string; label: string }[]) => (
    <Select label={label} value={filters[key]} options={options} onChange={(value) => apply({ ...filters, [key]: value })} />
  );

  return (
    <Page>
      <PageHeader
        title={category ? CATEGORY_TITLE[category] : "All partners"}
        subtitle={category ? CATEGORY_SUBTITLE[category] : "Every partner account, with its type and status."}
        primary={<NewPartnerButton category={category} />}
      />
      {created && <CreatedBanner partnerId={created} invite={createdInvite} />}
      {guestOutcome && <Alert tone="warning" title={guestOutcome} />}
      {onList && <StatusChips label="Partner type" value={filters.type} onChange={(type) => apply({ ...filters, type })} chips={TYPE_CHIPS} />}
      <FilterBar label="Filter partners" onClear={filtered ? () => apply({ ...filters, ...cleared }) : undefined} active={active}>
        <SearchInput label="Search by partner name or email" value={filters.q} onSearch={(q) => apply({ ...filters, q: q.slice(0, 80) })} />
        {select("status", "Status", STATUS_OPTIONS)}
        {select("profile", "Profile", PROFILE_OPTIONS)}
        {select("kyc", "KYC", KYC_OPTIONS)}
        <DateRangeFilter label="Created" value={{ from: filters.from, to: filters.to }} onApply={(range) => apply({ ...filters, ...range })} />
      </FilterBar>
      {feedback}
      <DataTable
        caption="Partners"
        columns={columns}
        {...pagedTable(paged, "partners")}
        rowKey={(r) => r.id}
        rowHref={(r) => `/admin/partners/${r.id}`}
        rowLabel={(r) => r.display_name}
        actions={rowActions}
        rowMenu={rowMenu}
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
      {guestLogin.dialog}
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
