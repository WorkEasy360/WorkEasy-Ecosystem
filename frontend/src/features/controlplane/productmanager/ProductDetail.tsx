"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { adminApi, productMediaUrl, type ConsoleProduct, type ManagedProduct, type ProductActivityRow, type ReadinessItem } from "@/lib/api/controlPlane";
import { can, useAdmin } from "../ControlShell";
import { LoadGate, MoreButton, ReasonAction, describeError, useAction, useLoad, usePaged, when } from "../ui";
import {
  Alert,
  Button,
  ButtonLink,
  Card,
  DescriptionList,
  EmptyState,
  LoadingBlock,
  Page,
  PageHeader,
  Tabs,
  UrlCell,
  formatDate,
} from "../kit";
import { Icon } from "@/components/sidebar/icons";
import { AvailabilityTab } from "./AvailabilityTab";
import { IntegrationTab } from "./IntegrationTab";
import { ListingTab } from "./ListingTab";
import { PlansPricingTab } from "./PlansPricingTab";
import { trialLabel, visibilityOf } from "./model";
import { ManagerStatusBadge, VisibilityBadge, listingOf } from "./ProductManager";
import { ProductMark } from "./ProductMark";
import { StorePreview } from "./StorePreview";
import { UpdatesTab } from "./UpdatesTab";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Product Manager → one product (ADR-051). ADR-056: the Plans &
 * Pricing tab edits prices, discounts, set-up fees, effective dates and plan
 * states in place; the plan builder and the price books stay one link away.
 */

export const TABS = ["overview", "listing", "integration", "availability", "updates", "plans", "pricing", "activity"] as const;
type TabKey = (typeof TABS)[number];
const TAB_LABEL: Record<TabKey, string> = {
  overview: "Overview",
  listing: "Store listing",
  integration: "Integration",
  availability: "Availability",
  updates: "Updates",
  plans: "Plans & pricing",
  pricing: "Price books",
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
            <Icon name={i.ok ? "check" : "alert"} size={14} />
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
  const missing = readiness.filter((i) => !i.ok).length;
  return (
    <div className={styles.twoCol}>
      <Card title="Overview" fill>
        <DescriptionList
          items={[
            { label: "Product name", value: p.name },
            { label: "Slug", value: <code className={styles.code}>{p.slug}</code> },
            { label: "Product code", value: <code className={styles.code}>{p.code}</code> },
            { label: "Category", value: p.category },
            { label: "Short description", value: p.tagline },
            { label: "Status", value: <ManagerStatusBadge status={p.manager_status} /> },
            { label: "Store visibility", value: <VisibilityBadge visibility={visibilityOf(p.listing)} /> },
            { label: "Trial", value: trialLabel(p.channels.trial) },
            { label: "Application URL", value: p.application_url ? <UrlCell url={p.application_url} /> : null },
            { label: "Launch URL", value: p.launch_url ? <UrlCell url={p.launch_url} /> : null },
            { label: "Created", value: formatDate(p.created_at) },
            { label: "Last updated", value: when(p.updated_at) },
          ]}
        />
      </Card>
      <Card
        title="Ready to publish?"
        description={missing === 0 ? "Everything the store needs is in place." : `${missing} item${missing === 1 ? "" : "s"} missing.`}
        fill
      >
        <Checklist items={readiness} />
        {!p.channels.direct.ready && (
          <Alert tone="warning">
            <p className={styles.note}>Plans/Pricing required before final Store sale activation.</p>
          </Alert>
        )}
      </Card>
    </div>
  );
}

function PricingPanel({ product: p }: { product: ConsoleProduct }) {
  const priced = p.plans.filter((plan) => plan.priced);
  return (
    <Card
      title="Pricing"
      description="Prices live in Currency & pricing, one price book per currency."
      actions={
        <ButtonLink variant="primary" iconEnd="chevronRight" href={`/admin/pricing?product=${encodeURIComponent(p.code)}`}>
          Manage pricing
        </ButtonLink>
      }
    >
      <p>
        {priced.length} of {p.plans.length} plan{p.plans.length === 1 ? "" : "s"} priced.
      </p>
    </Card>
  );
}

function ActivityPanel({ productId }: { productId: string }) {
  const { rows, hasMore, more, state } = usePaged<ProductActivityRow>((cursor) => adminApi.productActivity(productId, cursor), [productId]);
  return (
    <Card title="Activity">
      {state === "loading" && <LoadingBlock what="activity" />}
      {state === "forbidden" && <p role="alert">Activity needs the audit view permission.</p>}
      {state === "error" && <p role="alert">Couldn&apos;t load the activity.</p>}
      {state === "ready" && rows.length === 0 && <EmptyState icon="history" title="Nothing recorded yet" compact />}
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
    </Card>
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

  const crumbs = (name: string) => [{ label: "Products", href: "/admin/products" }, { label: name }];
  return (
    <Page>
      {saved && <Alert tone="success">{saved}</Alert>}
      {feedback}
      {deleteError && <Alert tone="danger">{deleteError}</Alert>}
      <LoadGate load={load} what="this product">
        {(data: ManagedProduct) => {
          const p = data.product;
          const visibility = visibilityOf(p.listing);
          const logo = p.logo_media_id ? productMediaUrl(p.id, p.logo_media_id) : null;
          const deletable = manageCatalog && p.status === "draft" && !p.activated_at && p.plans.length === 0 && !p.launch_url;
          const tabs = TABS.filter((t) => t !== "activity" || data.can_see_activity).map((key) => ({ key, label: TAB_LABEL[key] }));
          return (
            <>
              <PageHeader
                crumbs={crumbs(p.name)}
                documentTitle={`${p.name} · Products`}
                avatar={<ProductMark name={p.name} icon={p.icon} src={logo} size={56} />}
                title={p.name}
                badges={
                  <>
                    <ManagerStatusBadge status={p.manager_status} />
                    <VisibilityBadge visibility={visibility} />
                  </>
                }
                meta={[<code key="slug" className={styles.code}>{p.slug}</code>, p.category || null, `Updated ${formatDate(p.updated_at)}`].filter(Boolean)}
                actions={
                  <>
                    <Button icon="eye" onClick={() => setPreviewing(true)}>
                      Preview listing
                    </Button>
                    {manageCatalog && visibility === "published" && (
                      <Button
                        icon="eyeOff"
                        disabled={busy}
                        onClick={() => void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`)}
                      >
                        Unpublish
                      </Button>
                    )}
                    {deletable && (
                      <ReasonAction
                        label="Delete"
                        danger
                        busy={busy}
                        message="The draft product and everything saved for it are deleted. This cannot be undone."
                        onConfirm={() =>
                          void adminApi.deleteProduct(p.id).then(
                            () => router.push("/admin/products"),
                            (err: unknown) => setDeleteError(describeError(err)),
                          )
                        }
                      />
                    )}
                  </>
                }
                primary={
                  manageCatalog && p.manager_status !== "archived" && visibility !== "published" ? (
                    <Button
                      variant="primary"
                      icon="upload"
                      disabled={busy}
                      onClick={() => void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`)}
                    >
                      Publish
                    </Button>
                  ) : undefined
                }
              />
              <Tabs label="Product sections" tabs={tabs} selected={tab} onSelect={setTab}>
                {tab === "overview" && <Overview product={p} readiness={p.readiness} />}
                {tab === "listing" && (
                  <ListingTab
                    product={p}
                    categories={data.categories}
                    manage={manageCatalog}
                    busy={busy}
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
                {tab === "availability" && <AvailabilityTab product={p} manage={manageCatalog} busy={busy} act={act} reload={reload} />}
                {tab === "updates" && <UpdatesTab product={p} manage={manageCatalog} />}
                {tab === "plans" && <PlansPricingTab productId={p.id} />}
                {tab === "pricing" && <PricingPanel product={p} />}
                {tab === "activity" && data.can_see_activity && <ActivityPanel productId={p.id} />}
              </Tabs>
              {previewing && <StorePreview listing={listingOf(p)} onClose={() => setPreviewing(false)} />}
            </>
          );
        }}
      </LoadGate>
    </Page>
  );
}
