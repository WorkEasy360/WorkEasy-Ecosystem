"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import { consoleApi, type ExploreProduct } from "@/lib/api/console";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { AppCard } from "@/features/launcher/AppCard";
import { LaunchButton } from "@/features/launcher/LaunchButton";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { DialogHead, Modal, dialog } from "@/features/controlplane/Modal";
import {
  ButtonLink,
  DescriptionList,
  EmptyState,
  Page,
  SectionHeader,
  StatusBadge,
  StatusChips,
  formatDate,
  formatNumber,
} from "@/features/controlplane/kit";
import local from "./ConsoleScreens.module.css";
import { PageHeader } from "./consoleUi";
import { SETUP_STATUS_WORDS, SetupStatus, setupOpen, useSetupRecords, type SetupRecord } from "./CustomerDesk";
import { useOrgAccess } from "./OrgAccess";

/**
 * My Products (2026-11-11, the User Admin brief Part 2 section 02): every product the
 * organization holds, ran a trial of, or must still set up - with filters, Open
 * Product, View Details and the product's setup, handled here.
 *
 * Nothing here decides access or price. What a member may open is the launcher's
 * answer (re-checked at launch); the plan, seats and renewal are what the directory
 * returns to a member allowed to see them; expired trials and features are Explore's
 * answer; setup is the setup desk's. Buying - for a new product or a new plan - happens
 * in the Store, which already knows this organization.
 */

export type ProductFilter = "" | "active" | "free" | "trial" | "setup" | "expired";

const FILTERS: { value: ProductFilter; label: string }[] = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "free", label: "Free" },
  { value: "trial", label: "Trial" },
  { value: "setup", label: "Pending Setup" },
  { value: "expired", label: "Expired" },
];

const HELD = new Set<AppDirectoryEntry["state"]>(["active", "no_seat", "integration_suspended"]);

export interface ProductRow {
  app: AppDirectoryEntry;
  explore: ExploreProduct | null;
  setup: SetupRecord | null;
  held: boolean;
  trial: boolean;
  free: boolean;
  expired: boolean;
  pendingSetup: boolean;
}

/** A plan whose every price is zero (a free plan, not a trial of a paid one). */
function freePlan(explore: ExploreProduct | null, planCode: string | undefined): boolean {
  if (!explore || !planCode) return false;
  const plan = explore.plans.find((p) => p.plan_code === planCode);
  if (!plan || plan.billing_type === "free_trial") return false;
  const amounts = plan.prices.map((p) => p.amount_minor).filter((a): a is number => a !== null);
  return amounts.length > 0 && amounts.every((a) => a === 0);
}

/** The products this organization is associated with, classified for the filters. */
export function productRows(
  apps: AppDirectoryEntry[],
  explore: ExploreProduct[] | null,
  setup: SetupRecord[] | null,
): ProductRow[] {
  const byCode = new Map((explore ?? []).map((p) => [p.code, p]));
  const setupBy = new Map<string, SetupRecord>();
  for (const r of setup ?? []) {
    const known = setupBy.get(r.product_code);
    // The record still asking for something wins over an approved one.
    if (!known || (!setupOpen(known) && setupOpen(r))) setupBy.set(r.product_code, r);
  }
  const rows: ProductRow[] = [];
  for (const app of apps) {
    const e = byCode.get(app.product_code) ?? null;
    const record = setupBy.get(app.product_code) ?? null;
    const held = HELD.has(app.state);
    const expired = !held && e?.trial_state === "used" && e.trial_outcome === "expired";
    const pendingSetup = Boolean(record && setupOpen(record));
    // Associated: held, a trial that ran out, or a setup in progress.
    if (!held && !expired && !pendingSetup) continue;
    const trial = held && (app.entitlement_status === "trial" || Boolean(app.trial));
    rows.push({
      app,
      explore: e,
      setup: record,
      held,
      trial,
      free: held && !trial && freePlan(e, app.plan?.code),
      expired,
      pendingSetup,
    });
  }
  return rows;
}

export function matches(row: ProductRow, filter: ProductFilter): boolean {
  switch (filter) {
    case "active":
      return row.held && !row.trial;
    case "free":
      return row.free;
    case "trial":
      return row.trial;
    case "setup":
      return row.pendingSetup;
    case "expired":
      return row.expired;
    default:
      return true;
  }
}

const SETUP_TONE: Record<SetupRecord["status"], string> = {
  pending: "w360-badge-warning",
  correction_required: "w360-badge-danger",
  submitted: "w360-badge-neutral",
  approved: "w360-badge-success",
};

function filterFrom(raw: string | null): ProductFilter {
  return FILTERS.some((f) => f.value === raw) ? (raw as ProductFilter) : "";
}

/** A product code from the address - a lookup key only (an unknown one opens nothing). */
function productFrom(raw: string | null): string | null {
  return raw && /^[a-z0-9_-]{1,64}$/i.test(raw) ? raw : null;
}

/** My Products: what the organization holds, each with Open (when the member
 * may) and View Details; filters; the setup a product still asks for. */
export function MyProductsScreen({ banner }: { banner?: React.ReactNode }) {
  const { slug, can } = useOrgAccess();
  const params = useSearchParams();
  // Deep links (the dashboard, the notifications and the top bar's search):
  // `?filter=setup|active|free|trial|expired` picks the chip, `?product=<code>`
  // opens that product's details. Followed again when the address changes.
  const linkedFilter = params.get("filter");
  const linkedProduct = params.get("product");
  const [filter, setFilter] = useState<ProductFilter>(() => filterFrom(linkedFilter));
  const [open, setOpen] = useState<string | null>(() => productFrom(linkedProduct));
  const [seen, setSeen] = useState({ filter: linkedFilter, product: linkedProduct });
  if (seen.filter !== linkedFilter || seen.product !== linkedProduct) {
    setSeen({ filter: linkedFilter, product: linkedProduct });
    if (seen.filter !== linkedFilter) setFilter(filterFrom(linkedFilter));
    if (seen.product !== linkedProduct) setOpen(productFrom(linkedProduct));
  }
  const [directory] = useLoad(() => getAppDirectory(slug).then((r) => r.apps), [slug]);
  // Supplementary: while they load (or if they cannot), the cards show what the
  // directory says and nothing more.
  const [explore] = useLoad(() => consoleApi.explore(slug).then((r) => r.products), [slug]);
  const [setup, reloadSetup] = useSetupRecords(slug);
  const manage = can("commercial.subscription.view") ? `/o/${slug}/settings/billing` : undefined;
  const seatsHref = can("platform.seat.assign") ? `/o/${slug}/settings/products` : undefined;
  const store = `/o/${slug}/store`;

  return (
    <Page>
      {banner}
      <PageHeader
        title="My Products"
        lead="Every product your organization uses: its plan, status, renewal and setup."
        primary={
          <ButtonLink variant="primary" icon="store" href={store}>
            Explore Products
          </ButtonLink>
        }
      />
      <LoadGate load={directory} what="your products">
        {(apps) => {
          const rows = productRows(
            apps,
            explore.status === "ready" ? explore.data : null,
            setup.status === "ready" ? setup.data : null,
          );
          const shown = rows.filter((r) => matches(r, filter));
          const selected = rows.find((r) => r.app.product_code === open) ?? null;
          if (rows.length === 0) {
            return (
              <EmptyState
                icon="box"
                title="Your organization doesn't have any products yet."
                hint="Explore the Store to buy one or start a free trial."
                action={{ label: "Explore Products", href: store, icon: "store" }}
              />
            );
          }
          return (
            <>
              <StatusChips
                label="Show products"
                value={filter}
                onChange={setFilter}
                chips={FILTERS.map((f) => ({ value: f.value, label: f.label, count: rows.filter((r) => matches(r, f.value)).length }))}
              />
              {filter === "setup" && shown.length > 0 && (
                <p className={local.muted}>
                  These products need details from your organization before they can be set up. Send them from each card&apos;s
                  details.
                </p>
              )}
              {shown.length === 0 ? (
                <EmptyState icon="filter" title="No products match this filter." hint="Choose All to see every product." compact />
              ) : (
                <div className={local.productGrid}>
                  {shown.map((row) => (
                    <AppCard
                      key={row.app.product_code}
                      orgSlug={slug}
                      app={row.app}
                      manageHref={row.held ? manage : undefined}
                      seatsHref={seatsHref}
                      expired={row.expired}
                      exploreHref={`${store}/${encodeURIComponent(row.app.product_code)}`}
                      setup={
                        row.setup && setupOpen(row.setup)
                          ? { label: `Setup: ${SETUP_STATUS_WORDS[row.setup.status]}`, tone: SETUP_TONE[row.setup.status] }
                          : null
                      }
                      onDetails={() => setOpen(row.app.product_code)}
                    />
                  ))}
                </div>
              )}
              {selected && (
                <ProductDetails
                  slug={slug}
                  row={selected}
                  onClose={() => setOpen(null)}
                  onSetupDone={reloadSetup}
                  canBuy={can("commercial.subscription.manage")}
                  canSeePlans={can("commercial.subscription.view")}
                  seatsHref={seatsHref}
                />
              )}
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}

const ACTIVATION_WORDS: Record<string, string> = {
  trial: "Free trial",
  active: "Active",
  past_due: "Active - payment overdue",
  grace: "Active - payment overdue",
  suspended: "Suspended (read-only)",
  cancelled: "Active - ends at the end of the period",
};

const ACCESS_WORDS: Record<AppDirectoryEntry["state"], string> = {
  active: "You can open it",
  no_seat: "You don't hold a seat yet",
  no_entitlement: "Not activated",
  integration_suspended: "Temporarily unavailable",
  product_unavailable: "Coming soon",
};

/** View Details: the plan, its features, seats, validity, activation and setup - and
 * Renew / Change Plan only where the platform supports them. */
function ProductDetails({
  slug,
  row,
  onClose,
  onSetupDone,
  canBuy,
  canSeePlans,
  seatsHref,
}: {
  slug: string;
  row: ProductRow;
  onClose: () => void;
  onSetupDone: () => void;
  canBuy: boolean;
  canSeePlans: boolean;
  seatsHref?: string;
}) {
  const titleId = useId();
  const { app, explore } = row;
  const plan = explore?.plans.find((p) => p.plan_code === app.plan?.code) ?? null;
  const features = explore?.features ?? [];
  const storeHref = `/o/${slug}/store/${encodeURIComponent(app.product_code)}`;
  const overdue = app.entitlement_status === "past_due" || app.entitlement_status === "grace";
  const validity = app.trial
    ? `Trial ends ${formatDate(app.trial.ends_at)} (${app.trial.days_left === 1 ? "1 day left" : `${app.trial.days_left} days left`})`
    : app.cancel_at
      ? `Ends ${formatDate(app.cancel_at)} - won't renew`
      : app.renews_at
        ? `Renews ${formatDate(app.renews_at)}`
        : row.expired && explore?.trial_ends_at
          ? `Trial ended ${formatDate(explore.trial_ends_at)}`
          : null;

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="lg">
      <DialogHead titleId={titleId} title={app.display_name} onClose={onClose} />
      <div className={dialog.body}>
        <div className={local.detailGrid}>
          {app.tagline && <p className={local.muted}>{app.tagline}</p>}
          <DescriptionList
            columns={2}
            items={[
              {
                label: "Activation status",
                value: row.expired ? (
                  <StatusBadge value="expired" label="Trial expired" />
                ) : app.entitlement_status ? (
                  <StatusBadge value={app.entitlement_status} label={ACTIVATION_WORDS[app.entitlement_status] ?? undefined} />
                ) : null,
              },
              { label: "Your access", value: ACCESS_WORDS[app.state] },
              { label: "Plan", value: app.plan?.name ?? (canSeePlans ? null : "Visible to billing managers") },
              { label: "Validity", value: validity },
              {
                label: "Seats",
                value: app.seats ? `${formatNumber(app.seats.used)} of ${formatNumber(app.seats.total)} assigned` : null,
                when: app.licensing_mode === "per_seat",
              },
              { label: "Category", value: app.category },
            ]}
          />
          {plan?.description && (
            <section aria-labelledby={`${titleId}-plan`}>
              <SectionHeader id={`${titleId}-plan`} level={3} title={`About the ${plan.name} plan`} />
              <p className={local.muted}>{plan.description}</p>
            </section>
          )}
          {features.length > 0 && (
            <section aria-labelledby={`${titleId}-features`}>
              <SectionHeader id={`${titleId}-features`} level={3} title="Included features" />
              <ul className={local.featureList}>
                {features.map((f) => (
                  <li key={f.name}>
                    <strong>{f.name}</strong>
                    {f.description && <span className={local.muted}> - {f.description}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section aria-labelledby={`${titleId}-setup`}>
            <SectionHeader id={`${titleId}-setup`} level={3} title="Setup" />
            {row.setup ? (
              <SetupStatus slug={slug} record={row.setup} onDone={onSetupDone} productName={app.display_name} />
            ) : (
              <p className={local.muted}>Nothing to set up: this product needs no details from your organization.</p>
            )}
          </section>
        </div>
      </div>
      <div className={dialog.foot}>
        {seatsHref && app.licensing_mode === "per_seat" && row.held && (
          <Link className="w360-btn w360-btn-secondary" href={seatsHref}>
            Manage seats
          </Link>
        )}
        {overdue && canBuy && (
          <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/orders`}>
            Renew - pay the open invoice
          </Link>
        )}
        {canBuy && (row.held || row.expired) && (
          <Link className="w360-btn w360-btn-secondary" href={storeHref}>
            {row.expired ? "Upgrade / Buy now" : "Change plan"}
          </Link>
        )}
        {app.state === "active" && <LaunchButton orgSlug={slug} productCode={app.product_code} productName={app.display_name} />}
      </div>
    </Modal>
  );
}
