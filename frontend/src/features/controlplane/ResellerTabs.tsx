"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi, type PartnerDetail } from "@/lib/api/controlPlane";
import {
  partnerConsoleApi,
  type PartnerPricingRow,
  type PartnerSummaryView,
  type SetupStep,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { CommissionSummary, ExperienceLabels } from "./PartnerConsole";
import { CustomerTable, StaffOwnerActions } from "./PartnerCustomers";
import { PayoutRowView } from "./AdminMoney";
import { LoadGate, MoreButton, ReasonAction, Status, Table, money, useAction, useLoad, usePaged, when } from "./ui";
import { formatBasisPoints, toBasisPoints, toMinor } from "./amounts";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The panels of the reseller detail (Phase 10c). Each tab reads through the
 * console's own read models and changes anything only through the *existing*
 * commands: capabilities, product grants, prices, commission, members, domains
 * and payouts each keep the one implementation they always had. Subscriptions
 * are shown here and changed where they always were (the customer's page), and
 * nothing in this file writes a subscription or an entitlement.
 */

export interface TabProps {
  partnerId: string;
  partner: PartnerDetail;
  /** Bumped after any change, so panels re-read. */
  rev: number;
  bump: () => void;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={id}>
      <h3 id={id} className={styles.cardTitle}>
        {title}
      </h3>
      {children}
    </section>
  );
}

// --- overview ---------------------------------------------------------------------------------

const STEP_TEXT: Record<SetupStep["state"], string> = {
  complete: "Complete",
  incomplete: "Needs attention",
  pending: "Waiting",
  optional: "Optional",
};

const STEP_BADGE: Record<SetupStep["state"], string> = {
  complete: "w360-badge w360-badge-success",
  incomplete: "w360-badge w360-badge-warning",
  pending: "w360-badge w360-badge-neutral",
  optional: "w360-badge w360-badge-neutral",
};

export function OverviewTab({
  partnerId,
  partner,
  rev,
  onOpen,
}: TabProps & { onOpen: (tab: "customers" | "products" | "commissions" | "payouts" | "branding" | "domains" | "users") => void }) {
  const [load] = useLoad(() => partnerConsoleApi.summary(partnerId), [partnerId, rev]);
  return (
    <LoadGate load={load} what="the overview">
      {(d) => <OverviewBody summary={d} partner={partner} onOpen={onOpen} />}
    </LoadGate>
  );
}

function OverviewBody({
  summary: d,
  partner,
  onOpen,
}: {
  summary: PartnerSummaryView;
  partner: PartnerDetail;
  onOpen: (tab: "customers" | "products" | "commissions" | "payouts" | "branding" | "domains" | "users") => void;
}) {
  return (
    <div className={styles.stack}>
      <Section title="Setup progress">
        <ul className={local.checks}>
          {d.setup_progress.map((s) => (
            <li key={s.key} className={local.progressItem}>
              <span className={STEP_BADGE[s.state]}>{STEP_TEXT[s.state]}</span>
              <strong>{s.label}</strong>
              {s.detail && <span className={styles.muted}>{s.detail}</span>}
            </li>
          ))}
        </ul>
      </Section>
      <div className={local.split}>
        <Section title="Partner">
          <dl className={local.review}>
            <div>
              <dt>Status</dt>
              <dd>
                <Status value={d.partner.status} />
                {d.partner.status_reason && <span className={styles.muted}> {d.partner.status_reason}</span>}
              </dd>
            </div>
            <div>
              <dt>Contact</dt>
              <dd>{[partner.contact_name, partner.contact_email].filter(Boolean).join(" · ") || "None recorded"}</dd>
            </div>
            <div>
              <dt>Legal name</dt>
              <dd>{partner.legal_name || "—"}</dd>
            </div>
            <div>
              <dt>Paid in</dt>
              <dd>{d.partner.payout_currency}</dd>
            </div>
            <div>
              <dt>Capabilities</dt>
              <dd>
                <ExperienceLabels labels={d.experience} />
                <div className={styles.muted}>
                  {d.capabilities.length === 0 ? "None granted." : d.capabilities.map((c) => c.label).join("; ")}
                </div>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("users")}>
                  Manage capabilities
                </button>
              </dd>
            </div>
          </dl>
        </Section>
        <Section title="Customers">
          <dl className={local.review}>
            <div>
              <dt>Live relationships</dt>
              <dd>
                {d.customers.live} ({d.customers.active} active, {d.customers.suspended} suspended) · {d.customers.ended} ended
              </dd>
            </div>
            <div>
              <dt>Owner activation</dt>
              <dd>
                {d.customers.owner_activation_pending === 0
                  ? "Every owner has activated, or has nothing to activate."
                  : `${d.customers.owner_activation_pending} awaiting their owner.`}
              </dd>
            </div>
            <div>
              <dt>Active subscriptions</dt>
              <dd>{d.active_subscriptions}</dd>
            </div>
          </dl>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("customers")}>
            Open customers
          </button>
        </Section>
        <Section title="Products">
          {d.products.length === 0 ? (
            <p className={styles.muted}>None: this partner cannot sell anything.</p>
          ) : (
            <ul className={local.checks}>
              {d.products.map((p) => (
                <li key={p.grant_id}>
                  {p.product_name}
                  {p.plan_code && <span className={styles.muted}> ({p.plan_code} only)</span>}
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("products")}>
            Products &amp; pricing
          </button>
        </Section>
        <Section title="Commission">
          <p>
            {d.commission_rule
              ? `Earns ${formatBasisPoints(d.commission_rule.rate_bp)} (${d.commission_rule.scope.replace(/_/g, " ")}).`
              : "No commission rule applies: it earns nothing."}
          </p>
          <CommissionSummary rows={d.commission} />
          <div>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("commissions")}>
              Open commissions
            </button>
          </div>
        </Section>
        <Section title="Payouts">
          <p>
            {d.payouts.open} open.{" "}
            {d.payouts.latest
              ? `Latest: ${d.payouts.latest.status}, ${money(d.payouts.latest.payable_minor, d.payouts.latest.currency)} for the period to ${d.payouts.latest.period_end}.`
              : "None yet."}
          </p>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("payouts")}>
            Open payouts
          </button>
        </Section>
        <Section title="White-label">
          <dl className={local.review}>
            <div>
              <dt>Enabled</dt>
              <dd>{d.white_label.enabled ? "Yes" : "No"}</dd>
            </div>
            <div>
              <dt>Brand</dt>
              <dd>{d.white_label.brand_configured ? "Configured" : "Not configured"}</dd>
            </div>
            <div>
              <dt>Domain</dt>
              <dd>{d.white_label.domain_status === "none" ? "None" : <Status value={d.white_label.domain_status} />}</dd>
            </div>
          </dl>
          <div className={styles.row}>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("branding")}>
              Branding
            </button>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("domains")}>
              Domains
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}

// --- customers ---------------------------------------------------------------------------------------

export function CustomersTab({ partnerId, rev }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [status, setStatus] = useState("");
  const [activation, setActivation] = useState("");
  const statusId = useId();
  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.customers(partnerId, { status: status || undefined, activation: activation || undefined, cursor }),
    [partnerId, status, activation, rev],
  );
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        Customers created by this partner or linked to it. A customer whose owner has not claimed the account is shown as
        awaiting activation. Customers are created by the partner itself, from its own portal.
      </p>
      <div className={local.filters}>
        <div>
          <label className="w360-label" htmlFor={statusId}>
            Relationship
          </label>
          <select id={statusId} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            <option value="live">Live</option>
            <option value="ended">Ended</option>
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${statusId}-a`}>
            Owner activation
          </label>
          <select id={`${statusId}-a`} className="w360-input" value={activation} onChange={(e) => setActivation(e.target.value)}>
            <option value="">Any</option>
            <option value="pending">Owner activation pending</option>
            <option value="activated">Owner activated</option>
            <option value="expired">Invitation expired</option>
            <option value="revoked">Invitation revoked</option>
          </select>
        </div>
      </div>
      {paged.state === "loading" && <p aria-live="polite">Loading customers…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to customers.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load customers.</p>}
      {paged.state === "ready" && (
        <>
          <CustomerTable
            rows={paged.rows}
            caption="This partner's customers"
            empty="No customers yet."
            actions={manage ? (row) => <StaffOwnerActions row={row} onDone={paged.reload} /> : undefined}
          />
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- products & pricing --------------------------------------------------------------------------------------

export function ProductsTab({ partnerId, partner, rev, bump }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const canPrice = can(permissions, "platform.staff_pricing.manage");
  const canCommission = can(permissions, "platform.staff_commission.manage");
  const { act, busy, feedback } = useAction(bump);
  const [catalog] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const [pricing] = useLoad(() => partnerConsoleApi.pricing(partnerId), [partnerId, rev]);
  const productId = useId();
  const [product, setProduct] = useState("");

  return (
    <div className={styles.stack}>
      {feedback}
      <Section title="Products this partner may sell">
        <Table caption="Product grants" head={["Product", "Plan", ""]} isEmpty={partner.product_grants.length === 0} empty="None: this partner cannot sell anything.">
          {partner.product_grants.map((g) => (
            <tr key={g.id}>
              <th scope="row">{g.product_code}</th>
              <td>{g.plan_code ?? "all plans"}</td>
              <td>
                {manage && (
                  <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.revokeProductGrant(partner.id, g.id), "Product grant revoked.")}>
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
        {manage && (
          <form
            className={styles.form}
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => adminApi.grantProduct(partner.id, product), "Product granted.");
            }}
          >
            <div className={styles.field}>
              <label className="w360-label" htmlFor={productId}>
                Allow a product
              </label>
              <select id={productId} className="w360-input" required value={product} onChange={(e) => setProduct(e.target.value)}>
                <option value="">Choose…</option>
                {catalog.status === "ready" &&
                  catalog.data.results
                    .filter((p) => p.status === "available")
                    .map((p) => (
                      <option key={p.code} value={p.code}>
                        {p.name}
                      </option>
                    ))}
              </select>
            </div>
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !product}>
                Grant product
              </button>
            </div>
          </form>
        )}
      </Section>

      <Section title="Prices">
        <p className={styles.muted}>
          The customer pays the partner price; the partner earns commission on it. The server calculates every figure. A plan
          without a partner price is charged the list price.
        </p>
        <LoadGate load={pricing} what="the prices">
          {(d) => (
            <PricingTable rows={d.results} canPrice={canPrice} busy={busy} onSave={(row, minor) => act(() => partnerConsoleApi.setPartnerPrice(partner.id, { plan_version_id: row.plan_version_id, interval: row.interval, amount_minor: minor }), "Partner price saved.")} currency={d.currency} />
          )}
        </LoadGate>
      </Section>

      <CommissionSection partnerId={partnerId} rev={rev} canManage={canCommission} act={act} busy={busy} products={catalog.status === "ready" ? catalog.data.results : []} />
    </div>
  );
}

function PricingTable({
  rows,
  currency,
  canPrice,
  busy,
  onSave,
}: {
  rows: PartnerPricingRow[];
  currency: string;
  canPrice: boolean;
  busy: boolean;
  onSave: (row: PartnerPricingRow, minor: number) => void;
}) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <>
      {problem && (
        <p role="alert" className="w360-error-text">
          {problem}
        </p>
      )}
      <Table
        caption="Partner prices"
        head={["Plan", "Cycle", "List price", "Partner price", "Discount", "Commission per sale", "Platform keeps", ...(canPrice ? ["Set price"] : [])]}
        isEmpty={rows.length === 0}
        empty="Nothing to price: allow a product with a published plan first."
      >
        {rows.map((r) => {
          const key = `${r.plan_version_id}|${r.interval}`;
          return (
            <tr key={key}>
              <th scope="row">
                {r.product_name} — {r.plan_name}
              </th>
              <td>{r.interval.replace(/_/g, " ")}</td>
              <td className={styles.num}>{r.list_price_minor === null ? "No list price" : money(r.list_price_minor, r.currency)}</td>
              <td className={styles.num}>
                {r.partner_price_minor === null ? "—" : money(r.partner_price_minor, r.currency)}
                <div className={styles.muted}>{r.partner_price_source === "partner_book" ? "partner price" : "list price"}</div>
              </td>
              <td className={styles.num}>{r.discount_minor === null ? "—" : `${money(r.discount_minor, r.currency)} (${formatBasisPoints(r.discount_bp ?? 0)})`}</td>
              <td className={styles.num}>{r.commission_minor === null ? "No commission" : `${money(r.commission_minor, r.currency)} at ${formatBasisPoints(r.commission_bp ?? 0)}`}</td>
              <td className={styles.num}>{r.platform_net_minor === null ? "—" : money(r.platform_net_minor, r.currency)}</td>
              {canPrice && (
                <td>
                  <form
                    className={styles.row}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const minor = toMinor(typed[key] ?? "", currency);
                      if (minor === null) {
                        setProblem(`Enter an amount in ${currency}, for example 45.00.`);
                        return;
                      }
                      setProblem(null);
                      onSave(r, minor);
                      setTyped((t) => ({ ...t, [key]: "" }));
                    }}
                  >
                    <label className="w360-visually-hidden" htmlFor={`set-${key}`}>
                      New partner price for {r.plan_name} ({r.interval}) in {currency}
                    </label>
                    <input id={`set-${key}`} className="w360-input" inputMode="decimal" value={typed[key] ?? ""} onChange={(e) => setTyped((t) => ({ ...t, [key]: e.target.value }))} />
                    <button type="submit" className="w360-btn w360-btn-secondary" disabled={busy || !(typed[key] ?? "").trim()}>
                      Save
                    </button>
                  </form>
                </td>
              )}
            </tr>
          );
        })}
      </Table>
    </>
  );
}

function CommissionSection({
  partnerId,
  rev,
  canManage,
  act,
  busy,
  products,
}: {
  partnerId: string;
  rev: number;
  canManage: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
  products: { code: string; name: string; status: string }[];
}) {
  const [state] = useLoad(() => partnerConsoleApi.commission(partnerId), [partnerId, rev]);
  const rateId = useId();
  const reasonId = useId();
  const scopeId = useId();
  const fromId = useId();
  const [rate, setRate] = useState("");
  const [reason, setReason] = useState("");
  const [scope, setScope] = useState("");
  const [from, setFrom] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <Section title="Commission">
      <p className={styles.muted}>
        A new rate is a new version that takes effect from the date you choose - never earlier. Sales already made keep the
        rate they were computed under.
      </p>
      <LoadGate load={state} what="the commission rules">
        {(d) => (
          <>
            <p>
              {d.in_force
                ? `In force now: ${formatBasisPoints(d.in_force.rate_bp)} (${d.in_force.scope.replace(/_/g, " ")}).`
                : "No rule applies: this partner earns nothing."}
              {!d.partner_earns && " It does not hold the Earn commission capability, so nothing accrues either way."}
            </p>
            <Table caption="This partner's rules" head={["Scope", "Version", "Rate", "From", "State"]} isEmpty={d.rules.length === 0} empty="No rule of its own: the programme default applies.">
              {d.rules.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{r.product_code || "All products"}</th>
                  <td>{r.version}</td>
                  <td className={styles.num}>{formatBasisPoints(r.rate_bp)}</td>
                  <td>{when(r.effective_from)}</td>
                  <td>{r.state.replace(/_/g, " ")}</td>
                </tr>
              ))}
            </Table>
            {canManage ? (
              <form
                className={styles.form}
                onSubmit={(e) => {
                  e.preventDefault();
                  const bp = toBasisPoints(rate);
                  if (bp === null) {
                    setProblem("Enter a percentage from 0 to 100 with at most two decimals, for example 15 or 12.5.");
                    return;
                  }
                  setProblem(null);
                  void act(
                    () =>
                      partnerConsoleApi.assignCommission(partnerId, {
                        rate_bp: bp,
                        reason: reason.trim(),
                        expected_version: d.live_versions[scope] ?? 0,
                        product_code: scope || undefined,
                        effective_from: from ? new Date(from).toISOString() : undefined,
                      }),
                    "Commission rate assigned.",
                  );
                }}
              >
                <div className={styles.field}>
                  <label className="w360-label" htmlFor={rateId}>
                    New rate (%)
                  </label>
                  <input id={rateId} className="w360-input" inputMode="decimal" required value={rate} onChange={(e) => setRate(e.target.value)} />
                </div>
                <div className={styles.field}>
                  <label className="w360-label" htmlFor={scopeId}>
                    Applies to
                  </label>
                  <select id={scopeId} className="w360-input" value={scope} onChange={(e) => setScope(e.target.value)}>
                    <option value="">All products</option>
                    {products
                      .filter((p) => p.status === "available")
                      .map((p) => (
                        <option key={p.code} value={p.code}>
                          {p.name} only
                        </option>
                      ))}
                  </select>
                </div>
                <div className={styles.field}>
                  <label className="w360-label" htmlFor={fromId}>
                    Takes effect (optional; default now)
                  </label>
                  <input id={fromId} className="w360-input" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
                </div>
                <div className={styles.field}>
                  <label className="w360-label" htmlFor={reasonId}>
                    Reason (recorded in the audit log)
                  </label>
                  <input id={reasonId} className="w360-input" required minLength={10} maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
                </div>
                {problem && (
                  <p role="alert" className="w360-error-text">
                    {problem}
                  </p>
                )}
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !rate.trim() || reason.trim().length < 10}>
                    Assign rate
                  </button>
                </div>
              </form>
            ) : (
              <p className={styles.muted}>Assigning a rate needs the platform.staff_commission.manage permission.</p>
            )}
          </>
        )}
      </LoadGate>
    </Section>
  );
}

// --- subscriptions ---------------------------------------------------------------------------------------------

export function SubscriptionsTab({ partnerId, rev }: TabProps) {
  const [subscription, setSubscription] = useState("");
  const filterId = useId();
  const paged = usePaged(
    (cursor) => partnerConsoleApi.customers(partnerId, { status: "live", subscription: subscription || undefined, cursor }),
    [partnerId, subscription, rev],
  );
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        The subscriptions of this partner&apos;s live customers. They are changed on the customer&apos;s own page - through the same
        commercial service a direct sale uses - and by the partner from its portal; nothing here writes one.
      </p>
      <div className={local.filters}>
        <div>
          <label className="w360-label" htmlFor={filterId}>
            Subscription status
          </label>
          <select id={filterId} className="w360-input" value={subscription} onChange={(e) => setSubscription(e.target.value)}>
            {["", "active", "trialing", "past_due", "grace", "suspended", "cancelled", "expired", "none"].map((s) => (
              <option key={s} value={s}>
                {s === "" ? "Any" : s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      </div>
      {paged.state === "loading" && <p aria-live="polite">Loading subscriptions…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to subscriptions.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load subscriptions.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Customer subscriptions" head={["Customer", "Subscription", "Products", "Seats", ""]} isEmpty={paged.rows.length === 0} empty="No subscriptions match.">
            {paged.rows.map((c) => (
              <tr key={c.link_id}>
                <th scope="row">{c.name}</th>
                <td>
                  <Status value={c.subscription_status} />
                </td>
                <td>{c.products.join(", ") || "—"}</td>
                <td className={styles.num}>{c.seat_capacity ?? "—"}</td>
                <td>
                  <Link href={`/admin/customers/${c.organization_id}`}>Manage on the customer page</Link>
                </td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- commissions (the ledger) ---------------------------------------------------------------------------------------

export function CommissionsTab({ partnerId, rev }: TabProps) {
  const [summary] = useLoad(() => partnerConsoleApi.summary(partnerId), [partnerId, rev]);
  const paged = usePaged((cursor) => adminApi.accruals({ partner_id: partnerId, cursor }), [partnerId, rev]);
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        The ledger is append-only. Each entry is an <strong>accrual</strong> (earned on a sale), a <strong>reversal</strong> (offsets an
        earlier entry) or an <strong>adjustment</strong> (a staff correction). An entry has no payment status of its own: whether it
        has been paid is the state of the payout that includes it.
      </p>
      <LoadGate load={summary} what="the balance">
        {(d) => (d.commission.length === 0 ? <p className={styles.muted}>No commission yet.</p> : <CommissionSummary rows={d.commission} />)}
      </LoadGate>
      {paged.state === "loading" && <p aria-live="polite">Loading the ledger…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to the ledger.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load the ledger.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="Commission ledger entries"
            head={["When", "Kind", "Amount", "Customer", "Sale", "Rule", "Payout", "Clears"]}
            isEmpty={paged.rows.length === 0}
            empty="No entries yet."
          >
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.event_at)}</td>
                <td>
                  {a.kind}
                  {a.reason_code && <div className={styles.muted}>{a.reason_code.replace(/_/g, " ")}</div>}
                </td>
                <td className={styles.num}>{money(a.amount_minor, a.currency)}</td>
                <td>{a.organization_name || "—"}</td>
                <td className={styles.mono}>{a.source_ref ? a.source_ref.replace("subscriptions.item:", "item ").slice(0, 13) : "—"}</td>
                <td>{a.rule_code ? `${a.rule_code} v${a.rule_version}${a.rate_bp != null ? ` · ${formatBasisPoints(a.rate_bp)}` : ""}` : "—"}</td>
                <td>{a.payout ? <Status value={a.payout.status} /> : <span className={styles.muted}>Not in a payout</span>}</td>
                <td>{when(a.clears_at)}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- payouts ---------------------------------------------------------------------------------------------------------------

export function PayoutsTab({ partnerId, rev, bump }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payout.manage");
  const approve = can(permissions, "platform.staff_payout.approve");
  const paged = usePaged((cursor) => adminApi.payouts({ partner_id: partnerId, cursor }), [partnerId, rev]);
  const { act, busy, feedback } = useAction(() => {
    paged.reload();
    bump();
  });
  const dateId = useId();
  const [periodEnd, setPeriodEnd] = useState("");
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        A payout is a reviewed batch of cleared commission. This platform <strong>moves no money</strong>: “paid” records that a person
        confirmed a transfer made elsewhere. The person who prepares a payout cannot approve it.
      </p>
      {feedback}
      {paged.state === "loading" && <p aria-live="polite">Loading payouts…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to payouts.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load payouts.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="This partner's payouts" head={["Period end", "Status", "Gross", "Adjustments", "Payable", "Entries", "Actions"]} isEmpty={paged.rows.length === 0} empty="No payouts.">
            {paged.rows.map((p) => (
              <PayoutRowView key={p.id} payout={p} manage={manage} approve={approve} busy={busy} act={act} />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
      {manage && (
        <form
          className={`w360-card ${styles.card} ${styles.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => adminApi.preparePayout(partnerId, periodEnd), "Draft payout prepared from cleared commission.");
          }}
        >
          <h3 className={styles.cardTitle}>Prepare a payout for this partner</h3>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={dateId}>
              Include everything cleared by
            </label>
            <input id={dateId} className="w360-input" type="date" required value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !periodEnd}>
              Prepare payout
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// --- branding and domains ------------------------------------------------------------------------------------------------------

export function BrandingTab({ partnerId, rev }: TabProps) {
  const [load] = useLoad(() => partnerConsoleApi.brand(partnerId), [partnerId, rev]);
  return (
    <div className={styles.stack}>
      <LoadGate load={load} what="the brand">
        {({ brand }) =>
          brand === null ? (
            <p>No brand has been set up for this partner.</p>
          ) : (
            <dl className={local.review}>
              <div>
                <dt>Brand name</dt>
                <dd>{brand.brand_name}</dd>
              </div>
              <div>
                <dt>Accent colour</dt>
                <dd>{brand.accent_color || "Default"}</dd>
              </div>
              <div>
                <dt>Logo</dt>
                <dd>{brand.logo_file_id ? "Uploaded" : "None"}</dd>
              </div>
              <div>
                <dt>Support</dt>
                <dd>{[brand.support_email, brand.support_url].filter(Boolean).join(" · ") || "None"}</dd>
              </div>
              <div>
                <dt>Last changed</dt>
                <dd>{when(brand.updated_at)}</dd>
              </div>
            </dl>
          )
        }
      </LoadGate>
      <div>
        <Link className="w360-btn w360-btn-primary" href={`/admin/partners/white-label/${partnerId}`}>
          Set up white-label
        </Link>
      </div>
    </div>
  );
}

export function DomainsTab({ partnerId, rev, bump }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [load] = useLoad(() => adminApi.domains(partnerId), [partnerId, rev]);
  const { act, busy, feedback } = useAction(bump);
  return (
    <div className={styles.stack}>
      {feedback}
      <LoadGate load={load} what="the domains">
        {(d) => (
          <Table caption="Custom domains" head={["Hostname", "Status", "DNS record to publish", ""]} isEmpty={d.results.length === 0} empty="No domains requested.">
            {d.results.map((r) => (
              <tr key={r.id}>
                <th scope="row" className={styles.mono}>
                  {r.hostname}
                </th>
                <td>
                  <Status value={r.status} />
                  {r.failure_code && <div className={styles.muted}>{r.failure_code.replace(/_/g, " ")}</div>}
                </td>
                <td className={styles.mono}>
                  {r.verification_record.value ? `${r.verification_record.type} ${r.verification_record.name} = ${r.verification_record.value}` : "—"}
                </td>
                <td>
                  {manage && r.status !== "disabled" && r.status !== "failed" && (
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.disableDomain(r.id), "Domain disabled.")}>
                      Disable
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      <div>
        <Link className="w360-btn w360-btn-primary" href={`/admin/partners/white-label/${partnerId}`}>
          Set up white-label
        </Link>
      </div>
    </div>
  );
}

// --- users & permissions (the sections that existed before the tabs) --------------------------------------------------------------

export function UsersTab({ partner, bump }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [caps] = useLoad(() => adminApi.capabilities(), []);
  const { act, busy, feedback } = useAction(bump);
  const capId = useId();
  const memberId = useId();
  const [capability, setCapability] = useState("");
  const [capReason, setCapReason] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("admin");
  const p = partner;
  return (
    <div className={styles.stack}>
      {feedback}
      <Section title="Capabilities">
        <p className={styles.muted}>What this partner can do is exactly the capabilities it holds; “Reseller” and “White-label” are what some of them are called.</p>
        <Table caption="Granted capabilities" head={["Capability", "Granted", "Expires", ""]} isEmpty={p.capabilities.length === 0} empty="None granted: this partner can do nothing.">
          {p.capabilities.map((c) => (
            <tr key={c.capability}>
              <th scope="row" className={styles.mono}>
                {c.capability}
              </th>
              <td>{when(c.granted_at)}</td>
              <td>{c.expires_at ? when(c.expires_at) : "never"}</td>
              <td>{manage && <ReasonAction label="Revoke" busy={busy} onConfirm={(r) => act(() => adminApi.revokeCapability(p.id, c.capability, r), "Capability revoked.")} />}</td>
            </tr>
          ))}
        </Table>
        {manage && caps.status === "ready" && (
          <form
            className={styles.form}
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => adminApi.grantCapability(p.id, capability, capReason.trim()), "Capability granted.");
            }}
          >
            <div className={styles.field}>
              <label className="w360-label" htmlFor={capId}>
                Grant a capability
              </label>
              <select id={capId} className="w360-input" required value={capability} onChange={(e) => setCapability(e.target.value)}>
                <option value="">Choose…</option>
                {caps.data.results.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.key} — {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className={styles.field}>
              <label className="w360-label" htmlFor={`${capId}-r`}>
                Reason (recorded)
              </label>
              <input id={`${capId}-r`} className="w360-input" required minLength={10} maxLength={200} value={capReason} onChange={(e) => setCapReason(e.target.value)} />
            </div>
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !capability || capReason.trim().length < 10}>
                Grant capability
              </button>
            </div>
          </form>
        )}
      </Section>

      <Section title="Partner users">
        <Table caption="Members" head={["Email", "Role", "Sign-in", "Added", ""]} isEmpty={p.members.length === 0} empty="No users yet.">
          {p.members.map((m) => (
            <tr key={m.id}>
              <th scope="row">{m.email}</th>
              <td>{m.role}</td>
              <td>{m.password_set ? "Ready" : "Invitation pending"}</td>
              <td>{when(m.added_at)}</td>
              <td>
                <div className={styles.row}>
                  {manage && !m.password_set && (
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => partnerConsoleApi.resendSetup(p.id, m.id), "A new setup link was sent. The old one no longer works.")}>
                      Resend setup link
                    </button>
                  )}
                  {manage && (
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.removeMember(p.id, m.id), "User removed. Their access ended immediately.")}>
                      Remove
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </Table>
        {manage && (
          <form
            className={styles.form}
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await partnerConsoleApi.inviteAdministrator(p.id, email.trim(), role);
                setEmail("");
              }, "Added. If the address had no account, one was created and the person was emailed a link to choose a password.");
            }}
          >
            <div className={styles.field}>
              <label className="w360-label" htmlFor={memberId}>
                Add a person by email
              </label>
              <input id={memberId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className={styles.field}>
              <label className="w360-label" htmlFor={`${memberId}-role`}>
                Role
              </label>
              <select id={`${memberId}-role`} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="admin">Administrator</option>
                <option value="viewer">Viewer (read-only)</option>
              </select>
            </div>
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !email.trim()}>
                Add user
              </button>
            </div>
          </form>
        )}
      </Section>
    </div>
  );
}

// --- activity ----------------------------------------------------------------------------------------------------------------------------

export function ActivityTab({ partnerId, rev }: TabProps) {
  const paged = usePaged((cursor) => partnerConsoleApi.activity(partnerId, cursor), [partnerId, rev]);
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>Everything recorded about this partner in the platform audit log, newest first.</p>
      {paged.state === "loading" && <p aria-live="polite">Loading activity…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to the audit log.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load activity.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Partner activity" head={["When", "Action", "By", "Outcome", "Reason"]} isEmpty={paged.rows.length === 0} empty="No activity recorded.">
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.occurred_at)}</td>
                <th scope="row" className={styles.mono}>
                  {a.action}
                </th>
                <td>{a.actor_kind}</td>
                <td>{a.outcome}</td>
                <td>{a.reason || "—"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}
