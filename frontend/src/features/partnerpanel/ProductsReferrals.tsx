"use client";

import Link from "next/link";
import { useState } from "react";
import { LoadGate, Table, describeError, money, useLoad } from "@/features/controlplane/ui";
import { panelApi, type PlanRow, type StoreProduct } from "@/lib/api/partnerPanel";
import { walletApi, type HelpArticle } from "@/lib/api/partnerWallet";
import { usePortals } from "@/lib/portalContext";
import { portalHref } from "@/lib/portals";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import { earningText } from "./StoreConsole";
import { CopyButton } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * Products & Referrals (ADR-063, the specification's 5): the products this
 * partner may promote or sell, their plans, its purchase prices and commission
 * terms, its referral links (one to the Store), and the material Workeasy360
 * approved for selling. Only products in the partner's own grants are listed.
 */

/** The partner's general referral link, opening the Store instead of sign-up -
 * on the Store's own host when it has one (ADR-065), so the visit is recorded
 * where the visitor will sign up and buy. Only the origin changes: the path is
 * the server's `/r/<code>`. */
export function storeLink(url: string, storeOrigin?: string): string {
  let link = url;
  if (storeOrigin) {
    try {
      const parsed = new URL(url);
      link = `${storeOrigin}${parsed.pathname}${parsed.search}`;
    } catch {
      link = url;
    }
  }
  return `${link}${link.includes("?") ? "&" : "?"}to=store`;
}

export function StoreReferralLink() {
  const { id, can } = usePanel();
  const origins = usePortals();
  const [load, reload] = useLoad(() => panelApi.affiliateOverview(id, "30d"), [id]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!can("referral.view")) return null;
  return (
    <section className={styles.panel} aria-labelledby="store-link">
      <h2 id="store-link">Store referral link</h2>
      <p className={styles.muted}>
        Opens the Workeasy360 Store. Anyone who signs up from it is attributed to you, under the same rules as your other links.
      </p>
      <LoadGate load={load} what="your links">
        {(overview) => {
          const general = overview.links.find((l) => !l.product_code && l.url && l.status === "active");
          if (general?.url) {
            const url = storeLink(general.url, origins?.store || undefined);
            return (
              <div className={styles.linkRow}>
                <input className="w360-input" readOnly value={url} aria-label="Store referral link" onFocus={(e) => e.currentTarget.select()} />
                <CopyButton text={url} label="Store referral link" />
              </div>
            );
          }
          return can("referral.create") ? (
            <p>
              <button
                type="button"
                className="w360-btn w360-btn-primary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    await panelApi.createTrackingLink(id, { label: "Store link", link_kind: "general" });
                    reload();
                  } catch (err) {
                    setError(describeError(err));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Create my Store link
              </button>
              {error && (
                <span className="w360-error-text" role="alert">
                  {" "}
                  {error}
                </span>
              )}
            </p>
          ) : (
            <p className={styles.muted}>No general link yet. A partner administrator can create one.</p>
          );
        }}
      </LoadGate>
    </section>
  );
}

export function EligibleProducts() {
  const { can } = usePanel();
  const promotes = can("referral.view");
  const sells = can("pricing.view");
  return (
    <div className={styles.page}>
      <PageHeader
        title="Eligible Products"
        lead="The products approved for you, their plans and what you earn or pay. Ask for more under Product Store."
      />
      <StoreReferralLink />
      {promotes && <PromotedProducts />}
      {sells && <SoldProducts />}
    </div>
  );
}

function PromotedProducts() {
  const { id, can } = usePanel();
  const portals = usePortals();
  const [load] = useLoad(() => panelApi.storeConsole(id, "promote"), [id]);
  const [links, setLinks] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  async function generate(p: StoreProduct) {
    setError(null);
    try {
      const made = await panelApi.createTrackingLink(id, { label: p.name, product_code: p.code, link_kind: "product" });
      setLinks((all) => ({ ...all, [p.code]: made.url }));
    } catch (err) {
      setError(describeError(err));
    }
  }
  return (
    <section className={styles.panel} aria-labelledby="promote-products">
      <h2 id="promote-products">Products you may promote</h2>
      <LoadGate load={load} what="the products you may promote">
        {(console) => {
          const mine = console.products.filter((p) => p.status === "approved");
          if (mine.length === 0) {
            return (
              <EmptyState title="No product approved for promotion yet" icon="gift">
                <p>Ask to promote a product from the Product Store tab.</p>
              </EmptyState>
            );
          }
          return (
            <ul className={styles.list}>
              {mine.map((p) => (
                <li key={p.code} className={styles.listItem}>
                  <h3>{p.name}</h3>
                  <p>{p.summary || p.tagline}</p>
                  <p className={styles.muted}>
                    Plans: {p.plans.map((plan) => plan.plan_name).join(", ") || "none listed"}
                    {p.starting_price && ` · from ${money(p.starting_price.amount_minor, p.starting_price.currency)}`}
                  </p>
                  {earningText(p) && <p>{earningText(p)}</p>}
                  <p className={styles.actionsRow}>
                    <a className="w360-btn w360-btn-secondary" href={portalHref(portals, portals?.store ? "store" : "app", `/store/product/${encodeURIComponent(p.slug)}`)}>
                      View product
                    </a>
                    {can("referral.create") && (
                      <button type="button" className="w360-btn w360-btn-primary" onClick={() => void generate(p)}>
                        Generate referral link
                      </button>
                    )}
                  </p>
                  {links[p.code] && (
                    <div className={styles.linkRow}>
                      <input className="w360-input" readOnly value={links[p.code]} aria-label={`Referral link for ${p.name}`} />
                      <CopyButton text={links[p.code]} label={`Referral link for ${p.name}`} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          );
        }}
      </LoadGate>
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function SoldProducts() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.plans(id), [id]);
  return (
    <section className={styles.panel} aria-labelledby="sell-products">
      <h2 id="sell-products">Products you may sell</h2>
      <LoadGate load={load} what="the plans you may sell">
        {(data) => {
          const forSale = data.plans.filter((p) => p.enabled_for_sale);
          if (forSale.length === 0) {
            return (
              <EmptyState title="No product approved for selling yet" icon="store">
                <p>Ask to sell a product from the Product Store tab.</p>
              </EmptyState>
            );
          }
          const byProduct = new Map<string, PlanRow[]>();
          for (const plan of forSale) byProduct.set(plan.product_name, [...(byProduct.get(plan.product_name) ?? []), plan]);
          return (
            <>
              {[...byProduct.entries()].map(([name, plans]) => (
                <div key={name}>
                  <h3>{name}</h3>
                  <Table
                    caption={`${name} plans and your purchase price`}
                    head={["Plan", "Billing", "List price", "Your purchase price", "Your customer pays"]}
                    numeric={["List price", "Your purchase price", "Your customer pays"]}
                  >
                    {plans.flatMap((plan) =>
                      plan.prices.map((price) => (
                        <tr key={`${plan.plan_version_id}-${price.interval}`}>
                          <th scope="row">{plan.plan_name}</th>
                          <td>{price.interval}</td>
                          <td className={styles.num}>{price.list_price_minor != null ? money(price.list_price_minor, plan.currency) : "—"}</td>
                          <td className={styles.num}>
                            {price.partner_cost_minor != null ? money(price.partner_cost_minor, plan.currency) : "Not approved yet"}
                          </td>
                          <td className={styles.num}>{price.customer_price_minor != null ? money(price.customer_price_minor, plan.currency) : "—"}</td>
                        </tr>
                      )),
                    )}
                  </Table>
                </div>
              ))}
              <p>
                <Link className="w360-btn w360-btn-primary" href={`${base}/customers/activate`}>
                  Activate a plan for a customer
                </Link>{" "}
                <Link className="w360-btn w360-btn-secondary" href={`${base}/plans`}>
                  Pricing & Margins
                </Link>
              </p>
            </>
          );
        }}
      </LoadGate>
    </section>
  );
}

// --- Sales Resources (the specification's 5.3) -----------------------------------

export function SalesResources() {
  const { id } = usePanel();
  const [load] = useLoad(() => walletApi.salesResources(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Sales Resources"
        lead="Material Workeasy360 approved for the products you promote or sell: images, product videos, descriptions and sales guides."
      />
      <LoadGate load={load} what="your sales resources">
        {(data) =>
          data.products.length === 0 && data.guides.length === 0 ? (
            <EmptyState title="No sales resources yet" icon="gift">
              <p>Material appears here for each product approved for you, as soon as Workeasy360 publishes it.</p>
            </EmptyState>
          ) : (
            <>
              {data.guides.length > 0 && (
                <section className={styles.panel} aria-labelledby="general-guides">
                  <h2 id="general-guides">Sales guides</h2>
                  <Articles items={data.guides} />
                </section>
              )}
              {data.products.map((p) => (
                <section key={p.product_code} className={styles.panel} aria-labelledby={`res-${p.product_code}`}>
                  <h2 id={`res-${p.product_code}`}>{p.name}</h2>
                  {p.summary && <p>{p.summary}</p>}
                  {p.description && (
                    <p className={styles.actionsRow}>
                      <CopyButton text={p.description} label={`${p.name} description`} />
                      <span className={styles.muted}>Copy the approved description</span>
                    </p>
                  )}
                  {p.images.length > 0 && (
                    <ul className={styles.mediaRow} aria-label={`${p.name} images`}>
                      {p.images.map((m) => (
                        <li key={m.id}>
                          <a href={m.url} target="_blank" rel="noopener noreferrer" download>
                            {/* eslint-disable-next-line @next/next/no-img-element -- a served listing image, sized by CSS */}
                            <img className={styles.mediaThumb} src={m.url} alt={m.caption || `${p.name} ${m.kind}`} />
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                  {p.video && (p.video.url || p.video.file_url) && (
                    <p>
                      <a href={String(p.video.url ?? p.video.file_url)} target="_blank" rel="noopener noreferrer">
                        Watch the product video
                      </a>
                    </p>
                  )}
                  {p.documentation_url && (
                    <p>
                      <a href={p.documentation_url} target="_blank" rel="noopener noreferrer">
                        Product documentation
                      </a>
                    </p>
                  )}
                  {p.guides.length > 0 && (
                    <>
                      <h3>Guides for {p.name}</h3>
                      <Articles items={p.guides} />
                    </>
                  )}
                </section>
              ))}
            </>
          )
        }
      </LoadGate>
    </div>
  );
}

function Articles({ items }: { items: HelpArticle[] }) {
  return (
    <ul className={styles.list}>
      {items.map((a) => (
        <li key={a.slug} className={styles.listItem}>
          <details>
            <summary>{a.title}</summary>
            <p className={styles.preWrap}>{a.body}</p>
          </details>
        </li>
      ))}
    </ul>
  );
}

// --- Help Content (the specification's 10.2) ----------------------------------------

const KINDS = [
  { value: "", label: "All" },
  { value: "faq", label: "FAQs" },
  { value: "setup_guide", label: "Setup instructions" },
  { value: "sales_guide", label: "Product & sales guides" },
];

export function HelpContent() {
  const { id, base } = usePanel();
  const [kind, setKind] = useState("");
  const [load] = useLoad(() => walletApi.helpArticles(id, kind || undefined), [id, kind]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Help Content"
        lead="Partner FAQs, product guides and setup instructions published by Workeasy360."
        actions={
          <Link href={`${base}/support-manager?new=1`} className="w360-btn w360-btn-primary">
            Contact Support
          </Link>
        }
      />
      <div className={styles.tabs} role="group" aria-label="Kind of help">
        {KINDS.map((k) => (
          <button key={k.value || "all"} type="button" className="w360-btn w360-btn-secondary" aria-pressed={kind === k.value} onClick={() => setKind(k.value)}>
            {k.label}
          </button>
        ))}
      </div>
      <LoadGate load={load} what="help content">
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title="Nothing published here yet" icon="help">
              <p>Raise a ticket and Workeasy360 support will help.</p>
            </EmptyState>
          ) : (
            <Articles items={data.items} />
          )
        }
      </LoadGate>
    </div>
  );
}
