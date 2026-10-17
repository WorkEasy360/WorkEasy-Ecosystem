"use client";

import Link from "next/link";
import { useState } from "react";
import { LoadGate, MoreButton, Status, Table, money, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { partnerApi } from "@/lib/api/controlPlane";
import { panelApi, type ManagedProduct } from "@/lib/api/partnerPanel";
import { PartnerCommissions } from "@/features/controlplane/PartnerScreens";
import { NotAvailable, PageHeader, SubNav, UsersNav, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

const INTERVALS: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Every 6 months",
  annual: "Yearly",
  one_time: "One time",
};

function firstPrice(p: ManagedProduct, currency: string): string {
  for (const plan of p.plans) {
    for (const i of plan.intervals) {
      const minor = i.partner_price_minor ?? i.list_price_minor;
      if (minor != null) return `${money(minor, currency)} ${INTERVALS[i.interval]?.toLowerCase() ?? i.interval}`;
    }
  }
  return "Not priced";
}

// --- partner products: what Workeasy360 has granted ---------------------------------

/** The products granted to this partner, and the catalog beyond them. Read-only:
 * a partner can never grant a product to itself (Workeasy360 does, in the Super
 * Admin console), and what it does with a granted product is done where that
 * belongs - the sale switch in Product Billing, the customer-facing order and
 * visibility in Product Manager. */
export function PartnerProducts() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.products(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Partner Products"
        lead="The products Workeasy360 has granted to your account. New products appear here as soon as they are granted; you cannot grant a product to yourself."
      />
      <LoadGate load={load} what="your products">
        {(d) => (
          <>
            <Table
              caption="Products granted to your account"
              head={["Product", "Access", "Sell access", "Pricing", "Trial", "Customers"]}
              isEmpty={d.products.length === 0}
              empty="Workeasy360 has not granted you a product yet. Contact us to set up your programme."
            >
              {d.products.map((p) => (
                <tr key={p.code}>
                  <th scope="row">
                    {p.name}
                    <div className={styles.muted}>{p.tagline || p.category}</div>
                  </th>
                  <td>
                    <Status value="granted" />
                  </td>
                  <td>
                    <Status value={p.enabled_for_sale ? "enabled" : "disabled"} />
                  </td>
                  <td>{firstPrice(p, d.currency)}</td>
                  <td>{!p.trial.offered_by_platform ? "None" : p.trial.allowed_for_partner ? `${p.trial.days} days` : "Off for your customers"}</td>
                  <td className={styles.num}>{p.customers ?? "—"}</td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>
              Turn a product on or off for new sales in <Link href={`${base}/product-billing`}>Product Billing</Link>; arrange what customers see in{" "}
              <Link href={`${base}/product-manager`}>Product Manager</Link>.
            </p>
            {d.available.length > 0 && (
              <section className={styles.panel} aria-labelledby="avail">
                <h2 id="avail">Available from Workeasy360</h2>
                <p className={styles.muted}>Products in the catalog that your account is not yet approved to sell. Ask Workeasy360 to grant one.</p>
                <ul className={styles.list}>
                  {d.available.map((a) => (
                    <li key={a.code} className={styles.listItem}>
                      <strong>{a.name}</strong>
                      <span className={styles.muted}>{a.tagline}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- product manager: order and customer menu ------------------------------------------

export function ProductManager() {
  const { id, can, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.products(id), [id]);
  const { act, busy, feedback } = useAction(reload);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Product Manager"
        lead="Choose the order your customers see products in, and which appear on their My Products and Explore pages. This changes what is shown, never what anyone can use."
      />
      {feedback}
      <LoadGate load={load} what="your products">
        {(d) => (
          <Arrange
            key={d.products.map((p) => p.code).join()}
            products={d.products}
            editable={d.can_manage && can("product.manage") && active}
            busy={busy}
            onSave={(order) => void act(() => panelApi.orderProducts(id, order), "The order was saved.")}
            onToggle={(code, body) => void act(() => panelApi.updateProduct(id, code, body), "Saved.")}
          />
        )}
      </LoadGate>
    </div>
  );
}

function Arrange({
  products,
  editable,
  busy,
  onSave,
  onToggle,
}: {
  products: ManagedProduct[];
  editable: boolean;
  busy: boolean;
  onSave: (order: string[]) => void;
  onToggle: (code: string, body: { show_in_explore?: boolean; show_in_my_products?: boolean }) => void;
}) {
  const [order, setOrder] = useState(products.map((p) => p.code));
  const byCode = new Map(products.map((p) => [p.code, p]));
  const move = (index: number, delta: number) =>
    setOrder((o) => {
      const next = [...o];
      const swap = index + delta;
      if (swap < 0 || swap >= next.length) return o;
      [next[index], next[swap]] = [next[swap]!, next[index]!];
      return next;
    });
  const changed = order.join() !== products.map((p) => p.code).join();
  return (
    <>
      <Table caption="Products in the order customers see them" head={["Order", "Product", "Explore", "My Products", "Move"]} isEmpty={products.length === 0} empty="No products to arrange.">
        {order.map((code, i) => {
          const p = byCode.get(code)!;
          return (
            <tr key={code}>
              <td className={styles.num}>{i + 1}</td>
              <th scope="row">{p.name}</th>
              <td>
                <label>
                  <span className="w360-visually-hidden">Show {p.name} on Explore Products</span>
                  <input type="checkbox" checked={p.show_in_explore} disabled={!editable || busy} onChange={(e) => onToggle(code, { show_in_explore: e.target.checked })} />
                </label>
              </td>
              <td>
                <label>
                  <span className="w360-visually-hidden">Show {p.name} on My Products</span>
                  <input type="checkbox" checked={p.show_in_my_products} disabled={!editable || busy} onChange={(e) => onToggle(code, { show_in_my_products: e.target.checked })} />
                </label>
              </td>
              <td>
                {editable && (
                  <div className={styles.actions}>
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${p.name} up`}>
                      Up
                    </button>
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={i === order.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${p.name} down`}>
                      Down
                    </button>
                  </div>
                )}
              </td>
            </tr>
          );
        })}
      </Table>
      {editable && (
        <div>
          <button type="button" className="w360-btn w360-btn-primary" disabled={busy || !changed} onClick={() => onSave(order)}>
            Save order
          </button>
        </div>
      )}
      <section className={styles.panel} aria-labelledby="catalog-preview">
        <h2 id="catalog-preview">Customer preview</h2>
        <p className={styles.muted}>The catalog as your customers will see it, in this order. It reflects the switches above before you save the order.</p>
        <div className={styles.twoCol}>
          <div>
            <h3>Explore Products</h3>
            <PreviewList
              label="Explore Products preview"
              items={order.map((c) => byCode.get(c)!).filter((p) => p.enabled_for_sale && p.show_in_explore)}
              empty="Nothing is offered on Explore."
            />
          </div>
          <div>
            <h3>My Products</h3>
            <PreviewList
              label="My Products preview"
              items={order.map((c) => byCode.get(c)!).filter((p) => p.show_in_my_products)}
              empty="Nothing is listed on My Products."
            />
          </div>
        </div>
      </section>
      <p className={styles.muted}>A customer always sees the products it already holds, wherever they sit in this order. Only white-label customers see your arrangement.</p>
    </>
  );
}

function PreviewList({ label, items, empty }: { label: string; items: ManagedProduct[]; empty: string }) {
  if (items.length === 0) return <p className={styles.muted}>{empty}</p>;
  return (
    <ol className={styles.list} aria-label={label}>
      {items.map((p) => (
        <li key={p.code} className={styles.listItem}>
          <strong>{p.name}</strong>
          <span className={styles.muted}>{p.tagline || p.category}</span>
        </li>
      ))}
    </ol>
  );
}

// --- plans ------------------------------------------------------------------------------------

function PlansNav() {
  const { base } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/plans`, label: "Plans" },
        { href: `${base}/plans/add-ons`, label: "Add-ons" },
      ]}
    />
  );
}

export function Plans() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.plans(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Plans & Add-ons" lead="The plans you may sell, at the price your customers pay. Workeasy360 sets the plans and your prices." />
      <PlansNav />
      <LoadGate load={load} what="your plans">
        {(d) => (
          <>
            <Table
              caption="Plans you can sell"
              head={["Plan", "Product", "Billing", "Your price", "List price", "Seats", "Trial", "For sale"]}
              isEmpty={d.plans.length === 0}
              empty="Nothing has been approved for your account yet."
            >
              {d.plans.flatMap((p) =>
                (p.prices.length ? p.prices : [null]).map((price, n) => (
                  <tr key={`${p.plan_version_id}-${price?.interval ?? n}`}>
                    <th scope="row">{p.plan_name}</th>
                    <td>{p.product_name}</td>
                    <td>{price ? (INTERVALS[price.interval] ?? price.interval) : "—"}</td>
                    <td className={styles.num}>{price ? (price.partner_price_minor != null ? money(price.partner_price_minor, p.currency) : price.list_price_minor != null ? `${money(price.list_price_minor, p.currency)} (list)` : "—") : "Not priced"}</td>
                    <td className={styles.num}>{price?.list_price_minor != null ? money(price.list_price_minor, p.currency) : "—"}</td>
                    <td className={styles.num}>{p.included_seats}</td>
                    <td>{p.trial_days ? `${p.trial_days} days` : "—"}</td>
                    <td>
                      <Status value={p.enabled_for_sale ? "enabled" : "disabled"} />
                    </td>
                  </tr>
                )),
              )}
            </Table>
            <NotAvailable title="Creating plans" reasons={["You cannot create a global plan. Workeasy360 creates plans and sets your prices; you see and sell the ones approved for your account."]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function AddOns() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.plans(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Plans & Add-ons" lead="Extras that can be bought on top of a plan." />
      <PlansNav />
      <LoadGate load={load} what="add-ons">
        {(d) => <NotAvailable title="No add-ons yet" reasons={[d.not_implemented.add_ons ?? ""]} />}
      </LoadGate>
    </div>
  );
}

// --- pricing ---------------------------------------------------------------------------------

function PricingNav() {
  const { base } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/pricing/currency`, label: "Currency" },
        { href: `${base}/pricing/packages`, label: "Package & Pricing" },
        { href: `${base}/pricing/rules`, label: "Pricing Rule" },
      ]}
    />
  );
}

export function PricingPackages() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.packages(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Currency & Pricing" lead="Packages that bundle products. A package is a way to sell plans together; it never grants access by itself." />
      <PricingNav />
      <LoadGate load={load} what="packages">
        {(d) => (
          <>
            {d.packages.length === 0 ? (
              <p className={styles.muted}>No packages are available for the products you hold.</p>
            ) : (
              <ul className={styles.cards}>
                {d.packages.map((p) => (
                  <li key={p.code} className={styles.card}>
                    <h2 className={styles.cardTitle}>{p.name}</h2>
                    <p className={styles.muted}>{p.caption}</p>
                    <ul className={styles.list}>
                      {p.items.map((i) => (
                        <li key={`${i.product_name}-${i.plan_name}`} className={styles.listItem}>
                          <span>{i.product_name}</span>
                          <span className={styles.muted}>
                            {i.plan_name} × {i.quantity}
                            {i.is_optional ? " (optional)" : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
            <NotAvailable title="Your own packages" reasons={[d.not_implemented.partner_packages ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function PricingRules() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.priceBook(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Currency & Pricing" lead="The fixed prices Workeasy360 has set for your account. The server works out what a customer pays; the browser never does." />
      <PricingNav />
      <LoadGate load={load} what="your prices">
        {(d) => (
          <>
            <Table caption="Your fixed prices" head={["Product", "Plan", "Billing", "Price", "Since"]} isEmpty={d.rules.length === 0} empty="You have no prices of your own; the public list price applies.">
              {d.rules.map((r) => (
                <tr key={`${r.plan_code}-${r.interval}`}>
                  <th scope="row">{r.product_name}</th>
                  <td>{r.plan_name}</td>
                  <td>{INTERVALS[r.interval] ?? r.interval}</td>
                  <td className={styles.num}>{money(r.amount_minor, r.currency)}</td>
                  <td>{when(r.effective_from)}</td>
                </tr>
              ))}
            </Table>
            <NotAvailable title="Discounts and customer groups" reasons={[d.not_implemented.pricing_rules ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function Currency() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.currencies(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Currency & Pricing" lead="The currencies Workeasy360 supports. Your payout currency is fixed when your account is created; amounts are never converted between currencies." />
      <PricingNav />
      <LoadGate load={load} what="currencies">
        {(d) => (
          <>
            <Table caption="Supported currencies" head={["Currency", "Decimal places", "Your default", "You have prices", "Listed publicly"]}>
              {d.supported.map((c) => (
                <tr key={c.code}>
                  <th scope="row">{c.code}</th>
                  <td className={styles.num}>{c.exponent}</td>
                  <td>{c.is_default ? "Yes" : "—"}</td>
                  <td>{c.partner_priced ? "Yes" : "—"}</td>
                  <td>{c.listed_publicly ? "Yes" : "—"}</td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>Only the currencies in this list can be used. There is no exchange-rate service: two currencies are shown side by side, never added together.</p>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- product billing -----------------------------------------------------------------------

export function ProductBilling() {
  const { id, can, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.products(id), [id]);
  const { act, busy, feedback } = useAction(reload);
  return (
    <div className={styles.page}>
      <PageHeader title="Product billing" lead="What each product costs, who has it, and whether you are selling it. You can only sell a product Workeasy360 has granted you." />
      {feedback}
      <LoadGate load={load} what="product billing">
        {(d) => (
          <Table
            caption="Product billing"
            head={["Product", "For sale", "Your price", "List price", "Trial", "Customers", "Subscriptions", "Actions"]}
            isEmpty={d.products.length === 0}
            empty="No products have been granted to your account."
          >
            {d.products.map((p) => {
              const plan = p.plans.find((x) => x.intervals.length > 0);
              const interval = plan?.intervals[0];
              return (
                <tr key={p.code}>
                  <th scope="row">{p.name}</th>
                  <td>
                    <Status value={p.enabled_for_sale ? "enabled" : "disabled"} />
                  </td>
                  <td className={styles.num}>{interval?.partner_price_minor != null ? money(interval.partner_price_minor, d.currency) : "—"}</td>
                  <td className={styles.num}>{interval?.list_price_minor != null ? money(interval.list_price_minor, d.currency) : "—"}</td>
                  <td>{p.trial.offered_by_platform ? (p.trial.allowed_for_partner ? `${p.trial.days} days` : "Off for you") : "None"}</td>
                  <td className={styles.num}>{p.customers ?? "—"}</td>
                  <td className={styles.num}>{p.active_subscriptions ?? "—"}</td>
                  <td>
                    {can("product.manage") && active && (
                      <button
                        type="button"
                        className="w360-btn w360-btn-secondary"
                        disabled={busy}
                        onClick={() =>
                          void act(
                            () => panelApi.updateProduct(id, p.code, { enabled_for_sale: !p.enabled_for_sale }),
                            p.enabled_for_sale ? `${p.name} is off for new sales.` : `${p.name} is on sale.`,
                          )
                        }
                      >
                        {p.enabled_for_sale ? "Disable for new sales" : "Enable for sale"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </LoadGate>
    </div>
  );
}

// --- subscriptions, orders, balance, payment links ----------------------------------------------

export function Subscriptions() {
  const { id, base } = usePanel();
  const paged = usePaged((cursor) => partnerApi.customers(id, cursor), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Users" lead="Where each of your customers stands. To sell or change a plan, open the customer." />
      <UsersNav />
      {paged.state === "loading" && <p aria-live="polite">Loading subscriptions…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to subscriptions.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load subscriptions.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Customer subscriptions" head={["Customer", "Subscription", "Access", "Products", "Since"]} isEmpty={paged.rows.length === 0} empty="No customers yet.">
            {paged.rows
              .filter((c) => c.link_status !== "ended")
              .map((c) => (
                <tr key={c.organization_id}>
                  <th scope="row">{c.name}</th>
                  <td>
                    <Status value={c.subscription_status} />
                  </td>
                  <td>{c.access_class === "trial" ? "Trial" : c.access_class === "customer" ? "Paid" : "—"}</td>
                  <td>{c.products.join(", ") || "—"}</td>
                  <td>{when(c.started_at)}</td>
                </tr>
              ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
          <Link href={`${base}/users`}>Manage customers</Link>
        </>
      )}
    </div>
  );
}

function PaymentsNav() {
  const { base, can } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/payments`, label: "Orders", show: can("customer.view") },
        { href: `${base}/payments/commissions`, label: "Commissions", show: can("commission.view") },
        { href: `${base}/payments/balance`, label: "Balance", show: can("commission.view") },
      ]}
    />
  );
}

export function Payments() {
  const { id } = usePanel();
  const paged = usePaged((cursor) => panelApi.orders(id, cursor), [id]);
  const [features] = useLoad(() => panelApi.features(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Payments" lead="Orders placed by your customers. Amounts are what the platform recorded; a paid order is confirmed by the payment provider, never by a browser." />
      <PaymentsNav />
      {paged.state === "loading" && <p aria-live="polite">Loading orders…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to orders.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load orders.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Customer orders" head={["Order", "Customer", "Description", "Status", "Amount", "Placed", "Paid"]} isEmpty={paged.rows.length === 0} empty="No orders yet.">
            {paged.rows.map((o) => (
              <tr key={o.id}>
                <th scope="row" className={styles.mono}>
                  {o.number}
                </th>
                <td>{o.customer || <span className={styles.muted}>Former customer</span>}</td>
                <td>{o.description}</td>
                <td>
                  <Status value={o.status} />
                </td>
                <td className={styles.num}>{money(o.amount_due_minor, o.currency)}</td>
                <td>{when(o.created_at)}</td>
                <td>{when(o.paid_at)}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
      <LoadGate load={features} what="what is available">
        {(f) => (
          <NotAvailable
            title="Not offered"
            reasons={[
              f.not_implemented.recharge ?? "",
              f.not_implemented.payment_links ?? "",
              "No card, CVV or bank details are collected or stored on this platform; payments go through the provider's own checkout.",
            ].filter(Boolean)}
          />
        )}
      </LoadGate>
    </div>
  );
}

export function PaymentCommissions() {
  return (
    <div className={styles.page}>
      <PaymentsNav />
      <PartnerCommissions />
    </div>
  );
}

export function Balance() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.balance(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Payments" lead="Your balance is your commission ledger: entries are only ever added, never edited, and the balance is their sum." />
      <PaymentsNav />
      <LoadGate load={load} what="your balance">
        {(d) => (
          <>
            <Table caption="Balance by currency" head={["Currency", "Balance", "Pending (holding)", "Available", "In a payout", "Paid", "Entries"]} isEmpty={d.balances.length === 0} empty="No commission has been recorded yet.">
              {d.balances.map((b) => (
                <tr key={b.currency}>
                  <th scope="row">{b.currency}</th>
                  <td className={styles.num}>{money(b.balance_minor, b.currency)}</td>
                  <td className={styles.num}>{money(b.pending_minor, b.currency)}</td>
                  <td className={styles.num}>{money(b.available_minor, b.currency)}</td>
                  <td className={styles.num}>{money(b.in_payouts_minor, b.currency)}</td>
                  <td className={styles.num}>{money(b.paid_minor, b.currency)}</td>
                  <td className={styles.num}>{b.entries}</td>
                </tr>
              ))}
            </Table>
            <Table caption="Latest ledger entries" head={["When", "Kind", "Amount", "Clears"]} isEmpty={d.entries.length === 0} empty="No entries yet.">
              {d.entries.map((e) => (
                <tr key={e.id}>
                  <td>{when(e.event_at)}</td>
                  <td>{e.kind}</td>
                  <td className={styles.num}>{money(e.amount_minor, e.currency)}</td>
                  <td>{when(e.clears_at)}</td>
                </tr>
              ))}
            </Table>
            <NotAvailable title="Recharging" reasons={[d.not_implemented.recharge ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}
