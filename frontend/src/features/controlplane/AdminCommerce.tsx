"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi, type CatalogProduct } from "@/lib/api/controlPlane";
import { adminConsoleApi, type PlatformSettings, type StaffRoleRow } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, MoreButton, ReasonAction, Status, Table, money, useAction, useLoad, usePaged, when } from "./ui";
import cp from "./ControlPlane.module.css";

/**
 * Super Admin → Commerce and Access (Phase 9): every order across customers,
 * customers by subscription state, who holds each product, the platform
 * settings and named staff roles. Reads only what the grants allow; every
 * change is re-authorized (and, where dangerous, stepped up and audited) by the
 * server.
 */

function Paged<T>({
  paged,
  what,
  children,
}: {
  paged: ReturnType<typeof usePaged<T>>;
  what: string;
  children: (rows: T[]) => React.ReactNode;
}) {
  if (paged.state === "loading") return <p aria-live="polite">Loading {what}…</p>;
  if (paged.state === "forbidden") return <p role="alert">You don&apos;t have access to {what}.</p>;
  if (paged.state === "error") return <p role="alert">Couldn&apos;t load {what}.</p>;
  return (
    <>
      {children(paged.rows)}
      <MoreButton hasMore={paged.hasMore} onMore={() => void paged.more()} />
    </>
  );
}

function Tabs({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className={cp.row} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.key || "all"}
          type="button"
          className={value === o.key ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// --- orders ---------------------------------------------------------------------------------

const ORDER_TABS = [
  { key: "", label: "All" },
  { key: "pending_payment", label: "Awaiting payment" },
  { key: "fulfilled", label: "Fulfilled" },
  { key: "failed", label: "Failed" },
  { key: "expired", label: "Expired" },
  { key: "cancelled", label: "Cancelled" },
] as const;

export function StaffOrdersScreen() {
  const [status, setStatus] = useState("");
  const paged = usePaged((cursor) => adminConsoleApi.orders({ status: status || undefined, cursor }), [status]);
  return (
    <div className={cp.stack}>
      <div>
        <h1>Orders</h1>
        <p className={cp.lead}>Every order across customers, newest first. Payment confirms an order; nothing is granted before.</p>
      </div>
      <Tabs label="Order status" options={ORDER_TABS} value={status} onChange={setStatus} />
      <Paged paged={paged} what="orders">
        {(rows) => (
          <Table caption="Orders" head={["Order", "Customer", "Item", "Amount", "Status", "Placed", "Paid"]} isEmpty={rows.length === 0} empty="No orders.">
            {rows.map((o) => (
              <tr key={o.order_id}>
                <th scope="row">{o.number}</th>
                <td>
                  <Link href={`/admin/customers/${o.organization_id}`}>{o.organization_name || o.organization_id}</Link>
                </td>
                <td>{o.description}</td>
                <td className={cp.num}>{o.amount_due_minor ? money(o.amount_due_minor, o.currency) : "—"}</td>
                <td>
                  <Status value={o.status.replace(/_/g, " ")} />
                </td>
                <td>{when(o.created_at)}</td>
                <td>{o.paid_at ? when(o.paid_at) : "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Paged>
    </div>
  );
}

// --- subscriptions ------------------------------------------------------------------------------

const SUBSCRIPTION_TABS = [
  { key: "", label: "All" },
  { key: "active", label: "Active" },
  { key: "trialing", label: "Trial" },
  { key: "past_due", label: "Past due" },
  { key: "grace", label: "Grace" },
  { key: "suspended", label: "Suspended" },
  { key: "cancelled", label: "Cancelled" },
  { key: "none", label: "No subscription" },
] as const;

/** Customers by subscription state - the customer directory, filtered. */
export function SubscriptionsScreen() {
  const [status, setStatus] = useState("active");
  const paged = usePaged((cursor) => adminApi.customers({ subscription: status || undefined, cursor }), [status]);
  return (
    <div className={cp.stack}>
      <div>
        <h1>Subscriptions</h1>
        <p className={cp.lead}>Customers by the state of their subscription. Open a customer to act on it.</p>
      </div>
      <Tabs label="Subscription status" options={SUBSCRIPTION_TABS} value={status} onChange={setStatus} />
      <Paged paged={paged} what="subscriptions">
        {(rows) => (
          <Table caption="Customers" head={["Customer", "Owner", "Subscription", "Products", "Seats"]} isEmpty={rows.length === 0} empty="No customers.">
            {rows.map((c) => (
              <tr key={c.organization_id}>
                <th scope="row">
                  <Link href={`/admin/customers/${c.organization_id}`}>{c.display_name}</Link>
                </th>
                <td>{c.owner_email}</td>
                <td>{c.subscription_status ? <Status value={c.subscription_status} /> : "—"}</td>
                <td>{c.products.length ? c.products.join(", ") : "—"}</td>
                <td className={cp.num}>{c.seat_capacity ?? "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Paged>
    </div>
  );
}

// --- entitlements ------------------------------------------------------------------------------

/** Who holds each product now. Changing one customer's access - an override -
 * is on the customer's page, audited with a reason. */
export function EntitlementsScreen() {
  const selectId = useId();
  const [products] = useLoad(() => adminApi.products(), []);
  const [chosen, setChosen] = useState("");
  return (
    <div className={cp.stack}>
      <div>
        <h1>Entitlements</h1>
        <p className={cp.lead}>
          Which customers hold a product now. Entitlements are derived from subscriptions and support overrides; grant
          or revoke an override on the customer&apos;s page.
        </p>
      </div>
      <LoadGate load={products} what="products">
        {({ results }: { results: CatalogProduct[] }) => {
          const code = chosen || results[0]?.code || "";
          return (
            <>
              <div className={cp.row}>
                <label className="w360-label" htmlFor={selectId}>
                  Product
                </label>
                <select id={selectId} className="w360-input" value={code} onChange={(e) => setChosen(e.target.value)}>
                  {results.map((p) => (
                    <option key={p.code} value={p.code}>
                      {p.name} ({p.code})
                    </option>
                  ))}
                </select>
              </div>
              {code && <Holders code={code} />}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

function Holders({ code }: { code: string }) {
  const paged = usePaged((cursor) => adminApi.customers({ product: code, cursor }), [code]);
  return (
    <Paged paged={paged} what="holders">
      {(rows) => (
        <Table caption={`Customers holding ${code}`} head={["Customer", "Access", "Subscription", "Seats"]} isEmpty={rows.length === 0} empty="Nobody holds this product.">
          {rows.map((c) => (
            <tr key={c.organization_id}>
              <th scope="row">
                <Link href={`/admin/customers/${c.organization_id}`}>{c.display_name}</Link>
              </th>
              <td>{c.access_class || "—"}</td>
              <td>{c.subscription_status ? <Status value={c.subscription_status} /> : "Override only"}</td>
              <td className={cp.num}>{c.seat_capacity ?? "—"}</td>
            </tr>
          ))}
        </Table>
      )}
    </Paged>
  );
}

// --- platform settings ---------------------------------------------------------------------------

export function PlatformSettingsScreen() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminConsoleApi.settings(), []);
  const manage = can(permissions, "platform.staff_settings.manage");
  return (
    <div className={cp.stack}>
      <div>
        <h1>Platform settings</h1>
        <p className={cp.lead}>
          The public site&apos;s switches and addresses. Changing them needs a second factor and is recorded in the audit
          log.
        </p>
      </div>
      <LoadGate load={load} what="platform settings">
        {(s) => <SettingsForm key={s.version} settings={s} manage={manage} onSaved={reload} />}
      </LoadGate>
    </div>
  );
}

function SettingsForm({ settings, manage, onSaved }: { settings: PlatformSettings; manage: boolean; onSaved: () => void }) {
  const { act, busy, feedback } = useAction(onSaved);
  const [signup, setSignup] = useState(settings.signup_enabled);
  const [support, setSupport] = useState(settings.support_email);
  const [sales, setSales] = useState(settings.sales_email);
  const [currency, setCurrency] = useState(settings.public_catalog_currency);
  const ids = { signup: useId(), support: useId(), sales: useId(), currency: useId() };
  return (
    <form
      className={`w360-card ${cp.card} ${cp.form}`}
      onSubmit={(e) => {
        e.preventDefault();
        void act(
          () =>
            adminConsoleApi.saveSettings({
              signup_enabled: signup,
              support_email: support.trim(),
              sales_email: sales.trim(),
              public_catalog_currency: currency,
              version: settings.version,
            }),
          "Platform settings saved.",
        );
      }}
    >
      {feedback}
      <div className={cp.field}>
        <label htmlFor={ids.signup}>
          <input id={ids.signup} type="checkbox" checked={signup} disabled={!manage} onChange={(e) => setSignup(e.target.checked)} />{" "}
          Self-service signup is open
        </label>
        <p className="w360-hint">Close it to stop new accounts during abuse; existing accounts are unaffected.</p>
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.support}>
          Support email
        </label>
        <input id={ids.support} className="w360-input" type="email" maxLength={254} value={support} disabled={!manage} onChange={(e) => setSupport(e.target.value)} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.sales}>
          Sales email
        </label>
        <input id={ids.sales} className="w360-input" type="email" maxLength={254} value={sales} disabled={!manage} onChange={(e) => setSales(e.target.value)} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.currency}>
          Public catalog currency
        </label>
        <select id={ids.currency} className="w360-input" value={currency} disabled={!manage} onChange={(e) => setCurrency(e.target.value)}>
          <option value="">Deployment default ({settings.default_currency})</option>
          {settings.currencies.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      {manage && (
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
            Save settings
          </button>
        </div>
      )}
    </form>
  );
}

// --- staff roles -----------------------------------------------------------------------------------

/** Named roles over the one grant model: granting a role grants each of its
 * permissions; who holds a role is derived from the grants in force. */
export function StaffRolesPanel() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminConsoleApi.roles(), []);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_security.manage");
  const emailId = useId();
  const roleId = useId();
  const reasonId = useId();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("SUPPORT_ADMIN");
  const [reason, setReason] = useState("");

  return (
    <section className={cp.stack} aria-labelledby={`${roleId}-title`}>
      <h2 id={`${roleId}-title`}>Staff roles</h2>
      <p className={cp.lead}>Least privilege: give each person the narrowest role that covers their work.</p>
      {feedback}
      <LoadGate load={load} what="staff roles">
        {({ results }: { results: StaffRoleRow[] }) => (
          <Table caption="Roles and who holds them" head={["Role", "What it covers", "Holders"]} isEmpty={results.length === 0}>
            {results.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.name}</th>
                <td>
                  {r.description}
                  <div className={cp.muted}>{r.permissions.length} permissions</div>
                </td>
                <td>
                  {r.holders.length === 0
                    ? "—"
                    : r.holders.map((h) => (
                        <div key={h.user_id}>
                          {h.email}
                          {manage && (
                            <ReasonAction
                              label={`Remove ${r.name}`}
                              busy={busy}
                              onConfirm={(why) =>
                                act(() => adminConsoleApi.revokeRole({ user_id: h.user_id, role: r.key, reason: why }), `${r.name} removed.`)
                              }
                            />
                          )}
                        </div>
                      ))}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {manage && (
        <form
          className={`w360-card ${cp.card} ${cp.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => adminConsoleApi.grantRole({ email: email.trim(), role, reason: reason.trim() }), "Role granted.");
          }}
        >
          <h3 className={cp.cardTitle}>Grant a role</h3>
          <div className={cp.field}>
            <label className="w360-label" htmlFor={emailId}>
              Staff member&apos;s email
            </label>
            <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className={cp.field}>
            <label className="w360-label" htmlFor={roleId}>
              Role
            </label>
            <select id={roleId} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="SUPER_ADMIN">Super Admin</option>
              <option value="USER_ADMIN">User Admin</option>
              <option value="PRODUCT_ADMIN">Product Admin</option>
              <option value="BILLING_ADMIN">Billing Admin</option>
              <option value="SUPPORT_ADMIN">Support Admin</option>
              <option value="READ_ONLY_ADMIN">Read-only Admin</option>
            </select>
          </div>
          <div className={cp.field}>
            <label className="w360-label" htmlFor={reasonId}>
              Reason
            </label>
            <input id={reasonId} className="w360-input" required minLength={10} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !email.trim() || reason.trim().length < 10}>
              Grant role
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
