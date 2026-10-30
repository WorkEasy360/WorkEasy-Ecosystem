"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { LoadGate, Table, money, useAction, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type ProductAccess,
  type PromotionRequest,
  type StoreConsole as StoreConsoleData,
  type StoreProduct,
} from "@/lib/api/partnerPanel";
import { MyProductsTab } from "./AffiliateProducts";
import { EmptyState, PageHeader, SubNav, monogram, usePanel } from "./PanelKit";
import { percent } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * The Store Console (ADR-055): the affiliate's view of the one Admin product
 * catalog, in three tabs.
 *
 * - **My Products** - what Workeasy360 has approved this affiliate to promote,
 *   with the link generator and figures (ADR-054's page, unchanged).
 * - **Explore Products** - every Store product open to affiliates, with this
 *   affiliate's status for each and a Request Access form.
 * - **My Requests** - every request it made and what became of it.
 *
 * Nothing here is a gate. A request grants nothing until staff approve it, and
 * the server refuses a link for a product without approval whatever this page
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

export function StoreConsole({ tab }: { tab: StoreTab }) {
  const { id, base } = usePanel();
  const [load, reload] = useLoad(() => panelApi.storeConsole(id), [id]);
  const counts = load.status === "ready" ? load.data.counts : null;
  const n = (value: number | undefined) => (value === undefined ? "" : ` (${value})`);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Store Console"
        lead="Products from the Workeasy360 Store. Promote the ones you are approved for, and request access to the others."
      />
      <SubNav
        items={[
          { href: `${base}/store-console`, label: `My Products${n(counts?.approved)}` },
          { href: `${base}/store-console/explore`, label: `Explore Products${n(counts?.explore)}` },
          {
            href: `${base}/store-console/requests`,
            label: `My Requests${counts && counts.pending > 0 ? ` (${counts.pending} pending)` : ""}`,
          },
        ]}
      />
      {tab === "mine" ? (
        <MyProductsTab exploreHref={`${base}/store-console/explore`} />
      ) : (
        <LoadGate load={load} what="the Store">
          {(d) => (tab === "explore" ? <Explore data={d} onChanged={reload} /> : <Requests data={d} />)}
        </LoadGate>
      )}
    </div>
  );
}

// --- Explore Products ------------------------------------------------------------------

function Explore({ data, onChanged }: { data: StoreConsoleData; onChanged: () => void }) {
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
        <EmptyState title="No products are open to affiliates yet" icon="store">
          <p className={styles.muted}>Products appear here as soon as Workeasy360 publishes them on the Store for affiliates.</p>
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
              myProductsHref={`${base}/store-console`}
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
  onRequest,
}: {
  product: StoreProduct;
  canRequest: boolean;
  myProductsHref: string;
  onRequest: () => void;
}) {
  const headingId = useId();
  const price = priceText(p);
  const rate = commissionText(p);
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
          <Link className="w360-btn w360-btn-secondary" href={myProductsHref} aria-label={`Promote ${p.name} in My Products`}>
            Promote in My Products
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

function RequestForm({
  product,
  data,
  onCancel,
  onSent,
}: {
  product: StoreProduct;
  data: StoreConsoleData;
  onCancel: () => void;
  onSent: (name: string) => void;
}) {
  const { id } = usePanel();
  const formId = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const { act, busy, feedback } = useAction();
  const [channel, setChannel] = useState("");
  const [website, setWebsite] = useState(data.prefill.website);
  const [audience, setAudience] = useState("");
  const [strategy, setStrategy] = useState("");
  const [notes, setNotes] = useState("");
  // The form opens above the list: take the keyboard to it.
  useEffect(() => heading.current?.focus(), [product.code]);
  return (
    <form
      className={styles.panel}
      aria-labelledby={`${formId}-h`}
      onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          await panelApi.requestProduct(id, {
            product_code: product.code,
            channel,
            website: website.trim() || undefined,
            audience: audience.trim(),
            strategy: strategy.trim(),
            notes: notes.trim() || undefined,
          });
          onSent(product.name);
        }, "Request sent.");
      }}
    >
      <h2 id={`${formId}-h`} ref={heading} tabIndex={-1}>
        Request access to {product.name}
      </h2>
      <p className={styles.muted}>
        Tell Workeasy360 how you will promote it. Your partner profile is used for everything else; do not include anyone
        else&apos;s personal details.
      </p>
      <div className={styles.formGrid}>
        <div>
          <label className="w360-label" htmlFor={`${formId}-channel`}>
            Promotion channel
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
        <div>
          <label className="w360-label" htmlFor={`${formId}-audience`}>
            Target audience
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
      </div>
      <div>
        <label className="w360-label" htmlFor={`${formId}-strategy`}>
          How you will promote it
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
      <div>
        <label className="w360-label" htmlFor={`${formId}-notes`}>
          Anything else (optional)
        </label>
        <textarea id={`${formId}-notes`} className="w360-input" maxLength={1000} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      <div className={styles.actions}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          {busy ? "Sending…" : "Send request"}
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      {feedback}
    </form>
  );
}

// --- My Requests --------------------------------------------------------------------------

function Requests({ data }: { data: StoreConsoleData }) {
  const { base } = usePanel();
  const statusId = useId();
  const [status, setStatus] = useState<"" | PromotionRequest["status"]>("");
  const shown = data.requests.filter((r) => !status || r.status === status);
  if (data.requests.length === 0) {
    return (
      <EmptyState title="No requests yet" icon="store">
        <p className={styles.muted}>
          Find a product in <Link href={`${base}/store-console/explore`}>Explore Products</Link> and request access to promote it.
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
        Approved products are in My Products. Commission you earned before a permission was revoked is kept.
      </p>
    </>
  );
}
