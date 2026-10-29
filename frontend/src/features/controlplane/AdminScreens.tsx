"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi, type CustomerDetail, type Diagnosis } from "@/lib/api/controlPlane";
import { adminConsoleApi } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { CustomerBilling } from "./AdminBilling";
import { StaffRolesPanel } from "./AdminCommerce";
import { LoadGate, MoreButton, ReasonAction, Status, Table, autoReason, useAction, useLoad, usePaged, when } from "./ui";
import styles from "./ControlPlane.module.css";

// --- customers --------------------------------------------------------------------

export function Customers() {
  const searchId = useId();
  const [draft, setDraft] = useState("");
  // The query is applied on submit, not per keystroke: one bounded request per search.
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const paged = usePaged((cursor) => adminApi.customers({ q: query || undefined, status: status || undefined, cursor }), [query, status]);

  return (
    <div className={styles.stack}>
      <h1>Customers</h1>
      <form
        className={styles.row}
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <label className="w360-label" htmlFor={searchId}>
          Search by name or slug
        </label>
        <input id={searchId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={100} />
        <label className="w360-label" htmlFor={`${searchId}-status`}>
          Status
        </label>
        <select id={`${searchId}-status`} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </select>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {paged.state === "loading" && <p aria-live="polite">Loading customers…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to the customer directory.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load customers.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="Customer organizations"
            head={["Organization", "Status", "Subscription", "Products", "Seats", "Partner", "Created"]}
            isEmpty={paged.rows.length === 0}
            empty="No customers match."
          >
            {paged.rows.map((c) => (
              <tr key={c.organization_id}>
                <th scope="row">
                  <Link href={`/admin/customers/${c.organization_id}`}>{c.display_name}</Link>
                  <div className={styles.muted}>{c.owner_email || "no owner yet"}</div>
                </th>
                <td>
                  <Status value={c.status} />
                </td>
                <td>
                  <Status value={c.subscription_status} />
                </td>
                <td>{c.products.join(", ") || "—"}</td>
                <td>{c.seat_capacity ?? "—"}</td>
                <td>{c.partner ? `${c.partner.partner_name} (${c.partner.link_status})` : "Direct"}</td>
                <td>{when(c.created_at)}</td>
              </tr>
            ))}
          </Table>
          <p className={styles.muted}>Subscription, products and seats reflect the last reconciliation.</p>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

export function CustomerDetailScreen({ organizationId }: { organizationId: string }) {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminApi.customer(organizationId), [organizationId]);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_customer.manage");
  const subs = can(permissions, "platform.staff_subscription.manage");
  const payments = can(permissions, "platform.staff_payment.view");

  return (
    <div className={styles.stack}>
      <LoadGate load={load} what="this customer">
        {(d) => (
          <>
            <h1>
              {d.organization.display_name} <Status value={d.organization.status} />
            </h1>
            <p className={styles.muted}>
              {d.organization.slug} · created {when(d.organization.created_at)}
              {d.organization.suspension_reason && ` · suspended: ${d.organization.suspension_reason}`}
            </p>
            {feedback}
            {manage && (
              <div className={styles.row}>
                {d.organization.status === "active" ? (
                  <ReasonAction
                    label="Suspend organization"
                    busy={busy}
                    onConfirm={(reason) => act(() => adminApi.suspendCustomer(organizationId, reason), "Organization suspended.")}
                  />
                ) : (
                  <ReasonAction
                    label="Reactivate organization"
                    busy={busy}
                    onConfirm={(reason) => act(() => adminApi.reactivateCustomer(organizationId, reason), "Organization reactivated.")}
                  />
                )}
                <ReasonAction
                  label="Reset network allow-list"
                  busy={busy}
                  onConfirm={(reason) =>
                    act(
                      () => adminConsoleApi.resetNetworkPolicy(organizationId, reason),
                      "The IP allow-list is off: members can sign in from any network again.",
                    )
                  }
                />
              </div>
            )}

            <section className={`w360-card ${styles.card}`} aria-labelledby="subs">
              <h2 id="subs" className={styles.cardTitle}>
                Subscription
              </h2>
              <SubscriptionPanel detail={d} canManage={subs} busy={busy} act={act} organizationId={organizationId} />
            </section>

            <section className={`w360-card ${styles.card}`} aria-labelledby="ent">
              <h2 id="ent" className={styles.cardTitle}>
                Entitlements and seats
              </h2>
              <Table
                caption="Products this customer holds"
                head={["Product", "Status", "Seats", "Assigned", "Over limit"]}
                isEmpty={Object.keys(d.entitlements).length === 0}
                empty="No entitlements."
              >
                {Object.entries(d.entitlements).map(([code, e]) => {
                  const lic = d.licenses.find((l) => l.product_code === code);
                  return (
                    <tr key={code}>
                      <th scope="row">{code}</th>
                      <td>
                        <Status value={e.status} />
                      </td>
                      <td>{lic?.seats_total ?? "—"}</td>
                      <td>{lic?.seats_assigned ?? "—"}</td>
                      <td>{lic?.over_limit ?? 0}</td>
                    </tr>
                  );
                })}
              </Table>
              <p className={styles.muted}>Entitlements are rebuilt from subscriptions; staff overrides are time-boxed grants.</p>
              <OverridePanel detail={d} canManage={subs} busy={busy} act={act} organizationId={organizationId} />
            </section>

            <section className={`w360-card ${styles.card}`} aria-labelledby="meta">
              <h2 id="meta" className={styles.cardTitle}>
                Members, billing and partner
              </h2>
              <p>
                Members: {Object.entries(d.members).map(([k, v]) => `${v} ${k}`).join(", ")}
              </p>
              <p>
                Partner:{" "}
                {d.partner ? (
                  <>
                    <Link href={`/admin/partners/${d.partner.partner_id}`}>{d.partner.display_name}</Link>{" "}
                    <Status value={d.partner.link_status} />
                  </>
                ) : (
                  "none (direct customer)"
                )}
              </p>
              {d.billing ? (
                <p>
                  Billing: {d.billing.currency}, {d.billing.collection_mode}
                  {d.billing.billing_region && `, ${d.billing.billing_region}`}. <em>{d.billing.note}</em>
                </p>
              ) : (
                <p className={styles.muted}>No billing account, or you don&apos;t hold billing access.</p>
              )}
            </section>

            {payments && <CustomerBilling organizationId={organizationId} />}

            <section className={`w360-card ${styles.card}`} aria-labelledby="diag">
              <h2 id="diag" className={styles.cardTitle}>
                Why can&apos;t someone launch a product?
              </h2>
              <DiagnosticForm organizationId={organizationId} />
            </section>

            <section className={`w360-card ${styles.card}`} aria-labelledby="aud">
              <h2 id="aud" className={styles.cardTitle}>
                Recent audit trail
              </h2>
              <Table
                caption="This customer's own audit log"
                head={["When", "Action", "By", "Outcome"]}
                isEmpty={d.audit.length === 0}
                empty="No entries."
              >
                {d.audit.map((a) => (
                  <tr key={a.id}>
                    <td>{when(a.occurred_at)}</td>
                    <td>{a.action}</td>
                    <td>{a.actor_kind}</td>
                    <td>{a.outcome}</td>
                  </tr>
                ))}
              </Table>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}

const OVERRIDE_DAYS = [7, 30, 90];

function OverridePanel({
  detail,
  canManage,
  busy,
  act,
  organizationId,
}: {
  detail: CustomerDetail;
  canManage: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  organizationId: string;
}) {
  const kindId = useId();
  const codeId = useId();
  const daysId = useId();
  const [kind, setKind] = useState<"product_code" | "feature_key">("product_code");
  const [code, setCode] = useState("");
  const [days, setDays] = useState(30);

  return (
    <div className={styles.stack}>
      <h3 className={styles.cardTitle}>Staff overrides</h3>
      <Table
        caption="Active staff overrides"
        head={["Grants", "Reason", "Expires", ""]}
        isEmpty={detail.entitlement_overrides.length === 0}
        empty="No overrides."
      >
        {detail.entitlement_overrides.map((o) => (
          <tr key={o.id}>
            <th scope="row">{o.product_code || o.feature_key}</th>
            <td>{o.reason}</td>
            <td>{when(o.expires_at)}</td>
            <td>
              {canManage && (
                <ReasonAction
                  label="Revoke"
                  busy={busy}
                  onConfirm={(r) => act(() => adminApi.revokeOverride(organizationId, o.id, r), "Override revoked; access was recomputed.")}
                />
              )}
            </td>
          </tr>
        ))}
      </Table>
      {canManage && (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault();
            const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
            void act(async () => {
              await adminApi.grantOverride(organizationId, { [kind]: code.trim(), reason: autoReason("Grant override"), expires_at: expires });
              setCode("");
            }, "Override granted; access was recomputed.");
          }}
        >
          <div className={styles.field}>
            <label className="w360-label" htmlFor={kindId}>
              Grant access to
            </label>
            <select id={kindId} className="w360-input" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option value="product_code">A product</option>
              <option value="feature_key">A feature</option>
            </select>
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={codeId}>
              {kind === "product_code" ? "Product code" : "Feature key"}
            </label>
            <input id={codeId} className="w360-input" required value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={daysId}>
              Expires after
            </label>
            <select id={daysId} className="w360-input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {OVERRIDE_DAYS.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !code.trim()}>
              Grant override
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function SubscriptionPanel({
  detail,
  canManage,
  busy,
  act,
  organizationId,
}: {
  detail: CustomerDetail;
  canManage: boolean;
  busy: boolean;
  act: (action: () => Promise<unknown>, success: string) => Promise<void>;
  organizationId: string;
}) {
  if (detail.subscriptions.length === 0) return <p className={styles.muted}>No subscription.</p>;
  return (
    <>
      {detail.subscriptions.map((s) => (
        <div key={s.id} className={styles.stack}>
          <p>
            <Status value={s.status} /> · {s.currency} · {s.billing_interval} · period ends {when(s.current_period_end)}
            {s.cancel_at && ` · cancels ${when(s.cancel_at)}`}
          </p>
          <Table
            caption="Subscription lines"
            head={["Product", "Plan", "Quantity", "Closed"]}
            isEmpty={s.items.length === 0}
          >
            {s.items.map((i) => (
              <tr key={i.id}>
                <td>{i.product_code || i.bundle_code}</td>
                <td>{i.plan_code || "bundle"}</td>
                <td>{i.quantity}</td>
                <td>{i.removed_at ? when(i.removed_at) : "—"}</td>
              </tr>
            ))}
          </Table>
          {canManage && (
            <div className={styles.row}>
              {s.status === "suspended" ? (
                <ReasonAction
                  label="Resume subscription"
                  busy={busy}
                  onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "resume", r, s.version), "Subscription resumed.")}
                />
              ) : (
                <ReasonAction
                  label="Suspend subscription"
                  busy={busy}
                  onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "suspend", r, s.version), "Subscription suspended.")}
                />
              )}
              <ReasonAction
                label="Cancel at period end"
                busy={busy}
                onConfirm={(r) => act(() => adminApi.subscriptionAction(organizationId, s.id, "cancel", r, s.version), "Cancellation scheduled.")}
              />
            </div>
          )}
        </div>
      ))}
    </>
  );
}

function DiagnosticForm({ organizationId }: { organizationId: string }) {
  const productId = useId();
  const emailId = useId();
  const [product, setProduct] = useState("");
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await adminApi.diagnose(organizationId, product.trim(), email.trim() || undefined));
    } catch {
      setResult(null);
      setError("The diagnostic could not be run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className={styles.form} onSubmit={submit}>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={productId}>
            Product code
          </label>
          <input id={productId} className="w360-input" required value={product} onChange={(e) => setProduct(e.target.value)} maxLength={32} />
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={emailId}>
            Member&apos;s email (optional)
          </label>
          <input id={emailId} className="w360-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} />
        </div>
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !product.trim()}>
            Run read-only diagnostic
          </button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && <DiagnosticResult result={result} />}
    </>
  );
}

export function DiagnosticResult({ result }: { result: Diagnosis }) {
  return (
    <div aria-live="polite">
      <p>
        <strong>{result.can_launch ? "Nothing blocks a launch." : `Blocked at: ${result.blocking_step}.`}</strong>{" "}
        <span className={styles.muted}>Read-only — nothing was changed.</span>
      </p>
      <ol className={styles.chain}>
        {result.chain.map((s) => (
          <li key={s.step} className={styles.chainItem}>
            <span className={s.ok ? "w360-badge w360-badge-success" : "w360-badge w360-badge-danger"}>{s.ok ? "ok" : "blocked"}</span>
            <span>
              {s.step}: <code>{s.code}</code>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// --- support ---------------------------------------------------------------------------

export function SupportScreen() {
  const id = useId();
  const [orgId, setOrgId] = useState("");
  const valid = /^[0-9a-f-]{36}$/i.test(orgId.trim());
  return (
    <div className={styles.stack}>
      <h1>Support diagnostics</h1>
      <p className={styles.lead}>
        Run the same read-only reason chain from the customer page for an organization id. It reports state, never a
        secret, and changes nothing.
      </p>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={id}>
          Organization id
        </label>
        <input id={id} className="w360-input" value={orgId} onChange={(e) => setOrgId(e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" />
      </div>
      {valid && (
        <section className={`w360-card ${styles.card}`}>
          <DiagnosticForm organizationId={orgId.trim()} />
        </section>
      )}
    </div>
  );
}

// --- audit ------------------------------------------------------------------------------

export function AuditScreen() {
  const [prefix, setPrefix] = useState("");
  const id = useId();
  const paged = usePaged((cursor) => adminApi.audit(cursor, prefix || undefined), [prefix]);
  return (
    <div className={styles.stack}>
      <h1>Control-plane audit</h1>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={id}>
          Filter by action prefix (e.g. payout., partner.)
        </label>
        <input id={id} className="w360-input" value={prefix} onChange={(e) => setPrefix(e.target.value)} maxLength={64} />
      </div>
      {paged.state === "loading" && <p aria-live="polite">Loading…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to the audit trail.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load the audit trail.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Control-plane audit entries" head={["When", "Action", "By", "Resource", "Reason"]} isEmpty={paged.rows.length === 0}>
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.occurred_at)}</td>
                <td>{a.action}</td>
                <td>{a.actor_kind}</td>
                <td className={styles.mono}>{a.resource_type}</td>
                <td>{a.reason}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- security ---------------------------------------------------------------------------

export function SecurityScreen() {
  const { permissions } = useAdmin();
  const [load, reload] = useLoad(() => adminApi.securityGrants(), []);
  const { act, busy, feedback } = useAction(reload);
  const manage = can(permissions, "platform.staff_security.manage");
  const emailId = useId();
  const permId = useId();
  const expiresId = useId();
  const [email, setEmail] = useState("");
  const [permission, setPermission] = useState("platform.staff_customer.view");
  // Optional: the last moment of the chosen (local) day. Empty means no expiry.
  const [expiresOn, setExpiresOn] = useState("");
  const [today] = useState(() => new Date().toISOString().slice(0, 10));

  return (
    <div className={styles.stack}>
      <h1>Staff access</h1>
      <StaffRolesPanel />
      <h2>Individual permissions</h2>
      <p className={styles.lead}>
        Who holds which <code>platform.staff_*</code> permission. Grants take effect and end immediately, a time-boxed
        grant ends on its own when it expires, and the last permanent security administrator cannot be removed.
      </p>
      {feedback}
      <LoadGate load={load} what="permissions">
        {(d) => (
          <Table caption="Active grants" head={["Holder", "Permission", "Granted", "Expires", "Reason", ""]} isEmpty={d.results.length === 0}>
            {d.results.map((g) => (
              <tr key={g.id}>
                <th scope="row">{g.email}</th>
                <td className={styles.mono}>{g.permission}</td>
                <td>{when(g.granted_at)}</td>
                <td>{g.expires_at ? when(g.expires_at) : "No expiry"}</td>
                <td>{g.reason}</td>
                <td>
                  {manage && (
                    <ReasonAction label="Revoke" busy={busy} onConfirm={(r) => act(() => adminApi.revokePermission(g.id, r), "Permission revoked.")} />
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {manage && (
        <form
          className={`w360-card ${styles.card} ${styles.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            const expiresAt = expiresOn ? new Date(`${expiresOn}T23:59:59`).toISOString() : undefined;
            void act(
              () => adminApi.grantPermission(email.trim(), permission.trim(), autoReason("Grant permission"), expiresAt),
              "Permission granted.",
            );
          }}
        >
          <h2 className={styles.cardTitle}>Grant a permission</h2>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={emailId}>
              Staff member&apos;s email
            </label>
            <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={permId}>
              Permission
            </label>
            <input id={permId} className="w360-input" required value={permission} onChange={(e) => setPermission(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={expiresId}>
              Expires on (optional)
            </label>
            <input id={expiresId} className="w360-input" type="date" min={today} value={expiresOn} onChange={(e) => setExpiresOn(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
              Grant
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
