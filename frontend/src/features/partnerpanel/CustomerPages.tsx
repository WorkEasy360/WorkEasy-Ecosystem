"use client";

import Link from "next/link";
import { useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { LoadGate, Status, Table, describeError, money, useLoad, when } from "@/features/controlplane/ui";
import { newKey, partnerApi } from "@/lib/api/controlPlane";
import { panelApi, type PlanRow } from "@/lib/api/partnerPanel";
import { walletApi, type ActivationQuote, type ActivationResult, type CustomerProducts } from "@/lib/api/partnerWallet";
import { EmptyState, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/** A fresh key for one confirmation: a retry of the same review reuses it, so a
 * double click or a lost answer can never charge twice. */

function CustomerSelect({
  value,
  onChange,
  label = "Customer",
}: {
  value: string;
  onChange: (orgId: string, name?: string) => void;
  label?: string;
}) {
  const { id } = usePanel();
  const selectId = useId();
  const [q, setQ] = useState("");
  const [load] = useLoad(() => partnerApi.customers(id, undefined, q), [id, q]);
  return (
    <div className={styles.formRow}>
      <div className="w360-field">
        <label htmlFor={`${selectId}-q`}>Find a customer</label>
        <input id={`${selectId}-q`} className="w360-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name" />
      </div>
      <div className="w360-field">
        <label htmlFor={selectId}>{label}</label>
        <LoadGate load={load} what="your customers">
          {(page) => (
            <select
              id={selectId}
              className="w360-input"
              value={value}
              onChange={(e) => onChange(e.target.value, page.results.find((c) => c.organization_id === e.target.value)?.name)}
            >
              <option value="">Choose a customer</option>
              {page.results
                .filter((c) => c.link_status === "active")
                .map((c) => (
                  <option key={c.organization_id} value={c.organization_id}>
                    {c.name}
                  </option>
                ))}
            </select>
          )}
        </LoadGate>
      </div>
    </div>
  );
}

// --- Products & Setup (the specification's 6.2) -------------------------------------

const SETUP_LABEL: Record<string, string> = {
  pending: "Waiting for the customer",
  submitted: "Submitted - being reviewed",
  correction_required: "Correction requested",
  approved: "Approved",
};

export function ProductsSetup() {
  const params = useSearchParams();
  const [org, setOrg] = useState(params?.get("customer") ?? "");
  return (
    <div className={styles.page}>
      <PageHeader
        title="Products & Setup"
        lead="For one of your customers: the plans it holds and until when, its members, where each product's setup stands, and the product's own links."
      />
      <CustomerSelect value={org} onChange={setOrg} />
      {org ? <CustomerProductsView orgId={org} /> : <p className={styles.muted}>Choose a customer to see its products.</p>}
    </div>
  );
}

function CustomerProductsView({ orgId }: { orgId: string }) {
  const { id, base, can } = usePanel();
  const [load] = useLoad(() => walletApi.customerProducts(id, orgId), [id, orgId]);
  return (
    <LoadGate load={load} what="this customer's products">
      {(view: CustomerProducts) => (
        <>
          {view.members && (
            <p>
              <strong>Members:</strong> {view.members.active ?? view.members.total ?? 0} active
              {view.members.total !== undefined ? ` of ${view.members.total}` : ""}.
            </p>
          )}
          {view.products.length === 0 ? (
            <EmptyState title="No products yet" icon="box">
              <p>This customer holds no plan yet.</p>
              {can("pricing.view") && (
                <p>
                  <Link className="w360-btn w360-btn-primary" href={`${base}/customers/activate?customer=${encodeURIComponent(orgId)}`}>
                    Activate a plan
                  </Link>
                </p>
              )}
            </EmptyState>
          ) : (
            view.products.map((p) => (
              <section key={p.product_code} className={styles.panel} aria-labelledby={`p-${p.product_code}`}>
                <h2 id={`p-${p.product_code}`}>{p.product_name}</h2>
                <Table caption={`${p.product_name} plans`} head={["Plan", "Seats", "Valid from", "Valid until", "Subscription"]} numeric={["Seats"]} isEmpty={p.plans.length === 0} empty="No live plan.">
                  {p.plans.map((plan) => (
                    <tr key={plan.subscription_item_id}>
                      <th scope="row">{plan.plan_name}</th>
                      <td className={styles.num}>{plan.quantity}</td>
                      <td>{when(plan.valid_from)}</td>
                      <td>{when(plan.valid_until)}</td>
                      <td>
                        <Status value={plan.subscription_status} />
                      </td>
                    </tr>
                  ))}
                </Table>
                <h3>Setup</h3>
                {p.setup ? (
                  <>
                    <p>
                      <Status value={p.setup.status} label={SETUP_LABEL[p.setup.status] ?? p.setup.status} />
                      {p.setup.submitted_at && <span className={styles.muted}> Submitted {when(p.setup.submitted_at)}.</span>}
                    </p>
                    {p.setup.decision_reason && <p>Workeasy360 asked: {p.setup.decision_reason}</p>}
                    {p.setup.requirements.length > 0 && (
                      <ul className={styles.list}>
                        {p.setup.requirements.map((r) => (
                          <li key={r.label}>
                            {r.label}
                            {r.required ? "" : " (optional)"}
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <p className={styles.muted}>This product needs no setup.</p>
                )}
                <h3>Links</h3>
                <ul className={styles.list}>
                  {p.website_url && (
                    <li>
                      <a href={p.website_url} target="_blank" rel="noopener noreferrer">
                        Product website
                      </a>
                    </li>
                  )}
                  {p.application_url && (
                    <li>
                      <a href={p.application_url} target="_blank" rel="noopener noreferrer">
                        App sign-in page
                      </a>{" "}
                      <span className={styles.muted}>(your customer signs in with its own account)</span>
                    </li>
                  )}
                  {Object.entries(p.app_links).map(([store, url]) => (
                    <li key={store}>
                      <a href={url} target="_blank" rel="noopener noreferrer">
                        {store === "ios" ? "App Store" : store === "android" ? "Google Play" : store}
                      </a>
                    </li>
                  ))}
                  {!p.website_url && !p.application_url && Object.keys(p.app_links).length === 0 && <li className={styles.muted}>No published links.</li>}
                </ul>
              </section>
            ))
          )}
        </>
      )}
    </LoadGate>
  );
}

// --- Plan Activation (the specification's 6.3) ---------------------------------------

type Step = 1 | 2 | 3 | 4 | 5;

export function PlanActivation() {
  const { id, base, active } = usePanel();
  const params = useSearchParams();
  const [org, setOrg] = useState(params?.get("customer") ?? "");
  const [customerName, setCustomerName] = useState("");
  const [product, setProduct] = useState("");
  const [planId, setPlanId] = useState("");
  const [cycle, setCycle] = useState("monthly");
  const [quantity, setQuantity] = useState(1);
  const [quote, setQuote] = useState<ActivationQuote | null>(null);
  const [result, setResult] = useState<ActivationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef<string>(newKey());
  const [plans] = useLoad(() => panelApi.plans(id), [id]);
  const step: Step = result ? 5 : quote ? 4 : planId ? 3 : product ? 2 : 1;

  const forSale = useMemo(
    () => (plans.status === "ready" ? plans.data.plans.filter((p) => p.enabled_for_sale) : []),
    [plans],
  );
  const products = useMemo(() => {
    const seen = new Map<string, string>();
    for (const p of forSale) seen.set(p.product_code, p.product_name);
    return [...seen.entries()];
  }, [forSale]);
  const plan: PlanRow | undefined = forSale.find((p) => p.plan_version_id === planId);
  const intervals = plan ? plan.prices.map((p) => p.interval) : [];

  function reset(level: "customer" | "product" | "plan") {
    setQuote(null);
    setResult(null);
    setError(null);
    key.current = newKey();
    if (level !== "plan") setPlanId("");
    if (level === "customer") setProduct("");
  }

  async function review() {
    setBusy(true);
    setError(null);
    try {
      setQuote(await walletApi.activationQuote(id, org, { plan_version_id: planId, billing_interval: cycle, quantity }));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!quote || quote.charge_minor === null) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await walletApi.activate(
          id,
          org,
          { plan_version_id: planId, billing_interval: cycle, quantity, expected_charge_minor: quote.charge_minor },
          key.current,
        ),
      );
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Plan Activation"
        lead="Activate a plan for one of your customers and pay for it from your Platform Wallet at your approved partner purchase price. You see the exact amount before you confirm."
      />
      <ol className={styles.steps} aria-label="Steps">
        {["Customer", "Product", "Plan", "Price & wallet", "Done"].map((label, i) => (
          <li key={label} className={styles.step} aria-current={step === i + 1 ? "step" : undefined}>
            <span className={styles.stepNumber}>{i + 1}</span> {label}
          </li>
        ))}
      </ol>

      <section className={styles.panel} aria-labelledby="act-customer">
        <h2 id="act-customer">1. Customer</h2>
        <CustomerSelect
          value={org}
          onChange={(v, name) => {
            setOrg(v);
            setCustomerName(name ?? "");
            reset("customer");
          }}
          label="Assigned customer"
        />
      </section>

      {org && (
        <section className={styles.panel} aria-labelledby="act-product">
          <h2 id="act-product">2. Product and 3. plan</h2>
          <LoadGate load={plans} what="the plans you may sell">
            {() =>
              products.length === 0 ? (
                <p>No product is approved for you to sell yet. Ask Workeasy360 for one under Products & Referrals → Product Store.</p>
              ) : (
                <div className={styles.formRow}>
                  <div className="w360-field">
                    <label htmlFor="act-product-select">Eligible product</label>
                    <select
                      id="act-product-select"
                      className="w360-input"
                      value={product}
                      onChange={(e) => {
                        setProduct(e.target.value);
                        reset("product");
                      }}
                    >
                      <option value="">Choose a product</option>
                      {products.map(([code, name]) => (
                        <option key={code} value={code}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </div>
                  {product && (
                    <div className="w360-field">
                      <label htmlFor="act-plan-select">Available plan</label>
                      <select
                        id="act-plan-select"
                        className="w360-input"
                        value={planId}
                        onChange={(e) => {
                          setPlanId(e.target.value);
                          reset("plan");
                          const chosen = forSale.find((p) => p.plan_version_id === e.target.value);
                          if (chosen && !chosen.prices.some((p) => p.interval === cycle)) setCycle(chosen.prices[0]?.interval ?? "monthly");
                        }}
                      >
                        <option value="">Choose a plan</option>
                        {forSale
                          .filter((p) => p.product_code === product)
                          .map((p) => (
                            <option key={p.plan_version_id} value={p.plan_version_id}>
                              {p.plan_name}
                            </option>
                          ))}
                      </select>
                    </div>
                  )}
                  {plan && (
                    <>
                      <div className="w360-field">
                        <label htmlFor="act-interval">Billing cycle</label>
                        <select
                          id="act-interval"
                          className="w360-input"
                          value={cycle}
                          onChange={(e) => {
                            setCycle(e.target.value);
                            reset("plan");
                          }}
                        >
                          {intervals.map((i) => (
                            <option key={i} value={i}>
                              {i === "annual" || i === "yearly" ? "Yearly" : i.charAt(0).toUpperCase() + i.slice(1)}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="w360-field">
                        <label htmlFor="act-seats">Seats</label>
                        <input
                          id="act-seats"
                          className="w360-input"
                          type="number"
                          min={1}
                          max={10000}
                          value={quantity}
                          onChange={(e) => {
                            setQuantity(Math.max(1, Number.parseInt(e.target.value || "1", 10) || 1));
                            reset("plan");
                          }}
                        />
                      </div>
                    </>
                  )}
                </div>
              )
            }
          </LoadGate>
          {plan && !quote && (
            <p>
              <button type="button" className="w360-btn w360-btn-primary" disabled={busy || !active} onClick={() => void review()}>
                Review price
              </button>
            </p>
          )}
        </section>
      )}

      {quote && !result && (
        <section className={styles.panel} aria-labelledby="act-price">
          <h2 id="act-price">4. Partner purchase price and wallet deduction</h2>
          {!quote.payer.usable ? (
            <p role="alert">{quote.payer.message}</p>
          ) : quote.charge && quote.wallet ? (
            <>
              <dl className={styles.earnings} aria-label="Confirmation summary">
                <div>
                  <dt>Customer</dt>
                  <dd>{customerName || "The customer you chose"}</dd>
                </div>
                <div>
                  <dt>Product</dt>
                  <dd>{products.find(([code]) => code === quote.plan.product_code)?.[1] ?? quote.plan.product_code}</dd>
                </div>
                <div>
                  <dt>Plan</dt>
                  <dd>{quote.plan.name}</dd>
                </div>
                <div>
                  <dt>Partner purchase price</dt>
                  <dd>
                    {money(quote.charge.partner_cost_minor, quote.charge.currency)} per {quote.charge.billing_interval === "monthly" ? "month" : "period"}
                    {quote.charge.quantity > 1 ? ` for ${quote.charge.quantity} seats` : ""}
                  </dd>
                </div>
                {quote.charge.period.prorated && (
                  <div>
                    <dt>For the rest of the current period</dt>
                    <dd>{money(quote.charge.due_now_minor, quote.charge.currency)}</dd>
                  </div>
                )}
                {quote.charge.setup_fee_minor > 0 && (
                  <div>
                    <dt>One-time set-up fee</dt>
                    <dd>{money(quote.charge.setup_fee_minor, quote.charge.currency)}</dd>
                  </div>
                )}
                <div>
                  <dt>Available wallet balance</dt>
                  <dd>{money(quote.wallet.balance_minor, quote.wallet.currency)}</dd>
                </div>
                <div data-strong>
                  <dt>Amount to deduct</dt>
                  <dd>{money(quote.wallet.charge_minor, quote.wallet.currency)}</dd>
                </div>
                <div>
                  <dt>Remaining balance after purchase</dt>
                  <dd>{quote.wallet.sufficient ? money(quote.wallet.balance_after_minor, quote.wallet.currency) : "Not enough"}</dd>
                </div>
              </dl>
              <p className={styles.muted}>
                The plan renews automatically from your wallet at the end of each period, at your purchase price then. Your customer is not invoiced by
                Workeasy360.
              </p>
              {quote.wallet.sufficient ? (
                <p>
                  <button type="button" className="w360-btn w360-btn-primary" disabled={busy || !active} onClick={() => void confirm()}>
                    Confirm and pay {money(quote.wallet.charge_minor, quote.wallet.currency)}
                  </button>{" "}
                  <button type="button" className="w360-btn w360-btn-secondary" onClick={() => reset("plan")}>
                    Change
                  </button>
                </p>
              ) : (
                <p role="alert">
                  Your Platform Wallet does not hold enough for this.{" "}
                  <Link href={`${base}/platform-wallet`} className="w360-btn w360-btn-primary">
                    Add Money
                  </Link>
                </p>
              )}
            </>
          ) : null}
        </section>
      )}

      {result && (
        <section className={styles.panel} aria-labelledby="act-done">
          <h2 id="act-done">5. Activated</h2>
          <p role="status">
            Order {result.order.number} is recorded and the plan is active. Your Platform Wallet now holds{" "}
            {money(result.wallet.balance_minor, result.wallet.currency)}.
          </p>
          <p>
            <Link href={`${base}/customers/setup?customer=${encodeURIComponent(org)}`} className="w360-btn w360-btn-secondary">
              See its products & setup
            </Link>{" "}
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              onClick={() => {
                setOrg("");
                reset("customer");
              }}
            >
              Activate another
            </button>
          </p>
        </section>
      )}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
