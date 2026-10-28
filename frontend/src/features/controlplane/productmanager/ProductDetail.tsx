"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { adminApi, productMediaUrl, type ConsoleProduct, type ManagedProduct, type ProductActivityRow, type ReadinessItem } from "@/lib/api/controlPlane";
import { can, useAdmin } from "../ControlShell";
import { LoadGate, MoreButton, ReasonAction, describeError, useAction, useLoad, usePaged, when } from "../ui";
import { Tabs } from "../widgets";
import { AvailabilityTab } from "./AvailabilityTab";
import { IntegrationTab } from "./IntegrationTab";
import { ListingTab } from "./ListingTab";
import { STATUS_BADGE, STATUS_LABEL, VISIBILITY_BADGE, VISIBILITY_LABEL, dateOnly, trialLabel, visibilityOf } from "./model";
import { listingOf } from "./ProductManager";
import { ProductMark } from "./ProductMark";
import { StorePreview } from "./StorePreview";
import { UpdatesTab } from "./UpdatesTab";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Product Manager → one product (ADR-051). Plans and prices are
 * not managed here: the Plans and Pricing tabs open the shared Plans & add-ons
 * and Currency & pricing screens, filtered to this product.
 */

export const TABS = ["overview", "listing", "integration", "availability", "updates", "plans", "pricing", "activity"] as const;
type TabKey = (typeof TABS)[number];
const TAB_LABEL: Record<TabKey, string> = {
  overview: "Overview",
  listing: "Store Listing",
  integration: "Integration",
  availability: "Availability",
  updates: "Updates",
  plans: "Plans",
  pricing: "Pricing",
  activity: "Activity",
};

const SAVED: Record<string, string> = {
  draft: "Saved as a draft. It is hidden from the store until you publish it.",
  published: "Created and published to the store.",
};

function Checklist({ items }: { items: ReadinessItem[] }) {
  return (
    <ul className={styles.checklist}>
      {items.map((i) => (
        <li key={i.key}>
          <span aria-hidden="true" className={i.ok ? styles.ok : styles.missing}>
            {i.ok ? "✓" : "!"}
          </span>
          <span>
            {i.label}
            <span className="w360-visually-hidden">{i.ok ? ": done" : ": missing"}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Overview({ product: p, readiness }: { product: ConsoleProduct; readiness: ReadinessItem[] }) {
  const visibility = visibilityOf(p.listing);
  const missing = readiness.filter((i) => !i.ok).length;
  const facts: [string, React.ReactNode][] = [
    ["Product name", p.name],
    ["Slug", <span key="s" className={styles.code}>{p.slug}</span>],
    ["Product code", <span key="c" className={styles.code}>{p.code}</span>],
    ["Category", p.category || "—"],
    ["Short description", p.tagline || "—"],
    ["Status", <span key="st" className={STATUS_BADGE[p.manager_status]}>{STATUS_LABEL[p.manager_status]}</span>],
    ["Store visibility", <span key="v" className={VISIBILITY_BADGE[visibility]}>{VISIBILITY_LABEL[visibility]}</span>],
    ["Trial", trialLabel(p.channels.trial)],
    ["Application URL", p.application_url ? <span key="a" className={styles.url}>{p.application_url}</span> : "—"],
    ["Launch URL", p.launch_url ? <span key="l" className={styles.url}>{p.launch_url}</span> : "—"],
    ["Created", dateOnly(p.created_at)],
    ["Last updated", when(p.updated_at)],
  ];
  return (
    <div className={styles.twoCol}>
      <section className={styles.card} aria-labelledby="overview-title">
        <h2 id="overview-title" className={styles.cardTitle}>
          Overview
        </h2>
        <dl className={styles.facts}>
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className={styles.card} aria-labelledby="ready-title">
        <h2 id="ready-title" className={styles.cardTitle}>
          Ready to publish?
        </h2>
        <p className={styles.cardLead}>{missing === 0 ? "Everything the store needs is in place." : `${missing} item${missing === 1 ? "" : "s"} missing.`}</p>
        <Checklist items={readiness} />
        {!p.channels.direct.ready && <p className={styles.note}>Plans/Pricing required before final Store sale activation.</p>}
      </section>
    </div>
  );
}

function PlansPanel({ product: p }: { product: ConsoleProduct }) {
  return (
    <section className={styles.card} aria-labelledby="plans-title">
      <h2 id="plans-title" className={styles.cardTitle}>
        Plans
      </h2>
      <p className={styles.cardLead}>Plans are built in Plans &amp; add-ons, shared by every product.</p>
      {p.plans.length === 0 ? (
        <p className={styles.muted}>No plans yet.</p>
      ) : (
        <ul className={styles.checklist}>
          {p.plans.map((plan) => (
            <li key={plan.code}>
              <strong>{plan.name}</strong>
              <span className={styles.muted}>
                {plan.kind === "add_on" ? "add-on" : "plan"} · {plan.status} · {plan.is_public ? "public" : "private"}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div>
        <Link className="w360-btn w360-btn-primary" href={`/admin/plans?product=${encodeURIComponent(p.code)}`}>
          Manage Plans →
        </Link>
      </div>
    </section>
  );
}

function PricingPanel({ product: p }: { product: ConsoleProduct }) {
  const priced = p.plans.filter((plan) => plan.priced);
  return (
    <section className={styles.card} aria-labelledby="pricing-title">
      <h2 id="pricing-title" className={styles.cardTitle}>
        Pricing
      </h2>
      <p className={styles.cardLead}>Prices live in Currency &amp; pricing, one price book per currency.</p>
      <p>
        {priced.length} of {p.plans.length} plan{p.plans.length === 1 ? "" : "s"} priced.
      </p>
      <div>
        <Link className="w360-btn w360-btn-primary" href={`/admin/pricing?product=${encodeURIComponent(p.code)}`}>
          Manage Pricing →
        </Link>
      </div>
    </section>
  );
}

function ActivityPanel({ productId }: { productId: string }) {
  const { rows, hasMore, more, state } = usePaged<ProductActivityRow>((cursor) => adminApi.productActivity(productId, cursor), [productId]);
  return (
    <section className={styles.card} aria-labelledby="activity-title">
      <h2 id="activity-title" className={styles.cardTitle}>
        Activity
      </h2>
      {state === "loading" && <p aria-live="polite">Loading activity…</p>}
      {state === "forbidden" && <p role="alert">Activity needs the audit view permission.</p>}
      {state === "error" && <p role="alert">Couldn&apos;t load the activity.</p>}
      {state === "ready" && rows.length === 0 && <p className={styles.muted}>Nothing recorded yet.</p>}
      {rows.length > 0 && (
        <ol className={styles.activity}>
          {rows.map((r) => (
            <li key={r.id}>
              <span className={styles.activityWhat}>{r.label}</span>
              <time className={styles.activityWhen} dateTime={r.occurred_at}>
                {when(r.occurred_at)}
              </time>
              <span className={styles.activityMeta}>
                {r.actor}
                {r.summary ? ` · ${r.summary}` : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
      <MoreButton hasMore={hasMore} onMore={() => void more()} />
    </section>
  );
}

export function ProductDetail({ productId }: { productId: string }) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const router = useRouter();
  const manageCatalog = can(permissions, "platform.staff_catalog.manage");
  const manageIntegration = can(permissions, "platform.staff_integration.manage");
  const [load, reload] = useLoad(() => adminApi.managedProduct(productId), [productId]);
  const { act, busy, feedback } = useAction(reload);
  const initial = params.get("tab");
  const [tab, setTab] = useState<TabKey>(TABS.includes(initial as TabKey) ? (initial as TabKey) : "overview");
  const [previewing, setPreviewing] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const saved = SAVED[params.get("saved") ?? ""];

  return (
    <div className={styles.page}>
      <Link className={styles.back} href="/admin/products">
        <span aria-hidden="true">←</span> Product Manager
      </Link>
      {saved && <p role="status">{saved}</p>}
      {feedback}
      {deleteError && (
        <p className="w360-error-text" role="alert">
          {deleteError}
        </p>
      )}
      <LoadGate load={load} what="this product">
        {(data: ManagedProduct) => {
          const p = data.product;
          const visibility = visibilityOf(p.listing);
          const logo = p.logo_media_id ? productMediaUrl(p.id, p.logo_media_id) : null;
          const deletable = manageCatalog && p.status === "draft" && !p.activated_at && p.plans.length === 0 && !p.launch_url;
          return (
            <>
              <header className={styles.detailHead}>
                <ProductMark name={p.name} icon={p.icon} src={logo} size={72} />
                <div className={styles.detailTitle}>
                  <h1>{p.name}</h1>
                  <div className={styles.badges}>
                    <span className={STATUS_BADGE[p.manager_status]}>{STATUS_LABEL[p.manager_status]}</span>
                    <span className={VISIBILITY_BADGE[visibility]}>{VISIBILITY_LABEL[visibility]}</span>
                    <span className={styles.slug}>{p.slug}</span>
                  </div>
                </div>
                <div className={styles.toolbar}>
                  <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setPreviewing(true)}>
                    Preview listing
                  </button>
                  {manageCatalog && p.manager_status !== "archived" && visibility !== "published" && (
                    <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`)}>
                      Publish
                    </button>
                  )}
                  {manageCatalog && visibility === "published" && (
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`)}>
                      Unpublish
                    </button>
                  )}
                  {deletable && (
                    <ReasonAction
                      label="Delete"
                      danger
                      busy={busy}
                      onConfirm={() =>
                        void adminApi.deleteProduct(p.id).then(
                          () => router.push("/admin/products"),
                          (err: unknown) => setDeleteError(describeError(err)),
                        )
                      }
                    />
                  )}
                </div>
              </header>
              <div className={styles.tabsWrap}>
                <Tabs
                  label="Product sections"
                  items={TABS.filter((t) => t !== "activity" || data.can_see_activity).map((key) => ({ key, label: TAB_LABEL[key] }))}
                  selected={tab}
                  onSelect={setTab}
                  listClassName={styles.tabList}
                  tabClassName={styles.tab}
                >
                  {tab === "overview" && <Overview product={p} readiness={p.readiness} />}
                  {tab === "listing" && (
                    <ListingTab
                      product={p}
                      categories={data.categories}
                      manage={manageCatalog}
                      busy={busy}
                      act={act}
                      reload={reload}
                      onPreview={() => setPreviewing(true)}
                    />
                  )}
                  {tab === "integration" && (
                    <IntegrationTab
                      product={p}
                      canSeeIntegrations={data.can_see_integrations}
                      manageCatalog={manageCatalog}
                      manageIntegration={manageIntegration}
                      busy={busy}
                      act={act}
                      reload={reload}
                    />
                  )}
                  {tab === "availability" && <AvailabilityTab key={p.updated_at ?? ""} product={p} manage={manageCatalog} busy={busy} act={act} />}
                  {tab === "updates" && <UpdatesTab product={p} manage={manageCatalog} />}
                  {tab === "plans" && <PlansPanel product={p} />}
                  {tab === "pricing" && <PricingPanel product={p} />}
                  {tab === "activity" && data.can_see_activity && <ActivityPanel productId={p.id} />}
                </Tabs>
              </div>
              {previewing && <StorePreview listing={listingOf(p)} onClose={() => setPreviewing(false)} />}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
