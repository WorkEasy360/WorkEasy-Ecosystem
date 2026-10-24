"use client";

import { useId, useState } from "react";
import Link from "next/link";
import {
  adminApi,
  type CatalogProduct,
  type ConsoleIntegration,
  type ConsoleProduct,
  type PayoutRow,
  type ProductChanges,
  type ProductsConsole as ProductsConsoleData,
  type TrialConfig,
} from "@/lib/api/controlPlane";
import { can, useAdmin } from "./ControlShell";
import { AuthenticationEditor, AuthenticationFacts, CredentialsPanel, RegisterIntegration } from "./IntegrationAuthentication";
import { LoadGate, ReasonAction, Status, money, useAction, useLoad, when } from "./ui";
import styles from "./ControlPlane.module.css";

// --- catalog -----------------------------------------------------------------------

/** Edit how a product is presented and where it is listed (Phase 9). The
 * code is never editable: entitlements, integrations and every historical
 * subscription name the product by it. */
function ProductEditor({
  product,
  busy,
  onSave,
}: {
  product: CatalogProduct;
  busy: boolean;
  onSave: (changes: ProductChanges, message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({
    name: product.name,
    tagline: product.tagline,
    slug: product.slug,
    marketing_url: product.marketing_url,
    listing: product.listing,
    description: product.description,
  });
  const ids = { name: useId(), tagline: useId(), slug: useId(), url: useId(), listing: useId(), description: useId() };
  const field = (key: keyof typeof draft) => ({
    value: draft[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setDraft((d) => ({ ...d, [key]: e.target.value })),
  });
  if (!open) {
    return (
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(true)} aria-label={`Edit ${product.name}`}>
        Edit
      </button>
    );
  }
  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          {
            name: draft.name.trim(),
            tagline: draft.tagline.trim(),
            slug: draft.slug.trim(),
            marketing_url: draft.marketing_url.trim(),
            listing: draft.listing as CatalogProduct["listing"],
            description: draft.description,
          },
          `${draft.name.trim()} saved.`,
        );
        setOpen(false);
      }}
    >
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.name}>
          Name
        </label>
        <input id={ids.name} className="w360-input" required maxLength={128} {...field("name")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.tagline}>
          Tagline
        </label>
        <input id={ids.tagline} className="w360-input" maxLength={160} {...field("tagline")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.slug}>
          Address on the public site
        </label>
        <input id={ids.slug} className="w360-input" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={64} {...field("slug")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.url}>
          Marketing page (https, optional)
        </label>
        <input id={ids.url} className="w360-input" type="url" pattern="https://.*" maxLength={500} {...field("marketing_url")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.listing}>
          Listing
        </label>
        <select id={ids.listing} className="w360-input" {...field("listing")}>
          <option value="listed">Listed - on the public site and in Explore once available</option>
          <option value="coming_soon">Coming soon - announced, not on sale</option>
          <option value="hidden">Hidden - private; sold by staff, partners or contract</option>
        </select>
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.description}>
          Description (light markdown)
        </label>
        <textarea id={ids.description} className="w360-input" rows={5} maxLength={4000} {...field("description")} />
      </div>
      <div className={styles.row}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Save
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// --- the products console (Phase 11) ---------------------------------------------
//
// One place to see a product whole (Step 17/18 of the brief): commercial
// status and technical (integration) status are shown as two separate facts,
// never one gating the other, alongside where it may be sold - direct,
// reseller, affiliate - and its trial configuration. Every figure is read
// from `GET /admin/products/console`; nothing here computes eligibility.

function TrialConfigEditor({
  product,
  busy,
  onSave,
}: {
  product: ConsoleProduct;
  busy: boolean;
  onSave: (config: TrialConfig, message: string) => void;
}) {
  const current = product.channels.trial;
  const [enabled, setEnabled] = useState(current?.enabled ?? false);
  const [days, setDays] = useState(String(current?.duration_days ?? 7));
  const [seatLimit, setSeatLimit] = useState(current?.seat_limit != null ? String(current.seat_limit) : "");
  const [plans, setPlans] = useState<string[]>(current?.eligible_plan_codes ?? []);
  const standardPlans = product.plans.filter((p) => p.kind === "standard");
  const ids = { enabled: useId(), days: useId(), seats: useId() };

  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          {
            enabled,
            duration_days: Number(days),
            seat_limit: seatLimit.trim() ? Number(seatLimit) : null,
            payment_method_required: false,
            eligible_plan_codes: plans,
          },
          `Trial configuration for ${product.name} saved.`,
        );
      }}
    >
      <p className={styles.muted}>
        No default is assumed: this product has no trial until it is enabled here, with at least one eligible plan.
      </p>
      <label className="w360-label">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Trial enabled
      </label>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.days}>
          Duration (days)
        </label>
        <input
          id={ids.days}
          className="w360-input"
          type="number"
          min={1}
          max={365}
          required
          value={days}
          onChange={(e) => setDays(e.target.value)}
        />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.seats}>
          Seat limit (empty = the plan&apos;s own included seats)
        </label>
        <input
          id={ids.seats}
          className="w360-input"
          type="number"
          min={1}
          value={seatLimit}
          onChange={(e) => setSeatLimit(e.target.value)}
        />
      </div>
      <fieldset className={styles.field}>
        <legend className="w360-label">Eligible plans</legend>
        {standardPlans.length === 0 && <p className={styles.muted}>This product has no standard plan yet.</p>}
        {standardPlans.map((plan) => (
          <label key={plan.code} className="w360-label">
            <input
              type="checkbox"
              checked={plans.includes(plan.code)}
              onChange={(e) => setPlans((cur) => (e.target.checked ? [...cur, plan.code] : cur.filter((c) => c !== plan.code)))}
            />
            {" "}
            {plan.name}
            {(!plan.is_public || plan.status !== "available" || !plan.priced) && (
              <span className={styles.muted}> (not yet on sale)</span>
            )}
          </label>
        ))}
      </fieldset>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || (enabled && plans.length === 0)}>
          Save trial configuration
        </button>
      </div>
    </form>
  );
}

function IntegrationEditor({
  integration,
  busy,
  onSave,
}: {
  integration: ConsoleIntegration;
  busy: boolean;
  onSave: (changes: Partial<Pick<ConsoleIntegration, "display_name" | "launch_url" | "health_url" | "contact_email">>, message: string) => void;
}) {
  const [draft, setDraft] = useState({
    display_name: integration.display_name,
    launch_url: integration.launch_url,
    health_url: integration.health_url,
    contact_email: integration.contact_email,
  });
  const ids = { name: useId(), launch: useId(), health: useId(), contact: useId() };
  const field = (key: keyof typeof draft) => ({
    value: draft[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setDraft((d) => ({ ...d, [key]: e.target.value })),
  });
  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft, "Integration updated.");
      }}
    >
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.name}>
          Display name
        </label>
        <input id={ids.name} className="w360-input" required maxLength={128} {...field("display_name")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.launch}>
          Launch URL (https)
        </label>
        <input id={ids.launch} className="w360-input" type="url" required pattern="https://.*" {...field("launch_url")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.health}>
          Health URL (https, optional - the launch URL is checked otherwise)
        </label>
        <input id={ids.health} className="w360-input" type="url" pattern="https://.*" {...field("health_url")} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.contact}>
          Contact email (optional)
        </label>
        <input id={ids.contact} className="w360-input" type="email" {...field("contact_email")} />
      </div>
      <div className={styles.row}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Save
        </button>
      </div>
    </form>
  );
}

function IntegrationPanel({
  code,
  name,
  integration,
  busy,
  manage,
  act,
  reload,
}: {
  code: string;
  name: string;
  integration: ConsoleIntegration | null;
  busy: boolean;
  manage: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  reload: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editingAuth, setEditingAuth] = useState(false);
  const [health, setHealth] = useState<string | null>(null);

  if (!integration) {
    return (
      <div className={styles.stack}>
        <p className={styles.muted}>No integration registered for this product.</p>
        {manage && <RegisterIntegration code={code} name={name} busy={busy} act={act} />}
      </div>
    );
  }
  return (
    <div className={styles.stack}>
      <dl className={styles.factsGrid}>
        <div>
          <dt>Integration status</dt>
          <dd>
            <Status value={integration.status} />
          </dd>
        </div>
        <div>
          <dt>Single sign-on</dt>
          <dd>{integration.sso === "connected" ? "Connected" : "Integration required"}</dd>
        </div>
        <div>
          <dt>Health</dt>
          <dd>
            <Status value={integration.health.status} />
            {integration.health.checked_at && <span className={styles.muted}> · checked {when(integration.health.checked_at)}</span>}
          </dd>
        </div>
        <div>
          <dt>Launch URL</dt>
          <dd className={styles.mono}>{integration.launch_url}</dd>
        </div>
      </dl>
      {integration.status === "suspended" && integration.suspension_reason && (
        <p className={styles.muted}>Suspended: {integration.suspension_reason}</p>
      )}
      {manage && (
        <div className={styles.row}>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={busy}
            onClick={() =>
              void adminApi
                .checkIntegrationHealth(code)
                .then((r) => {
                  setHealth(`${r.status}${r.detail ? ` (${r.detail})` : ""}`);
                  reload();
                })
                .catch(() => setHealth("Could not check right now."))
            }
          >
            Check now
          </button>
          {integration.status === "active" && (
            <ReasonAction
              label="Suspend"
              busy={busy}
              onConfirm={(r) => act(() => adminApi.setIntegrationStatus(code, "suspended", r), "Integration suspended.")}
            />
          )}
          {integration.status === "suspended" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() => act(() => adminApi.setIntegrationStatus(code, "active", ""), "Integration resumed.")}
            >
              Resume
            </button>
          )}
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel" : "Edit"}
          </button>
        </div>
      )}
      {health && <p role="status">{health}</p>}
      {editing && (
        <IntegrationEditor
          integration={integration}
          busy={busy}
          onSave={(changes, message) => {
            void act(() => adminApi.updateIntegration(code, changes), message);
            setEditing(false);
          }}
        />
      )}
      <h3 className={styles.cardTitle}>Authentication</h3>
      <AuthenticationFacts integration={integration} />
      {manage && (
        <div className={styles.row}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setEditingAuth((v) => !v)}>
            {editingAuth ? "Cancel" : "Edit authentication"}
          </button>
          {integration.status === "draft" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() => act(() => adminApi.setIntegrationStatus(code, "active", ""), "Integration activated.")}
            >
              Activate
            </button>
          )}
        </div>
      )}
      {editingAuth && (
        <AuthenticationEditor code={code} integration={integration} busy={busy} act={act} onDone={() => setEditingAuth(false)} />
      )}
      <CredentialsPanel code={code} manage={manage} busy={busy} act={act} />
    </div>
  );
}

function ProductConsoleCard({
  product: p,
  busy,
  act,
  reload,
  manageCatalog,
  manageIntegration,
  canSeeIntegrations,
}: {
  product: ConsoleProduct;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  reload: () => void;
  manageCatalog: boolean;
  manageIntegration: boolean;
  canSeeIntegrations: boolean;
}) {
  const [editingTrial, setEditingTrial] = useState(false);
  const trial = p.channels.trial;

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={`product-${p.id}`}>
      <div className={styles.row}>
        <div>
          <h2 id={`product-${p.id}`} className={styles.cardTitle}>
            {p.name}
          </h2>
          <p className={styles.muted}>
            {p.code}
            {p.category && ` · ${p.category}`}
          </p>
        </div>
        <div className={styles.row}>
          <Status value={p.status} />
          <Status value={p.availability.replace(/_/g, " ")} />
        </div>
      </div>

      <dl className={styles.factsGrid}>
        <div>
          <dt>Direct sale</dt>
          <dd>
            {p.channels.direct.ready ? "Ready" : "Not ready"} ({p.channels.direct.priced_plans} priced of {p.channels.direct.public_plans} public plan(s))
          </dd>
        </div>
        <div>
          <dt>Reseller</dt>
          <dd>{p.channels.reseller.partners} partner(s) granted</dd>
        </div>
        <div>
          <dt>Affiliate</dt>
          <dd>
            {p.channels.affiliate.product_rules} product rule(s)
            {p.channels.affiliate.programme_default && " · programme default applies"}
          </dd>
        </div>
        <div>
          <dt>Trial</dt>
          <dd>
            {trial?.enabled
              ? `${trial.duration_days} days, on · ${trial.eligible_plan_codes.length} eligible plan(s)`
              : trial
                ? `${trial.duration_days} days, off${trial.eligible_plan_codes.length === 0 ? " · switch on once a plan is marked eligible" : ""}`
                : "Not configured"}
          </dd>
        </div>
      </dl>

      {canSeeIntegrations && (
        <IntegrationPanel
          code={p.code}
          name={p.name}
          integration={p.integration}
          busy={busy}
          manage={manageIntegration}
          act={act}
          reload={reload}
        />
      )}

      {manageCatalog && (
        <div className={styles.row}>
          <ProductEditor product={p} busy={busy} onSave={(changes, message) => act(() => adminApi.updateProduct(p.id, changes), message)} />
          {p.status === "draft" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() => act(() => adminApi.setProductStatus(p.id, "available"), "Product is now available to sell.")}
            >
              Make available
            </button>
          )}
          {p.status === "available" && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() => act(() => adminApi.setProductStatus(p.id, "deprecated"), "Product deprecated. Existing subscriptions are unaffected.")}
            >
              Deprecate
            </button>
          )}
          <Link className="w360-btn w360-btn-secondary" href="/admin/entitlements">
            Holders
          </Link>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setEditingTrial((v) => !v)}>
            {editingTrial ? "Cancel" : "Configure trial"}
          </button>
        </div>
      )}
      {editingTrial && (
        <TrialConfigEditor
          product={p}
          busy={busy}
          onSave={(config, message) => {
            void act(() => adminApi.setTrialConfig(p.id, config), message);
            setEditingTrial(false);
          }}
        />
      )}
    </section>
  );
}

/** The Super Admin's Products screen (Step 17/18 of the brief). Composes the
 * catalog (existing), the integration registry (Phase 11) and the reseller
 * and affiliate channel facts, per product - and creates a new SKU, unchanged
 * from before. */
export function ProductsConsole() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminApi.productsConsole(), []);
  const { act, busy, feedback } = useAction(reload);
  const manageCatalog = can(permissions, "platform.staff_catalog.manage");
  const manageIntegration = can(permissions, "platform.staff_integration.manage");
  const codeId = useId();
  const nameId = useId();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  return (
    <div className={styles.stack}>
      <h1>Products</h1>
      <p className={styles.lead}>
        Each product&apos;s commercial status (for sale) and technical status (able to sign users in) are separate
        facts - neither gates the other, and an unhealthy integration never affects Workeasy360 itself.
      </p>
      {feedback}
      <LoadGate load={load} what="products">
        {(d: ProductsConsoleData) => (
          <div className={styles.stack}>
            {d.products.map((p) => (
              <ProductConsoleCard
                key={p.id}
                product={p}
                busy={busy}
                act={act}
                reload={reload}
                manageCatalog={manageCatalog}
                manageIntegration={manageIntegration}
                canSeeIntegrations={d.can_see_integrations}
              />
            ))}
          </div>
        )}
      </LoadGate>
      {manageCatalog && (
        <form
          className={`w360-card ${styles.card} ${styles.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => adminApi.createProduct({ code: code.trim(), name: name.trim() }), "Product created as a draft.");
          }}
        >
          <h2 className={styles.cardTitle}>Add a product SKU</h2>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={codeId}>
              Code
            </label>
            <input id={codeId} className="w360-input" required pattern="[a-z][a-z0-9_]{1,31}" value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={nameId}>
              Name
            </label>
            <input id={nameId} className="w360-input" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !code.trim() || !name.trim()}>
              Create product
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// --- one payout row (Partner Detail -> Payouts) -------------------------------------

/** One payout with its lifecycle controls (approver is never the preparer, and
 * every dangerous step asks for a second factor - all enforced by the server).
 * Exported so a partner's own Payouts tab shows the very same controls rather
 * than a second copy of them. */
export function PayoutRowView({
  payout: p,
  manage,
  approve,
  busy,
  act,
}: {
  payout: PayoutRow;
  manage: boolean;
  approve: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const version = p.version ?? 0;
  const refId = useId();
  const [reference, setReference] = useState("");
  return (
    <tr>
      <th scope="row">{p.period_end}</th>
      <td>
        <Status value={p.status} />
      </td>
      <td className={styles.num}>{money(p.gross_minor, p.currency)}</td>
      <td className={styles.num}>{money(p.adjustments_minor, p.currency)}</td>
      <td className={styles.num}>{money(p.payable_minor, p.currency)}</td>
      <td>{p.line_count}</td>
      <td>
        <div className={styles.stack}>
          {manage && p.status === "draft" && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => adminApi.payoutAction(p.id, "ready", version, {}), "Marked ready for approval.")}>
              Mark ready
            </button>
          )}
          {approve && p.status === "ready" && (
            <ReasonAction label="Approve" busy={busy} onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "approve", version, { reason: r }), "Payout approved. No payment was made.")} />
          )}
          {manage && p.status === "approved" && (
            <form
              className={styles.field}
              onSubmit={(e) => {
                e.preventDefault();
                void act(() => adminApi.payoutAction(p.id, "paid", version, { reference: reference.trim() }), "Recorded as paid.");
              }}
            >
              <label className="w360-label" htmlFor={refId}>
                Transfer reference (no bank details)
              </label>
              <input id={refId} className="w360-input" required minLength={3} maxLength={64} value={reference} onChange={(e) => setReference(e.target.value)} />
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || reference.trim().length < 3}>
                Record as paid
              </button>
            </form>
          )}
          {manage && p.status !== "paid" && p.status !== "cancelled" && (
            <ReasonAction label="Cancel payout" busy={busy} onConfirm={(r) => act(() => adminApi.payoutAction(p.id, "cancel", version, { reason: r }), "Payout cancelled; its entries are payable again.")} />
          )}
        </div>
      </td>
    </tr>
  );
}
