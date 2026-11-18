"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";
import { LoadGate, Table, money, useLoad, when } from "@/features/controlplane/ui";
import { dialog, FormDialog } from "@/features/controlplane/Modal";
import {
  panelApi,
  type ProductAccess,
  type PromotionRequest,
  type RequestKind,
  type StoreConsole as StoreConsoleData,
  type StoreProduct,
} from "@/lib/api/partnerPanel";
import { MyProductsTab } from "./AffiliateProducts";
import { GrantedProducts } from "./Catalog";
import { EmptyState, PageHeader, SubNav, monogram, usePanel } from "./PanelKit";
import { percent, useFieldChecks } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * The Store Console (ADR-055): a partner's view of the one Admin product
 * catalog, in three tabs - for an affiliate (`promote`) or a reseller (`sell`).
 *
 * - **My Products** - what Workeasy360 has approved this partner to promote
 *   (ADR-054's page: link generator and figures) or to sell (the granted
 *   products table of Partner Products).
 * - **Explore Products** - every Store product open to the partner's channel,
 *   with its status for each and a Request Access form.
 * - **My Requests** - every request it made and what became of it.
 *
 * Nothing here is a gate. A request grants nothing until staff approve it, and
 * the server refuses a link or a sale without approval whatever this page
 * shows. Every figure, price, rate and status is the server's.
 */

export type StoreTab = "mine" | "explore" | "requests";

export const ACCESS_LABELS: Record<ProductAccess, string> = {
  not_requested: "Not requested",
  pending: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  revoked: "Revoked",
};

const ACCESS_BADGES: Record<ProductAccess, string> = {
  not_requested: "w360-badge w360-badge-neutral",
  pending: "w360-badge w360-badge-warning",
  approved: "w360-badge w360-badge-success",
  rejected: "w360-badge w360-badge-danger",
  revoked: "w360-badge w360-badge-danger",
};

export function AccessBadge({ status }: { status: ProductAccess }) {
  return <span className={ACCESS_BADGES[status]}>{ACCESS_LABELS[status]}</span>;
}

const INTERVALS: Record<string, string> = { monthly: "month", yearly: "year", annual: "year", quarterly: "quarter" };

function priceText(p: StoreProduct): string | null {
  if (!p.starting_price) return null;
  const per = INTERVALS[p.starting_price.interval] ?? p.starting_price.interval;
  return `From ${money(p.starting_price.amount_minor, p.starting_price.currency)} / ${per}`;
}

function commissionText(p: StoreProduct): string | null {
  if (p.commission === null) return null; // not disclosed to this member
  if (p.commission.rate_bp === null) return "Commission: not set yet";
  return `Commission: ${percent(p.commission.rate_bp)} of each paid sale${p.commission.recurring ? ", renewals too" : ""}`;
}

/** What a sale earns: a seller's margin when it has one, else the commission. */
export function earningText(p: StoreProduct): string | null {
  if (p.margin?.discount_bp != null) return `Your margin: ${percent(p.margin.discount_bp)} off the list price`;
  return commissionText(p);
}

/** The words that differ between promoting and selling; everything else is shared. */
export const WORDS: Record<RequestKind, { verb: string; lead: string; nobody: string; channel: string; audience: string; how: string; mine: string }> = {
  promote: {
    verb: "promote",
    lead: "Products from the Workeasy360 Store. Promote the ones you are approved for, and request access to the others.",
    nobody: "No products are open to affiliates yet",
    channel: "Promotion channel",
    audience: "Target audience",
    how: "How you will promote it",
    // The affiliate workspace's tab for it (2026-11-02).
    mine: "Approved / Promoting",
  },
  sell: {
    verb: "sell",
    lead: "Products from the Workeasy360 Store. Sell the ones you are approved for, and request access to the others.",
    nobody: "No products are open to resellers yet",
    channel: "Sales channel",
    audience: "Target customers",
    how: "How you will sell it",
    mine: "Approved / Reselling",
  },
};

/** `root` is where the Console lives in its workspace: `store-console`, or
 * `promote/store-console` for an upgraded affiliate in a higher one (2026-11-03). */
export function StoreConsole({ tab, kind = "promote", root = "store-console" }: { tab: StoreTab; kind?: RequestKind; root?: string }) {
  const { id, base } = usePanel();
  const [load, reload] = useLoad(() => panelApi.storeConsole(id, kind), [id, kind]);
  const counts = load.status === "ready" ? load.data.counts : null;
  const n = (value: number | undefined) => (value === undefined ? "" : `(${value})`);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Store Console"
        lead={WORDS[kind].lead}
      />
      <SubNav
        items={[
          { href: `${base}/${root}`, label: WORDS[kind].mine, note: n(counts?.approved) },
          { href: `${base}/${root}/explore`, label: "Explore Products", note: n(counts?.explore) },
          {
            href: `${base}/${root}/requests`,
            label: "My Requests",
            note: counts && counts.pending > 0 ? `(${counts.pending} pending)` : "",
          },
        ]}
      />
      {tab === "mine" ? (
        kind === "sell" ? (
          <SellerProducts />
        ) : (
          <MyProductsTab exploreHref={`${base}/${root}/explore`} />
        )
      ) : (
        <LoadGate load={load} what="the Store">
          {(d) => (tab === "explore" ? <Explore data={d} kind={kind} root={root} onChanged={reload} /> : <Requests data={d} kind={kind} root={root} />)}
        </LoadGate>
      )}
    </div>
  );
}

/** A reseller's My Products: the products it may sell (Partner Products' table)
 * and the existing pages where it sells them - no copy of any of them. */
function SellerProducts() {
  const { base, can } = usePanel();
  return (
    <>
      <GrantedProducts explore={`${base}/store-console/explore`} />
      <ul className={styles.quick} aria-label="Sell your products">
        {can("customer.view") && (
          <li>
            <Link href={`${base}/users`}>Customers</Link>
          </li>
        )}
        {can("customer.view") && (
          <li>
            <Link href={`${base}/payments`}>Sales &amp; Orders</Link>
          </li>
        )}
        {can("pricing.view") && (
          <li>
            <Link href={`${base}/plans`}>Pricing &amp; Margins</Link>
          </li>
        )}
        <li>
          <Link href={`${base}/product-billing`}>Product Billing</Link>
        </li>
      </ul>
    </>
  );
}

// --- Explore Products ------------------------------------------------------------------

function Explore({ data, kind, root, onChanged }: { data: StoreConsoleData; kind: RequestKind; root: string; onChanged: () => void }) {
  const { base } = usePanel();
  const searchId = useId();
  const categoryId = useId();
  const statusId = useId();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState<"" | ProductAccess>("");
  const [asking, setAsking] = useState<StoreProduct | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const categories = useMemo(() => [...new Set(data.products.map((p) => p.category).filter(Boolean))].sort(), [data.products]);
  const text = search.trim().toLowerCase();
  const shown = data.products.filter(
    (p) =>
      (!text || `${p.name} ${p.tagline} ${p.summary} ${p.category}`.toLowerCase().includes(text)) &&
      (!category || p.category === category) &&
      (!status || p.status === status),
  );
  return (
    <>
      {!data.can_request && data.cannot_request_reason && (
        <p className={styles.muted} role="note">
          {data.cannot_request_reason}
        </p>
      )}
      {done && (
        <p role="status" className={styles.notice}>
          {done}
        </p>
      )}
      {asking && (
        <RequestForm
          product={asking}
          kind={kind}
          data={data}
          onCancel={() => setAsking(null)}
          onSent={(name) => {
            setAsking(null);
            setDone(`Request sent for ${name}. It shows as Pending approval until Workeasy360 decides.`);
            onChanged();
          }}
        />
      )}
      <div className={styles.filters} role="search" aria-label="Find products">
        <div>
          <label className="w360-label" htmlFor={searchId}>
            Search
          </label>
          <input id={searchId} type="search" className="w360-input" maxLength={80} value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {categories.length > 1 && (
          <div>
            <label className="w360-label" htmlFor={categoryId}>
              Category
            </label>
            <select id={categoryId} className="w360-input" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="w360-label" htmlFor={statusId}>
            Status
          </label>
          <select id={statusId} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value as "" | ProductAccess)}>
            <option value="">Any status</option>
            {(Object.keys(ACCESS_LABELS) as ProductAccess[]).map((s) => (
              <option key={s} value={s}>
                {ACCESS_LABELS[s]}
              </option>
            ))}
          </select>
        </div>
      </div>
      {data.products.length === 0 ? (
        <EmptyState title={WORDS[kind].nobody} icon="store">
          <p className={styles.muted}>Products appear here as soon as Workeasy360 publishes them on the Store for your channel.</p>
        </EmptyState>
      ) : shown.length === 0 ? (
        <p className={styles.muted}>No product matches these filters.</p>
      ) : (
        <ul className={styles.cards} aria-label="Products">
          {shown.map((p) => (
            <ProductCard
              key={p.code}
              product={p}
              canRequest={data.can_request}
              myProductsHref={`${base}/${root}`}
              mineLabel={WORDS[kind].mine}
              onRequest={() => {
                setDone(null);
                setAsking(p);
              }}
            />
          ))}
        </ul>
      )}
    </>
  );
}

function ProductCard({
  product: p,
  canRequest,
  myProductsHref,
  mineLabel,
  onRequest,
}: {
  product: StoreProduct;
  canRequest: boolean;
  myProductsHref: string;
  mineLabel: string;
  onRequest: () => void;
}) {
  const headingId = useId();
  const price = priceText(p);
  const rate = earningText(p);
  const again = p.status === "rejected" || p.status === "revoked";
  return (
    <li className={styles.card} aria-labelledby={headingId}>
      <div className={styles.cardHead}>
        {p.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- same-origin listing image, sized by CSS
          <img className={styles.productIcon} src={p.logo_url} alt="" width={36} height={36} loading="lazy" />
        ) : (
          <span className={styles.productIcon} aria-hidden="true">
            {monogram(p.name)}
          </span>
        )}
        <div>
          <h2 id={headingId} className={styles.cardTitle}>
            {p.name}
          </h2>
          {p.category && <span className={styles.muted}>{p.category}</span>}
        </div>
      </div>
      <AccessBadge status={p.status} />
      {(p.summary || p.tagline) && <p className={styles.muted}>{p.summary || p.tagline}</p>}
      {price && <p>{price}</p>}
      {rate && <p>{rate}</p>}
      {p.status === "pending" && p.request && <p className={styles.muted}>Requested on {when(p.request.requested_at)}.</p>}
      {p.status === "rejected" && p.request?.decision_reason && <p className={styles.muted}>Reason: {p.request.decision_reason}</p>}
      {p.status === "revoked" && p.request?.revoke_reason && <p className={styles.muted}>Reason: {p.request.revoke_reason}</p>}
      <div className={styles.actions}>
        {p.status === "approved" ? (
          <Link className="w360-btn w360-btn-secondary" href={myProductsHref} aria-label={`Open ${p.name} in ${mineLabel}`}>
            Open in {mineLabel}
          </Link>
        ) : p.status === "pending" ? null : (
          canRequest && (
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              onClick={onRequest}
              aria-label={`${again ? "Request again" : "Request access"} to ${p.name}`}
            >
              {again ? "Request again" : "Request access"}
            </button>
          )
        )}
      </div>
    </li>
  );
}

/** The access request, in a popup opened from a product's card. */
export function RequestForm({
  product,
  kind,
  data,
  onCancel,
  onSent,
}: {
  product: StoreProduct;
  kind: RequestKind;
  data: Pick<StoreConsoleData, "channels" | "prefill">;
  onCancel: () => void;
  onSent: (name: string) => void;
}) {
  const { id } = usePanel();
  const formId = useId();
  const checks = useFieldChecks();
  const [channel, setChannel] = useState("");
  const [website, setWebsite] = useState(data.prefill.website);
  const [audience, setAudience] = useState("");
  const [strategy, setStrategy] = useState("");
  const [notes, setNotes] = useState("");
  return (
    <FormDialog
      title={`Request access to ${product.name}`}
      intro={
        <>
          Tell Workeasy360 how you will {WORDS[kind].verb} it. Your partner profile is used for everything else; do not include anyone else&apos;s
          personal details.
        </>
      }
      submitLabel="Send request"
      onSubmit={async () => {
        checks.check();
        await panelApi.requestProduct(id, kind, {
          product_code: product.code,
          channel,
          website: website.trim() || undefined,
          audience: audience.trim(),
          strategy: strategy.trim(),
          notes: notes.trim() || undefined,
        });
        return "Request sent.";
      }}
      onDone={() => onSent(product.name)}
      onClose={onCancel}
    >
      <div>
        <label className="w360-label" htmlFor={`${formId}-channel`}>
          {WORDS[kind].channel}
        </label>
        <select id={`${formId}-channel`} className="w360-input" required value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">Choose…</option>
          {Object.entries(data.channels).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="w360-label" htmlFor={`${formId}-website`}>
          Website or social profile (optional)
        </label>
        <input
          id={`${formId}-website`}
          className="w360-input"
          type="url"
          inputMode="url"
          placeholder="https://"
          maxLength={300}
          pattern="https://.+"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
        />
      </div>
      <div className={dialog.span}>
        <label className="w360-label" htmlFor={`${formId}-audience`}>
          {WORDS[kind].audience}
        </label>
        <input
          id={`${formId}-audience`}
          className="w360-input"
          required
          minLength={3}
          maxLength={300}
          value={audience}
          onChange={(e) => setAudience(e.target.value)}
        />
      </div>
      <div className={dialog.span}>
        <label className="w360-label" htmlFor={`${formId}-strategy`}>
          {WORDS[kind].how}
        </label>
        <textarea
          id={`${formId}-strategy`}
          className="w360-input"
          required
          minLength={10}
          maxLength={1000}
          rows={4}
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
        />
      </div>
      <div className={dialog.span}>
        <label className="w360-label" htmlFor={`${formId}-notes`}>
          Anything else (optional)
        </label>
        <textarea id={`${formId}-notes`} className="w360-input" maxLength={1000} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {checks.probe}
    </FormDialog>
  );
}

// --- My Requests --------------------------------------------------------------------------

function Requests({ data, kind, root }: { data: StoreConsoleData; kind: RequestKind; root: string }) {
  const { base } = usePanel();
  const statusId = useId();
  const [status, setStatus] = useState<"" | PromotionRequest["status"]>("");
  const shown = data.requests.filter((r) => !status || r.status === status);
  if (data.requests.length === 0) {
    return (
      <EmptyState title="No requests yet" icon="store">
        <p className={styles.muted}>
          Find a product in <Link href={`${base}/${root}/explore`}>Explore Products</Link> and request access to promote it.
        </p>
      </EmptyState>
    );
  }
  return (
    <>
      <div className={styles.filters}>
        <div>
          <label className="w360-label" htmlFor={statusId}>
            Status
          </label>
          <select id={statusId} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value as "" | PromotionRequest["status"])}>
            <option value="">All requests</option>
            <option value="pending">Pending approval</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="revoked">Revoked</option>
          </select>
        </div>
      </div>
      <Table
        caption="Your product requests"
        head={["Product", "Requested", "Channel", "Status", "Decided", "Reason"]}
        isEmpty={shown.length === 0}
        empty="No request has this status."
      >
        {shown.map((r) => (
          <tr key={r.id}>
            <th scope="row">{r.product_name}</th>
            <td>{when(r.requested_at)}</td>
            <td>{r.channel_label}</td>
            <td>
              <AccessBadge status={r.status} />
            </td>
            <td>{r.status === "revoked" ? when(r.revoked_at) : when(r.decided_at)}</td>
            <td>{(r.status === "revoked" ? r.revoke_reason : r.decision_reason) || "—"}</td>
          </tr>
        ))}
      </Table>
      <p className={styles.muted}>
        Approved products are in {WORDS[kind].mine}. Commission you earned before a permission was revoked is kept.
      </p>
    </>
  );
}
