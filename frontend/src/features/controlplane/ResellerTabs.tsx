"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi, type PartnerDetail } from "@/lib/api/controlPlane";
import {
  partnerConsoleApi,
  type CompletionSection,
  type ConsoleProduct,
  type PartnerPricingRow,
  type PartnerReferrals,
  type PartnerSummaryView,
  type SummaryProduct,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { CommissionSummary, ExperienceLabels } from "./PartnerConsole";
import { CustomerTable, StaffOwnerActions } from "./PartnerCustomers";
import { PayoutRowView } from "./AdminMoney";
import { MembersSection } from "./PartnerMembers";
import { Section } from "./PartnerSection";
import { TAB_LABELS, type TabKey, type TabProps } from "./partnerTabs";
import { LoadGate, MoreButton, ReasonAction, Status, Table, autoReason, money, useAction, useLoad, usePaged, when } from "./ui";
import { formatBasisPoints, toBasisPoints, toMinor } from "./amounts";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";
import ps from "./PartnerProfileTabs.module.css";

/**
 * The panels of one partner's page (Phase 10c, simplified by ADR-048). Each tab
 * reads through the console's own read models and changes anything only through
 * the *existing* commands: capabilities, product grants, prices, commission,
 * members, domains and payouts each keep the one implementation they always
 * had. The partner's Complete Your Profile wizard shows several of these same
 * panels as its steps. Subscriptions are shown here and changed where they
 * always were (the customer's page), and nothing in this file writes a
 * subscription or an entitlement.
 *
 * The Profile tab's editors are in `PartnerProfileTabs.tsx`, the people table in
 * `PartnerMembers.tsx`.
 */

export type { TabProps };

// --- overview ---------------------------------------------------------------------------------

const SECTION_TEXT: Record<CompletionSection["state"], string> = {
  complete: "Complete",
  incomplete: "Incomplete",
  pending: "Waiting",
  optional: "Not required",
};

const SECTION_BADGE: Record<CompletionSection["state"], string> = {
  complete: "w360-badge w360-badge-success",
  incomplete: "w360-badge w360-badge-warning",
  pending: "w360-badge w360-badge-neutral",
  optional: "w360-badge w360-badge-neutral",
};

/** Where each completion section is filled in: a step of Complete Your Profile,
 * or (for the capabilities and the administrator) a tab of this page. */
const SECTION_STEP: Record<string, string> = {
  business: "business",
  products: "products",
  pricing: "pricing",
  commission: "commission",
  brand: "brand",
  domain: "domain",
  referral: "referral",
};
const SECTION_TAB: Record<string, TabKey> = { capabilities: "users", administrator: "users" };

type OnOpen = (tab: TabKey) => void;

export function OverviewTab({
  partner,
  summary,
  onOpen,
  available,
}: TabProps & { onOpen: OnOpen; /** The tabs this page is showing: a link to any other would lead nowhere. */ available: TabKey[] }) {
  return (
    <LoadGate load={summary} what="the overview">
      {(d) => <OverviewBody summary={d} partner={partner} onOpen={onOpen} available={available} />}
    </LoadGate>
  );
}

/** The profile's sections as the server computed them: what is done, what is
 * not, and where each is filled in. Optional sections are listed and marked
 * "Not required"; they never count towards the percentage. */
export function CompletionList({
  sections,
  partnerId,
  onOpen,
  available,
  onStep,
}: {
  sections: CompletionSection[];
  partnerId: string;
  onOpen?: OnOpen;
  available?: TabKey[];
  /** Inside Complete Your Profile: move to a step of the wizard itself (a link to
   * the same page would change the address and not the step). `steps` are the
   * wizard's own; a section whose step is not among them links to the partner's page. */
  onStep?: { go: (step: string) => void; steps: readonly string[] };
}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  return (
    <ul className={ps.progress}>
      {sections.map((s) => {
        const step = SECTION_STEP[s.key];
        const tab = SECTION_TAB[s.key];
        const open = s.state !== "complete";
        return (
          <li key={s.key} className={ps.progressRow}>
            <span className={SECTION_BADGE[s.state]}>{SECTION_TEXT[s.state]}</span>
            <span className={ps.progressText}>
              <strong>
                {s.label}
                {!s.required && <span className={styles.muted}> (optional)</span>}
              </strong>
              {s.detail && <span className={styles.muted}>{s.detail}</span>}
            </span>
            {open && manage && step && onStep?.steps.includes(step) && (
              <button type="button" className={`w360-btn w360-btn-secondary ${ps.linkButton}`} onClick={() => onStep.go(step)}>
                Complete<span className="w360-visually-hidden"> {s.label}</span>
              </button>
            )}
            {open && manage && step && !onStep && (
              <Link className={`w360-btn w360-btn-secondary ${ps.linkButton}`} href={`/admin/partners/${partnerId}/profile?step=${step}`}>
                Complete<span className="w360-visually-hidden"> {s.label}</span>
              </Link>
            )}
            {open && manage && onStep && (s.key === "administrator" ? !onStep.steps.includes("team") : tab !== undefined) && (
              <Link className={`w360-btn w360-btn-secondary ${ps.linkButton}`} href={`/admin/partners/${partnerId}?tab=users`}>
                Open Users &amp; Permissions<span className="w360-visually-hidden"> to deal with {s.label}</span>
              </Link>
            )}
            {open && manage && onStep && s.key === "administrator" && onStep.steps.includes("team") && (
              <button type="button" className={`w360-btn w360-btn-secondary ${ps.linkButton}`} onClick={() => onStep.go("team")}>
                Complete<span className="w360-visually-hidden"> {s.label}</span>
              </button>
            )}
            {open && tab && onOpen && available?.includes(tab) && (
              <button type="button" className={`w360-btn w360-btn-secondary ${ps.linkButton}`} onClick={() => onOpen(tab)}>
                Open {TAB_LABELS[tab]}
                <span className="w360-visually-hidden"> to deal with {s.label}</span>
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Stat({ label, children, note }: { label: string; children: React.ReactNode; note?: string }) {
  return (
    <li className={ps.stat}>
      <span className={ps.statLabel}>{label}</span>
      <span className={ps.statValue}>{children}</span>
      {note && <span className={ps.statNote}>{note}</span>}
    </li>
  );
}

function OverviewBody({
  summary: d,
  partner,
  onOpen,
  available,
}: {
  summary: PartnerSummaryView;
  partner: PartnerDetail;
  onOpen: OnOpen;
  available: TabKey[];
}) {
  const labels = d.experience.map((e) => e.key);
  const sells = labels.includes("reseller") || labels.includes("white_label");
  return (
    <div className={styles.stack}>
      <Section title="Profile completion">
        <p>
          <strong>{d.profile_completion.percent}% complete</strong>
          <span className={styles.muted}>
            {" "}
            · {d.profile_completion.done} of {d.profile_completion.required} required sections
          </span>
        </p>
        <CompletionList sections={d.profile_completion.sections} partnerId={partner.id} onOpen={onOpen} available={available} />
      </Section>
      <ul className={ps.stats} aria-label="Key figures">
        <Stat label="Customers" note={d.customers.owner_activation_pending > 0 ? `${d.customers.owner_activation_pending} awaiting owner activation` : undefined}>
          {d.customers.live}
        </Stat>
        <Stat label="Products">{d.products.length}</Stat>
        <Stat label="Subscriptions" note="active">
          {d.active_subscriptions}
        </Stat>
        <Stat label="Commission" note={d.commission_rule ? d.commission_rule.scope.replace(/_/g, " ") : "no rule applies"}>
          {d.commission_rule ? formatBasisPoints(d.commission_rule.rate_bp) : "None"}
        </Stat>
        <Stat label="Pending payout" note="open">
          {d.payouts.open}
        </Stat>
        <Stat label="Domain status">{d.white_label.domain_status === "none" ? "None" : <Status value={d.white_label.domain_status} />}</Stat>
      </ul>
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
                {available.includes("users") && (
                  <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("users")}>
                    Manage capabilities
                  </button>
                )}
              </dd>
            </div>
          </dl>
        </Section>
        {sells && (
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
        )}
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
            Open products
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
            {available.includes("commission") && (
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("commission")}>
                Open commission
              </button>
            )}
          </div>
        </Section>
        <Section title="Payouts">
          <p>
            {d.payouts.open} open.{" "}
            {d.payouts.latest
              ? `Latest: ${d.payouts.latest.status}, ${money(d.payouts.latest.payable_minor, d.payouts.latest.currency)} for the period to ${d.payouts.latest.period_end}.`
              : "None yet."}
          </p>
          {available.includes("payouts") && (
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("payouts")}>
              Open payouts
            </button>
          )}
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
          {d.white_label.enabled ? (
            <div className={styles.row}>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("branding")}>
                Branding
              </button>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("domains")}>
                Domains
              </button>
            </div>
          ) : (
            <p className={styles.muted}>Not a white-label partner. Add the White-label experience under {TAB_LABELS.users} to give it a brand and a domain.</p>
          )}
        </Section>
        <Section title="Affiliate & referrals">
          {d.experience.some((e) => e.key === "affiliate") ? (
            <dl className={local.review}>
              <div>
                <dt>Active referral codes</dt>
                <dd>{d.referrals.active_codes}</dd>
              </div>
              <div>
                <dt>Referrals</dt>
                <dd>
                  {d.referrals.converted} converted of {d.referrals.captured} captured
                </dd>
              </div>
            </dl>
          ) : (
            <p className={styles.muted}>This partner holds no referral capability.</p>
          )}
          {d.experience.some((e) => e.key === "affiliate") && (
            <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen("referrals")}>
              Open referrals
            </button>
          )}
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

/** Whether the server's labels say this partner sells (Reseller or White-label). */
export const sellsFrom = (summary: TabProps["summary"], partner: TabProps["partner"]): boolean => {
  const labels = summary.status === "ready" ? summary.data.experience.map((e) => e.key) : (partner.experience ?? []).map((e) => e.key);
  return labels.includes("reseller") || labels.includes("white_label");
};

/** Products: what this partner may sell (product grants and their two options).
 * An affiliate is granted no product: what it promotes is the scope of its
 * commission, so for a partner that does not sell this says so instead. */
export function ProductsTab(props: TabProps) {
  return sellsFrom(props.summary, props.partner) ? <ProductGrantsPanel {...props} /> : <PromotionPanel {...props} />;
}

function PromotionPanel({ partnerId, rev }: TabProps) {
  const [state] = useLoad(() => partnerConsoleApi.commission(partnerId), [partnerId, rev]);
  const [catalog] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const nameOf = (code: string) => (catalog.status === "ready" ? (catalog.data.results.find((p) => p.code === code)?.name ?? code) : code);
  return (
    <div className={styles.stack}>
      <Section title="Products this partner promotes">
        <p className={styles.muted}>
          An affiliate promotes Workeasy360 products and earns commission on the customers it refers. It is granted no product: it cannot
          sell, price or administer anything for a customer. What it promotes is the scope of its commission rate - all products, or one
          product per rate - set on the Commission step.
        </p>
        <LoadGate load={state} what="the commission scope">
          {(d) => {
            const scoped = [...new Set(d.rules.filter((r) => r.state !== "superseded").map((r) => r.product_code))];
            if (scoped.length === 0) return <p>No rate of its own yet: the programme&apos;s default rate applies to every product.</p>;
            return (
              <ul className={local.checks}>
                {scoped.map((code) => (
                  <li key={code || "all"}>{code ? nameOf(code) : "All products"}</li>
                ))}
              </ul>
            );
          }}
        </LoadGate>
      </Section>
      {catalog.status === "ready" && (
        <Section title="Products in the catalog">
          <ul className={local.checks}>
            {catalog.data.results
              .filter((p) => p.status === "available")
              .map((p) => (
                <li key={p.code}>{p.name}</li>
              ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function ProductGrantsPanel({ partner, summary, bump }: TabProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const { act, busy, feedback } = useAction(bump);
  const [catalog] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  const productId = useId();
  const [product, setProduct] = useState("");
  const granted = new Set(partner.product_grants.map((g) => g.product_code));

  return (
    <div className={styles.stack}>
      {feedback}
      <Section title="Products this partner may sell">
        <p className={styles.muted}>
          Chosen from the product catalog; a product added to the catalog appears here. Allowing a product lets the partner sell it - it
          gives no customer access by itself.
        </p>
        <Table caption="Product grants" head={["Product", "Plan", ""]} isEmpty={partner.product_grants.length === 0} empty="None: this partner cannot sell anything yet.">
          {partner.product_grants.map((g) => (
            <tr key={g.id}>
              <th scope="row">{g.product_code}</th>
              <td>{g.plan_code ?? "all plans"}</td>
              <td>
                {manage && (
                  <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.revokeProductGrant(partner.id, g.id), "Product removed.")}>
                    Remove<span className="w360-visually-hidden"> {g.product_code}</span>
                  </button>
                )}
              </td>
            </tr>
          ))}
        </Table>
        {manage && partner.status !== "terminated" && (
          <form
            className={styles.form}
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => adminApi.grantProduct(partner.id, product), "Product allowed.");
              setProduct("");
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
                    .filter((p) => p.status === "available" && !granted.has(p.code))
                    .map((p) => (
                      <option key={p.code} value={p.code}>
                        {p.name}
                      </option>
                    ))}
              </select>
            </div>
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !product}>
                Allow product
              </button>
            </div>
          </form>
        )}
      </Section>

      <Section title="Product options">
        <p className={styles.muted}>
          Two choices for each product this partner may sell. A partner can only narrow the platform&apos;s free trial: ticking the trial
          here never creates one, and a trial that has already started is not touched. Showing a product on the partner&apos;s portal
          never grants or removes access: a customer who holds the product can still open it either way.
        </p>
        {partner.status === "terminated" && <p className={styles.muted}>This partner is terminated, so its product options can no longer be changed.</p>}
        <LoadGate load={summary} what="the product options">
          {(d) => (
            <ProductOptions
              products={d.products}
              trials={catalog.status === "ready" ? catalog.data.results : null}
              manage={manage && partner.status !== "terminated"}
              busy={busy}
              onChange={(product, change, success) => act(() => partnerConsoleApi.updateProductGrant(partner.id, product.grant_id, change), success)}
            />
          )}
        </LoadGate>
      </Section>
    </div>
  );
}

/** Pricing: what a customer of this partner pays for each plan it may sell,
 * beside the list price, with the server's discount, commission and margin. */
export function PricingTab({ partnerId, partner, rev, bump }: TabProps) {
  const { permissions } = useAdmin();
  const canPrice = can(permissions, "platform.staff_pricing.manage");
  const { act, busy, feedback } = useAction(bump);
  const [pricing] = useLoad(() => partnerConsoleApi.pricing(partnerId), [partnerId, rev]);
  return (
    <div className={styles.stack}>
      {feedback}
      <Section title="Prices">
        <p className={styles.muted}>
          Optional. The customer pays the partner price; the partner earns commission on it. A plan without a partner price is charged the
          list price. The server calculates every figure - nothing here computes a price.
        </p>
        <LoadGate load={pricing} what="the prices">
          {(d) => (
            <PricingTable
              rows={d.results}
              canPrice={canPrice && partner.status !== "terminated"}
              busy={busy}
              onSave={(row, minor) =>
                act(
                  () => partnerConsoleApi.setPartnerPrice(partner.id, { plan_version_id: row.plan_version_id, interval: row.interval, amount_minor: minor }),
                  "Partner price saved.",
                )
              }
              currency={d.currency}
            />
          )}
        </LoadGate>
      </Section>
    </div>
  );
}

/** Commission: the rate this partner earns (a new version from a date, never
 * backdated) and, for staff who may read money, its ledger. */
export function CommissionTab(props: TabProps & { ledger?: boolean }) {
  const { partnerId, rev, bump, ledger = true } = props;
  const { permissions } = useAdmin();
  const canCommission = can(permissions, "platform.staff_commission.manage");
  const { act, busy, feedback } = useAction(bump);
  const [catalog] = useLoad(() => partnerConsoleApi.products().catch(() => ({ results: [] })), []);
  return (
    <div className={styles.stack}>
      {feedback}
      <CommissionSection partnerId={partnerId} rev={rev} canManage={canCommission} act={act} busy={busy} products={catalog.status === "ready" ? catalog.data.results : []} />
      {ledger && can(permissions, "platform.staff_commission.view") && (
        <Section title="Commission earned">
          <CommissionsTab {...props} />
        </Section>
      )}
    </div>
  );
}

const yesNo = (value: boolean) => (value ? "Yes" : "No");

/** What the platform's own trial is for one product, as a sentence. The trial is
 * the platform's; this only says whether there is one for the partner's choice to narrow. */
function platformTrialText(product: ConsoleProduct | undefined): string {
  if (!product) return "";
  return product.trial?.enabled
    ? `The platform's free trial for this product lasts ${product.trial.duration_days} days.`
    : "The platform has no free trial for this product, so none can start whatever is chosen here.";
}

function ProductOptions({
  products,
  trials,
  manage,
  busy,
  onChange,
}: {
  products: SummaryProduct[];
  /** The catalog, for what the platform's trial is; `null` until it has loaded. */
  trials: ConsoleProduct[] | null;
  manage: boolean;
  busy: boolean;
  onChange: (product: SummaryProduct, change: { trial_allowed?: boolean; portal_visible?: boolean }, success: string) => void;
}) {
  if (products.length === 0) return <p className={styles.muted}>None: this partner may not sell any product yet, so there is nothing to choose.</p>;
  return (
    <ul className={ps.options}>
      {products.map((p) => {
        const name = p.plan_code ? `${p.product_name} (${p.plan_code} only)` : p.product_name;
        const hint = platformTrialText(trials?.find((t) => t.code === p.product_code));
        return (
          <li key={p.grant_id}>
            {manage ? (
              <fieldset className={ps.optionGroup} aria-busy={busy}>
                <legend>{name}</legend>
                <label className={ps.optionLabel}>
                  <input
                    type="checkbox"
                    checked={p.trial_allowed}
                    aria-describedby={hint ? `trial-hint-${p.grant_id}` : undefined}
                    onChange={(e) => onChange(p, { trial_allowed: e.target.checked }, "Trial choice saved.")}
                  />
                  <span>Customers may start the free trial</span>
                </label>
                {hint && (
                  <p id={`trial-hint-${p.grant_id}`} className="w360-hint">
                    {hint}
                  </p>
                )}
                <label className={ps.optionLabel}>
                  <input
                    type="checkbox"
                    checked={p.portal_visible}
                    onChange={(e) => onChange(p, { portal_visible: e.target.checked }, "Portal visibility saved.")}
                  />
                  <span>Shown on the partner&apos;s portal</span>
                </label>
              </fieldset>
            ) : (
              <div className={ps.optionGroup}>
                <strong>{name}</strong>
                <dl className={ps.readOnly}>
                  <div>
                    <dt>Customers may start the free trial</dt>
                    <dd>
                      {yesNo(p.trial_allowed)}
                      {hint && <span className="w360-hint"> {hint}</span>}
                    </dd>
                  </div>
                  <div>
                    <dt>Shown on the partner&apos;s portal</dt>
                    <dd>{yesNo(p.portal_visible)}</dd>
                  </div>
                </dl>
              </div>
            )}
          </li>
        );
      })}
    </ul>
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
  const scopeId = useId();
  const fromId = useId();
  const [rate, setRate] = useState("");
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
            <p className={styles.muted}>
              Commission type: a percentage of the sale. Rates apply per product (or to all products); the rule engine has no per-plan scope and
              no per-partner hold period - commission clears after the programme&apos;s holdback.
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
                        reason: autoReason("Assign commission rate"),
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
                {problem && (
                  <p role="alert" className="w360-error-text">
                    {problem}
                  </p>
                )}
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !rate.trim()}>
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
        <Link className="w360-btn w360-btn-primary" href={`/admin/partners/${partnerId}/profile?step=brand`}>
          Edit brand identity
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
        <Link className="w360-btn w360-btn-primary" href={`/admin/partners/${partnerId}/profile?step=domain`}>
          Request or verify a domain
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
  const [capability, setCapability] = useState("");
  const [capReason, setCapReason] = useState("");
  const p = partner;
  // The friendly name of each capability, from the server's catalog. A key the
  // catalog does not know (or a catalog that did not load) is shown as its key.
  const labelOf = (key: string): string | null => (caps.status === "ready" ? (caps.data.results.find((c) => c.key === key)?.label ?? null) : null);
  return (
    <div className={styles.stack}>
      {feedback}
      <Section title="Capabilities">
        <p className={styles.muted}>What this partner can do is exactly the capabilities it holds; “Reseller” and “White-label” are what some of them are called.</p>
        <Table caption="Granted capabilities" head={["Capability", "Granted", "Expires", ""]} isEmpty={p.capabilities.length === 0} empty="None granted: this partner can do nothing.">
          {p.capabilities.map((c) => (
            <tr key={c.capability}>
              <th scope="row">
                <div className={ps.stackedCell}>
                  {labelOf(c.capability) && <span>{labelOf(c.capability)}</span>}
                  <span className={`${styles.mono} ${styles.muted}`}>{c.capability}</span>
                </div>
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
                    {c.label} ({c.key})
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

      <MembersSection partner={p} manage={manage} act={act} busy={busy} />
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
          <Table caption="Partner activity" head={["Timestamp", "Actor", "Action", "Target", "Reason"]} isEmpty={paged.rows.length === 0} empty="No activity recorded.">
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.occurred_at)}</td>
                <td>{a.actor}</td>
                <th scope="row">
                  <div className={ps.stackedCell}>
                    <span>
                      {a.label}
                      {a.outcome && a.outcome !== "success" && <span className={styles.muted}> ({a.outcome})</span>}
                    </span>
                    <span className={`${styles.mono} ${styles.muted}`} title="The recorded action">
                      {a.action}
                    </span>
                  </div>
                </th>
                <td>{a.target || "-"}</td>
                <td>{a.reason || "-"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- referrals ---------------------------------------------------------------------------------------

const REFERRAL_STATES: { key: string; label: string }[] = [
  { key: "captured", label: "Waiting to convert" },
  { key: "converted", label: "Converted" },
  { key: "expired", label: "Expired" },
];

/** The partner's referral codes and referrals - read-only for staff (a code is
 * the partner's to create, from its own portal). Counts and codes only: a
 * referral row identifies nothing about who was referred. */
export function ReferralsTab({ partnerId, rev }: TabProps) {
  const [head, setHead] = useState<Pick<PartnerReferrals, "summary" | "codes"> | null>(null);
  const paged = usePaged(
    async (cursor?: string) => {
      const page = await partnerConsoleApi.referrals(partnerId, cursor);
      if (!cursor) setHead({ summary: page.summary, codes: page.codes });
      return page;
    },
    [partnerId, rev],
  );
  return (
    <div className={styles.stack}>
      {paged.state === "loading" && <p aria-live="polite">Loading referrals…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to this partner&apos;s referrals.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load referrals.</p>}
      {paged.state === "ready" && head && (
        <>
          <Section title="Referrals by state">
            <dl className={local.review}>
              {REFERRAL_STATES.map((s) => (
                <div key={s.key}>
                  <dt>{s.label}</dt>
                  <dd>{head.summary[s.key] ?? 0}</dd>
                </div>
              ))}
            </dl>
          </Section>
          <Table caption="Referral codes" head={["Code", "Campaign", "Status", "Created"]} isEmpty={head.codes.length === 0} empty="This partner has not created a referral code.">
            {head.codes.map((c) => (
              <tr key={c.id}>
                <th scope="row" className={styles.mono}>
                  {c.code}
                </th>
                <td>{c.label || "—"}</td>
                <td>
                  <Status value={c.status} />
                </td>
                <td>{when(c.created_at)}</td>
              </tr>
            ))}
          </Table>
          <Table caption="Referrals" head={["Code", "Status", "Captured", "Converted"]} isEmpty={paged.rows.length === 0} empty="No referral has been captured yet.">
            {paged.rows.map((r) => (
              <tr key={r.id}>
                <th scope="row" className={styles.mono}>
                  {r.code}
                </th>
                <td>
                  <Status value={r.status} />
                </td>
                <td>{when(r.captured_at)}</td>
                <td>{when(r.converted_at)}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}
