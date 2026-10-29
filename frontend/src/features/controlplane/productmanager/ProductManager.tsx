"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { adminApi, productMediaUrl, type ConsoleProduct, type ProductsConsole } from "@/lib/api/controlPlane";
import { can, useAdmin } from "../ControlShell";
import { LoadGate, useAction, useLoad } from "../ui";
import { STATUS_BADGE, STATUS_LABEL, VISIBILITY_BADGE, VISIBILITY_LABEL, dateOnly, trialLabel, visibilityOf, type ManagerStatus } from "./model";
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

/** "More" actions in a native disclosure: keyboard-operable for free, and it
 * closes on Escape, on a choice and on a click elsewhere. */
function MoreMenu({ label, children }: { label: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDetailsElement | null>(null);
  const close = () => {
    if (ref.current) ref.current.open = false;
  };
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, []);
  return (
    <details
      ref={ref}
      className={styles.menu}
      onKeyDown={(e) => {
        if (e.key === "Escape" && ref.current?.open) {
          close();
          ref.current.querySelector("summary")?.focus();
        }
      }}
    >
      <summary className="w360-btn w360-btn-secondary" aria-label={label}>
        More <span aria-hidden="true">▾</span>
      </summary>
      {/* A choice closes the menu: every item is a button or a link. */}
      <div className={styles.menuList} onClick={close}>
        {children}
      </div>
    </details>
  );
}

function ProductRow({
  product: p,
  manage,
  busy,
  act,
  onPreview,
}: {
  product: ConsoleProduct;
  manage: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  onPreview: (p: ConsoleProduct) => void;
}) {
  const visibility = visibilityOf(p.listing);
  const logo = p.logo_media_id ? productMediaUrl(p.id, p.logo_media_id) : null;
  const archived = p.manager_status === "archived";
  return (
    <tr>
      <th scope="row">
        <div className={styles.productCell}>
          <ProductMark name={p.name} icon={p.icon} src={logo} />
          <span>
            <Link className={styles.productName} href={`/admin/products/${p.id}`}>
              {p.name}
            </Link>
            <span className={styles.slug}>{p.slug}</span>
          </span>
        </div>
      </th>
      <td className={styles.secondary}>{p.category || <span className={styles.muted}>—</span>}</td>
      <td>
        <span className={STATUS_BADGE[p.manager_status]}>{STATUS_LABEL[p.manager_status]}</span>
      </td>
      <td>
        <span className={VISIBILITY_BADGE[visibility]}>{VISIBILITY_LABEL[visibility]}</span>
      </td>
      <td className={`${styles.secondary} ${styles.nowrap}`}>{trialLabel(p.channels.trial)}</td>
      <td className={styles.optional}>{p.plans.length}</td>
      <td className={`${styles.optional} ${styles.urlCell}`}>
        {p.launch_url ? <span className={styles.url}>{p.launch_url}</span> : <span className={styles.muted}>Not set</span>}
      </td>
      <td className={`${styles.optional} ${styles.nowrap}`}>{dateOnly(p.updated_at)}</td>
      <td>
        <div className={styles.actions}>
          <Link className="w360-btn w360-btn-secondary" href={`/admin/products/${p.id}`} aria-label={`Manage ${p.name}`}>
            Manage
          </Link>
          <MoreMenu label={`More actions for ${p.name}`}>
            <>
                {manage && (
                  <Link className={styles.menuItem} href={`/admin/products/${p.id}?tab=listing`}>
                    Edit
                  </Link>
                )}
                <button
                  type="button"
                  className={styles.menuItem}
                  onClick={() => onPreview(p)}
                >
                  Preview listing
                </button>
                {manage && !archived && visibility !== "published" && (
                  <button
                    type="button"
                    className={styles.menuItem}
                    disabled={busy}
                    onClick={() => {
                      void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`);
                    }}
                  >
                    Publish
                  </button>
                )}
                {manage && visibility === "published" && (
                  <button
                    type="button"
                    className={styles.menuItem}
                    disabled={busy}
                    onClick={() => {
                      void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`);
                    }}
                  >
                    Unpublish
                  </button>
                )}
                {manage && !archived && (
                  <button
                    type="button"
                    className={`${styles.menuItem} ${styles.danger}`}
                    disabled={busy}
                    onClick={() => {
                      void act(() => adminApi.productLifecycle(p.id, "archive"), `${p.name} is archived. Its history is kept.`);
                    }}
                  >
                    Archive
                  </button>
                )}
            </>
          </MoreMenu>
        </div>
      </td>
    </tr>
  );
}

export function ProductManager() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_catalog.manage");
  const [load, reload] = useLoad(() => adminApi.productsConsole(), []);
  const { act, busy, feedback } = useAction(reload);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [visibility, setVisibility] = useState<VisibilityFilter>("all");
  const [category, setCategory] = useState("all");
  const [preview, setPreview] = useState<ConsoleProduct | null>(null);
  const ids = { q: useId(), status: useId(), visibility: useId(), category: useId(), caption: useId() };

  return (
    <div className={styles.page}>
      <div className={styles.pageHead}>
        <div>
          <h1 className={styles.pageTitle}>Product Manager</h1>
          <p className={styles.lead}>Manage Workeasy360 products and their store availability.</p>
        </div>
        {manage && (
          <Link className="w360-btn w360-btn-primary" href="/admin/products/new">
            + Add Product
          </Link>
        )}
      </div>
      {feedback}
      <LoadGate load={load} what="products">
        {(d: ProductsConsole) => catalog(d)}
      </LoadGate>
      {preview && <StorePreview listing={listingOf(preview)} onClose={() => setPreview(null)} />}
    </div>
  );

  /** A render helper, not a component: the filter inputs keep focus as they change. */
  function catalog(data: ProductsConsole) {
    const rows = data.products.filter((p) => matches(p, q, status, visibility, category));
    const count = (s: ManagerStatus) => data.products.filter((p) => p.manager_status === s).length;
    return (
      <>
        <ul className={styles.summary} aria-label="Catalog summary">
          {[
            { label: "Products", value: data.products.length },
            { label: "Active", value: count("active") },
            { label: "Published", value: data.products.filter((p) => p.listing === "listed").length },
            { label: "Drafts", value: count("draft") },
          ].map((t) => (
            <li key={t.label} className={styles.summaryTile}>
              <span className={styles.summaryValue}>{t.value}</span>
              <span className={styles.summaryLabel}>{t.label}</span>
            </li>
          ))}
        </ul>

        <div className={styles.filters} role="search" aria-label="Filter products">
          <div>
            <label htmlFor={ids.q}>Search products</label>
            <input id={ids.q} className="w360-input" type="search" placeholder="Name, slug or category" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div>
            <label htmlFor={ids.status}>Status</label>
            <select id={ids.status} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
              <option value="all">All</option>
              <option value="draft">Draft</option>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <div>
            <label htmlFor={ids.visibility}>Store visibility</label>
            <select id={ids.visibility} className="w360-input" value={visibility} onChange={(e) => setVisibility(e.target.value as VisibilityFilter)}>
              <option value="all">All</option>
              <option value="published">Published</option>
              <option value="hidden">Hidden</option>
            </select>
          </div>
          <div>
            <label htmlFor={ids.category}>Category</label>
            <select id={ids.category} className="w360-input" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="all">All categories</option>
              {data.categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>

        <p className={styles.muted} aria-live="polite">
          {rows.length === data.products.length ? `${rows.length} product${rows.length === 1 ? "" : "s"}` : `${rows.length} of ${data.products.length} products`}
        </p>

        <div className={styles.tableWrap} role="region" aria-labelledby={ids.caption} tabIndex={0}>
          <table className={styles.table}>
            <caption id={ids.caption}>Products</caption>
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col" className={styles.secondary}>
                  Category
                </th>
                <th scope="col">Status</th>
                <th scope="col">Store visibility</th>
                <th scope="col" className={styles.secondary}>
                  Trial
                </th>
                <th scope="col" className={styles.optional}>
                  Plans
                </th>
                <th scope="col" className={styles.optional}>
                  Launch URL
                </th>
                <th scope="col" className={styles.optional}>
                  Last updated
                </th>
                <th scope="col">
                  <span className="w360-visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <ProductRow key={p.id} product={p} manage={manage} busy={busy} act={act} onPreview={setPreview} />
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className={styles.empty}>{data.products.length === 0 ? "No products yet." : "No product matches these filters."}</p>
          )}
        </div>
      </>
    );
  }
}
