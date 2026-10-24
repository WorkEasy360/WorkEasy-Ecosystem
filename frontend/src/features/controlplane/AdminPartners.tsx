"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi, type PartnerSummaryView } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperiencePanel } from "./ExperiencePanel";
import { TypeBadges } from "./labels";
import { CompletionMeter, SetupStatusBadge } from "./PartnerConsole";
import { BusinessDetailsTab, ContactsTab } from "./PartnerProfileTabs";
import {
  ActivityTab,
  BrandingTab,
  CommissionTab,
  CustomersTab,
  DomainsTab,
  OverviewTab,
  PayoutsTab,
  PricingTab,
  ProductsTab,
  ReferralsTab,
  SubscriptionsTab,
  UsersTab,
} from "./ResellerTabs";
import { TAB_LABELS, TAB_ORDER, tabFromParam, type TabKey, type TabProps } from "./partnerTabs";
import { LoadGate, ReasonAction, Status, useAction, useLoad, type Load } from "./ui";
import { UpgradeNotice, UpgradePanel } from "./UpgradePanel";
import { Tabs, type TabItem } from "./widgets";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";
import ps from "./PartnerProfileTabs.module.css";

/**
 * One partner (ADR-048): the header - name, type badges, status and how complete
 * its profile is, with **Complete Your Profile** - then the lifecycle actions and
 * the partner's sections.
 *
 * Overview, Profile, Products, Users & Permissions and Activity are every
 * partner's; Pricing, Commission, Customers, Branding, Domains, Referrals and
 * Payouts appear for the partners whose capabilities they belong to. Which ones
 * is read from the labels the *server* derived from the partner's grants -
 * hiding a tab is a convenience; the server re-checks every read and write.
 * There is no way to delete a partner, because its history is kept.
 */

/** The tabs this staff member can read that belong to what this partner can do. */
export function visibleTabs(permissions: string[], labels: string[], held: string[] = []): TabItem<TabKey>[] {
  const sells = labels.includes("reseller") || labels.includes("white_label");
  const earns = labels.includes("reseller") || labels.includes("affiliate") || held.includes("commission.earn");
  return TAB_ORDER.filter((key) => {
    if (key === "pricing" || key === "customers") return sells;
    if (key === "commission") return earns;
    if (key === "payouts") return earns && can(permissions, "platform.staff_commission.view");
    if (key === "activity") return can(permissions, "platform.staff_audit.view");
    if (key === "branding" || key === "domains") return labels.includes("white_label");
    if (key === "referrals") return labels.includes("affiliate");
    return true;
  }).map((key) => ({ key, label: TAB_LABELS[key] }));
}

export function PartnerDetailScreen({ partnerId }: { partnerId: string }) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const [tab, setTab] = useState<TabKey>(tabFromParam(params.get("tab")) ?? "overview");
  const [justCreated] = useState(params.get("created") === "1");
  const [rev, setRev] = useState(0);
  const [load, reload] = useLoad(() => adminApi.partner(partnerId), [partnerId]);
  // One overview read for the whole page: the header, the Activate guidance, the
  // Overview and the product options all show it, and a change refreshes it in
  // place (the current answer stays on screen until the new one arrives).
  const [summary, reloadSummary] = useLoad(() => partnerConsoleApi.summary(partnerId), [partnerId]);
  // The upgrade standing (ADR-049): read once here so the line above the tabs and
  // the panel on Users & Permissions show the same answer.
  const [upgrades, reloadUpgrades] = useLoad(() => partnerConsoleApi.upgrades(partnerId), [partnerId]);
  const bump = () => {
    reload();
    reloadSummary();
    reloadUpgrades();
    setRev((n) => n + 1);
  };
  const { act, busy, feedback } = useAction(bump);
  const manage = can(permissions, "platform.staff_partner.manage");

  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        <Link href="/admin/partners/all">All Partners</Link>
      </p>
      {justCreated && (
        <p role="status" className={local.success}>
          Partner created successfully.
        </p>
      )}
      <LoadGate load={load} what="this partner">
        {(p) => {
          const labels = summary.status === "ready" ? summary.data.experience.map((e) => e.key) : (p.experience ?? []).map((e) => e.key);
          const held = summary.status === "ready" ? summary.data.capabilities.map((c) => c.key) : [];
          const items = visibleTabs(permissions, labels, held);
          const selected = items.some((i) => i.key === tab) ? tab : "overview";
          const available = items.map((i) => i.key);
          const tabProps: TabProps = { partnerId: p.id, partner: p, summary, rev, bump };
          return (
            <>
              <div className={local.titleRow}>
                <h1>{p.display_name}</h1>
                {summary.status === "ready" ? <SetupStatusBadge value={summary.data.profile_completion.setup_status} /> : <Status value={p.status} />}
              </div>
              <TypeBadges labels={summary.status === "ready" ? summary.data.experience : (p.experience ?? [])} />
              <p className={styles.muted}>
                {p.code} · preferred currency {p.payout_currency} · {p.live_customers} live customers
                {p.status_reason && ` · ${p.status_reason}`}
              </p>
              <CompletionBanner partnerId={p.id} summary={summary} manage={manage} />
              <UpgradeNotice load={upgrades} onOpen={() => setTab("users")} />
              {feedback}
              {manage && (
                <div className={styles.row}>
                  {p.status === "draft" && (
                    <ActivateAction
                      summary={summary}
                      busy={busy}
                      onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner activated.")}
                    />
                  )}
                  {p.status === "active" && (
                    <ReasonAction label="Suspend" danger busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "suspended", r, p.version), "Partner suspended.")} />
                  )}
                  {p.status === "suspended" && (
                    <ReasonAction label="Reactivate" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner reactivated.")} />
                  )}
                  {p.status !== "terminated" && (
                    <ReasonAction
                      label="Terminate"
                      danger
                      busy={busy}
                      onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "terminated", r, p.version), "Partner terminated. Its customers were not touched.")}
                    />
                  )}
                </div>
              )}
              <Tabs label="Partner sections" items={items} selected={selected} onSelect={setTab}>
                {selected === "overview" && <OverviewTab {...tabProps} onOpen={(next) => setTab(next)} available={available} />}
                {selected === "profile" && (
                  <div className={styles.stack}>
                    <BusinessDetailsTab {...tabProps} />
                    <ContactsTab {...tabProps} onOpen={(next) => setTab(next)} />
                  </div>
                )}
                {selected === "products" && <ProductsTab {...tabProps} />}
                {selected === "pricing" && <PricingTab {...tabProps} />}
                {selected === "commission" && <CommissionTab {...tabProps} />}
                {selected === "customers" && (
                  <div className={styles.stack}>
                    <CustomersTab {...tabProps} />
                    <h2 className={styles.cardTitle}>Subscriptions</h2>
                    <SubscriptionsTab {...tabProps} />
                  </div>
                )}
                {selected === "branding" && <BrandingTab {...tabProps} />}
                {selected === "domains" && <DomainsTab {...tabProps} />}
                {selected === "referrals" && <ReferralsTab {...tabProps} />}
                {selected === "payouts" && <PayoutsTab {...tabProps} />}
                {selected === "users" && (
                  <div className={styles.stack}>
                    <UpgradePanel partnerId={p.id} load={upgrades} manage={manage} onChanged={bump} onOpenTab={setTab} available={available} />
                    <ExperiencePanel partner={p} manage={manage} onChanged={bump} />
                    <UsersTab {...tabProps} />
                  </div>
                )}
                {selected === "activity" && <ActivityTab {...tabProps} />}
              </Tabs>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

/** How complete the profile is, and the way to finish it. An incomplete profile
 * is said in capitals (by CSS) with what is missing and **Complete Your
 * Profile**; a complete one says so and keeps a quieter link to edit it. */
function CompletionBanner({ partnerId, summary, manage }: { partnerId: string; summary: Load<PartnerSummaryView>; manage: boolean }) {
  if (summary.status !== "ready") return null;
  const c = summary.data.profile_completion;
  const href = `/admin/partners/${partnerId}/profile`;
  if (c.complete) {
    return (
      <section className={`w360-card ${local.completion} ${local.completionDone}`} aria-label="Profile completion">
        <div className={local.completionHead}>
          <p className={local.completionTitle}>Profile complete</p>
          <CompletionMeter value={c} />
        </div>
        {c.setup_status === "ready" && <p>Everything required is in place. Activate the partner when you are ready; its administrator can then sign in to the partner portal.</p>}
        {manage && (
          <p>
            <Link className="w360-btn w360-btn-secondary" href={href}>
              Edit profile
            </Link>
          </p>
        )}
      </section>
    );
  }
  return (
    <section className={`w360-card ${local.completion}`} aria-label="Profile completion">
      <div className={local.completionHead}>
        <p className={local.completionTitle}>Profile incomplete</p>
        <CompletionMeter value={c} />
      </div>
      <p>
        Still needed: {c.missing.map((m) => m.label).join(", ")}.
        {summary.data.partner.status === "draft" && " The partner stays a draft - it can do nothing - until its profile is complete and it is activated."}
      </p>
      {manage && (
        <p>
          <Link className="w360-btn w360-btn-primary" href={href}>
            Complete Your Profile
          </Link>
        </p>
      )}
    </section>
  );
}

/**
 * Activate, for a draft partner. While the server's completion answer lists what
 * is missing the button is disabled and the reasons are written next to it - a
 * disabled button cannot be focused, so the words are on the page, not only on
 * the button. If the answer cannot be read, the action is offered and the server
 * decides.
 */
export function ActivateAction({ summary, busy, onConfirm }: { summary: Load<PartnerSummaryView>; busy: boolean; onConfirm: (reason: string) => void }) {
  const why = useId();
  if (summary.status === "loading") {
    return (
      <div className={ps.activateReason}>
        <button type="button" className="w360-btn w360-btn-secondary" disabled aria-describedby={why}>
          Activate
        </button>
        <p id={why} className={styles.muted}>
          Checking whether this partner is ready to activate…
        </p>
      </div>
    );
  }
  if (summary.status !== "ready") {
    // Without the completion answer there is nothing to say the profile is
    // complete, so activation is not offered until the page is reloaded.
    return (
      <div className={ps.activateReason}>
        <button type="button" className="w360-btn w360-btn-secondary" disabled aria-describedby={why}>
          Activate
        </button>
        <p id={why} className={styles.muted}>
          Whether this partner is ready could not be checked. Reload the page to try again.
        </p>
      </div>
    );
  }
  const missing = summary.data.activation.missing;
  if (missing.length === 0) return <ReasonAction label="Activate" busy={busy} onConfirm={onConfirm} />;
  return (
    <div className={ps.activateReason}>
      <button type="button" className="w360-btn w360-btn-secondary" disabled aria-describedby={why}>
        Activate
      </button>
      <p id={why} className={styles.muted}>
        The profile is incomplete, so this partner cannot be activated yet. Still needed: {missing.map((m) => m.label).join(", ")}.
      </p>
    </div>
  );
}
