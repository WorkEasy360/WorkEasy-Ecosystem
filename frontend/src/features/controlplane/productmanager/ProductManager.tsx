"use client";

import { useState } from "react";
import Link from "next/link";
import { adminApi, productMediaUrl, type ConsoleProduct, type ProductsConsole } from "@/lib/api/controlPlane";
import { can, useAdmin } from "../ControlShell";
import { LoadGate, useAction, useLoad } from "../ui";
import {
  Badge,
  ButtonLink,
  DataTable,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SearchInput,
  Select,
  StatCard,
  StatGrid,
  Trunc,
  UrlCell,
  formatDate,
  useConfirm,
  type Column,
  type MenuItem,
  type Tone,
} from "../kit";
import { STATUS_LABEL, VISIBILITY_LABEL, trialLabel, visibilityOf, type ManagerStatus, type Visibility } from "./model";
import { ProductMark } from "./ProductMark";
import { StorePreview, type ListingPreview } from "./StorePreview";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Product Store → Product Manager (ADR-051): the one central
 * product catalog. Every product - the four live today and any added later -
 * is a row here; nothing on this screen knows a product by its code.
 *
 * Status (draft, active, archived) and store visibility (published, hidden) are
 * two separate facts, and neither is access: a customer uses a product only
 * through an entitlement, made by a trial or a purchase.
 */

/** A stored product as the store preview draws it. */
export function listingOf(p: ConsoleProduct): ListingPreview {
  const logo = p.media.find((m) => m.kind === "logo");
  const banner = p.media.find((m) => m.kind === "banner");
  const trial = p.channels.trial;
  return {
    name: p.name,
    icon: p.icon,
    storeTitle: p.store_title,
    storeSummary: p.store_summary,
    tagline: p.tagline,
    category: p.category,
    description: p.description,
    trialDays: trial?.enabled ? trial.duration_days : null,
    logoSrc: logo ? productMediaUrl(p.id, logo.id) : null,
    bannerSrc: banner ? productMediaUrl(p.id, banner.id) : null,
    shots: p.media
      .filter((m) => m.kind === "screenshot")
      .map((m) => ({ key: m.id, src: productMediaUrl(p.id, m.id), caption: m.caption })),
    learnMoreUrl: p.marketing_url,
    documentationUrl: p.documentation_url,
    supportUrl: p.support_url,
  };
}

type StatusFilter = "all" | ManagerStatus;
type VisibilityFilter = "all" | "published" | "hidden";

function matches(p: ConsoleProduct, q: string, status: StatusFilter, visibility: VisibilityFilter, category: string): boolean {
  const text = q.trim().toLowerCase();
  if (text && ![p.name, p.slug, p.code, p.category].some((v) => v.toLowerCase().includes(text))) return false;
  if (status !== "all" && p.manager_status !== status) return false;
  if (visibility === "published" && p.listing !== "listed") return false;
  if (visibility === "hidden" && p.listing === "listed") return false;
  return category === "all" || p.category === category;
}

const STATUS_TONE: Record<ManagerStatus, Tone> = { draft: "neutral", active: "success", archived: "danger" };
const VISIBILITY_TONE: Record<Visibility, Tone> = { published: "success", hidden: "neutral", coming_soon: "info" };

/** A product's status in the one badge style. */
export function ManagerStatusBadge({ status }: { status: ManagerStatus }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/** A product's store visibility in the one badge style. */
export function VisibilityBadge({ visibility }: { visibility: Visibility }) {
  return (
    <Badge tone={VISIBILITY_TONE[visibility] ?? "neutral"} dot>
      {VISIBILITY_LABEL[visibility]}
    </Badge>
  );
}

export function ProductManager() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_catalog.manage");
  const [load, reload] = useLoad(() => adminApi.productsConsole(), []);
  const { act, busy, feedback } = useAction(reload);
  const confirm = useConfirm();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [visibility, setVisibility] = useState<VisibilityFilter>("all");
  const [category, setCategory] = useState("all");
  const [preview, setPreview] = useState<ConsoleProduct | null>(null);

  const columns: Column<ConsoleProduct>[] = [
    {
      key: "product",
      header: "Product",
      value: (p) => p.name,
      render: (p) => {
        const logo = p.logo_media_id ? productMediaUrl(p.id, p.logo_media_id) : null;
        return (
          <span className={styles.productLine}>
            <ProductMark name={p.name} icon={p.icon} src={logo} size={32} />
            <span className={styles.productText}>
              <Link href={`/admin/products/${p.id}`}>{p.name}</Link>
              <span className={styles.productSlug}>{p.slug}</span>
            </span>
          </span>
        );
      },
    },
    { key: "category", header: "Category", value: (p) => p.category, render: (p) => <Trunc max="sm">{p.category}</Trunc> },
    { key: "status", header: "Status", value: (p) => p.manager_status, render: (p) => <ManagerStatusBadge status={p.manager_status} /> },
    {
      key: "visibility",
      header: "Store visibility",
      value: (p) => visibilityOf(p.listing),
      render: (p) => <VisibilityBadge visibility={visibilityOf(p.listing)} />,
    },
    {
      key: "trial",
      header: "Trial",
      value: (p) => (p.channels.trial?.enabled ? p.channels.trial.duration_days : null),
      render: (p) => trialLabel(p.channels.trial),
    },
    { key: "plans", header: "Plans", align: "right", value: (p) => p.plans.length, render: (p) => p.plans.length },
    {
      key: "launch",
      header: "Launch URL",
      render: (p) => (p.launch_url ? <UrlCell url={p.launch_url} /> : <span className={styles.muted}>Not set</span>),
    },
    { key: "updated", header: "Last updated", value: (p) => p.updated_at, render: (p) => formatDate(p.updated_at) },
  ];

  const menu = (p: ConsoleProduct): MenuItem[] => {
    const shown = visibilityOf(p.listing);
    const archived = p.manager_status === "archived";
    return [
      { label: "Manage", icon: "settings", href: `/admin/products/${p.id}` },
      { label: "Edit listing", icon: "edit", href: `/admin/products/${p.id}?tab=listing`, when: manage },
      { label: "Preview listing", icon: "eye", onSelect: () => setPreview(p) },
      {
        label: "Publish",
        icon: "upload",
        disabled: busy,
        when: manage && !archived && shown !== "published",
        onSelect: () => void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`),
      },
      {
        label: "Unpublish",
        icon: "eyeOff",
        disabled: busy,
        when: manage && shown === "published",
        onSelect: () => void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`),
      },
      {
        label: "Archive",
        icon: "box",
        danger: true,
        separator: true,
        disabled: busy,
        when: manage && !archived,
        onSelect: () =>
          confirm.ask({
            title: `Archive ${p.name}?`,
            message: "It is hidden from the store and can no longer be sold. Its history, plans and customers' records are kept.",
            confirmLabel: "Archive",
            danger: true,
            onConfirm: () => act(() => adminApi.productLifecycle(p.id, "archive"), `${p.name} is archived. Its history is kept.`),
          }),
      },
    ];
  };

  return (
    <Page>
      <PageHeader
        title="Products"
        subtitle="Manage WorkEasy360 products and their store availability."
        info="Status (draft, active, archived) and store visibility (published, hidden) are separate facts. Neither gives access: a customer uses a product only through a trial or a purchase."
        primary={
          manage ? (
            <ButtonLink variant="primary" icon="plus" href="/admin/products/new">
              New product
            </ButtonLink>
          ) : undefined
        }
      />
      {feedback}
      <LoadGate load={load} what="products">
        {(d: ProductsConsole) => catalog(d)}
      </LoadGate>
      {preview && <StorePreview listing={listingOf(preview)} onClose={() => setPreview(null)} />}
      {confirm.dialog}
    </Page>
  );

  /** A render helper, not a component: the filter inputs keep focus as they change. */
  function catalog(data: ProductsConsole) {
    const rows = data.products.filter((p) => matches(p, q, status, visibility, category));
    const count = (s: ManagerStatus) => data.products.filter((p) => p.manager_status === s).length;
    const filtered = q !== "" || status !== "all" || visibility !== "all" || category !== "all";
    return (
      <>
        <StatGrid label="Catalog summary">
          <StatCard label="Products" icon="box" value={data.products.length} />
          <StatCard label="Active" icon="checkCircle" tone="blue" value={count("active")} />
          <StatCard label="Published" icon="store" tone="purple" value={data.products.filter((p) => p.listing === "listed").length} />
          <StatCard label="Drafts" icon="edit" tone="orange" value={count("draft")} />
        </StatGrid>

        <FilterBar
          label="Filter products"
          onClear={
            filtered
              ? () => {
                  setQ("");
                  setStatus("all");
                  setVisibility("all");
                  setCategory("all");
                }
              : undefined
          }
          end={
            <span className={styles.muted} aria-live="polite">
              {rows.length === data.products.length
                ? `${rows.length} product${rows.length === 1 ? "" : "s"}`
                : `${rows.length} of ${data.products.length} products`}
            </span>
          }
        >
          <SearchInput label="Search products" placeholder="Name, slug or category" value={q} onSearch={setQ} />
          <Select<StatusFilter>
            label="Status"
            prefix="Status:"
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: "All" },
              { value: "draft", label: "Draft" },
              { value: "active", label: "Active" },
              { value: "archived", label: "Archived" },
            ]}
          />
          <Select<VisibilityFilter>
            label="Store visibility"
            prefix="Visibility:"
            value={visibility}
            onChange={setVisibility}
            options={[
              { value: "all", label: "All" },
              { value: "published", label: "Published" },
              { value: "hidden", label: "Hidden" },
            ]}
          />
          <Select
            label="Category"
            prefix="Category:"
            value={category}
            onChange={setCategory}
            options={[{ value: "all", label: "All categories" }, ...data.categories.map((c) => ({ value: c, label: c }))]}
          />
        </FilterBar>

        <DataTable
          caption="Products"
          columns={columns}
          rows={rows}
          rowKey={(p) => p.id}
          rowHref={(p) => `/admin/products/${p.id}`}
          rowLabel={(p) => p.name}
          rowMenu={menu}
          hideEmptyColumns={false}
          empty={
            data.products.length === 0 ? (
              <EmptyState
                icon="box"
                title="No products yet"
                hint="Add the first product to the central catalog."
                action={manage ? { label: "New product", href: "/admin/products/new" } : undefined}
              />
            ) : (
              <EmptyState icon="search" title="No product matches these filters" hint="Change or clear the filters to see more." />
            )
          }
        />
      </>
    );
  }
}
