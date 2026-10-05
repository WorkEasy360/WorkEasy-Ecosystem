"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import { accountApi, consoleApi, type Order, type OrderRequest } from "@/lib/api/console";
import { newKey } from "@/lib/api/controlPlane";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { AppCard } from "@/features/launcher/AppCard";
import { LoadGate, MoreButton, Status, Table, describeError, money, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { useSiteSettings } from "@/features/site/SiteShell";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { OpenAccountForm, usePayInvoice } from "./BillingScreen";
import { AdminInsights } from "./DashboardScreen";
import { PageHeader, Panel, UUID_RE, intervalLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * The customer's side of the ecosystem (Phase 9): the dashboard every member
 * lands on, My products, the checkout that turns a choice into a paid order,
 * the order history and the support page.
 *
 * Nothing here decides access or price. What a member may open comes from
 * the launcher's decision (re-checked at launch); what an order costs comes
 * from the server's quote (re-derived when it is placed); whether it was paid
 * comes from the provider's signed callback.
 */

// --- my products ----------------------------------------------------------------------

const HELD = new Set<AppDirectoryEntry["state"]>(["active", "no_seat", "integration_suspended"]);

/** The organization's products: what it holds and what it could add. One
 * request - the directory already carries the plan, seats, trial and renewal
 * a member is allowed to see (Phase 11, `launcher_views._card_extras`); this
 * screen no longer needs its own subscriptions call to learn a plan name. */
function useProducts(slug: string) {
  return useLoad(async () => {
    const { apps } = await getAppDirectory(slug);
    return {
      held: apps.filter((a) => HELD.has(a.state)),
      other: apps.filter((a) => a.state === "no_entitlement"),
    };
  }, [slug]);
}

function ProductGrid({ slug, rows, manageHref }: { slug: string; rows: AppDirectoryEntry[]; manageHref?: string }) {
  const { can } = useOrgAccess();
  const seatsHref = can("platform.seat.assign") ? `/o/${slug}/settings/products` : undefined;
  return (
    <div className={local.appGrid}>
      {rows.map((app) => (
        <AppCard key={app.product_code} orgSlug={slug} app={app} manageHref={manageHref} seatsHref={seatsHref} />
      ))}
    </div>
  );
}

/** My products: only what the organization holds, each with Open (when the
 * member may) and - for those who manage it - the subscription. */
export function MyProductsScreen({ banner }: { banner?: React.ReactNode }) {
  const { slug, can } = useOrgAccess();
  const [load] = useProducts(slug);
  const manage = can("commercial.subscription.view") ? `/o/${slug}/settings/billing` : undefined;
  return (
    <div className={styles.page}>
      {banner}
      <PageHeader
        title="My products"
        lead="The products your organization has. Open any you have access to."
        actions={
          <Link className="w360-btn w360-btn-secondary" href={`/o/${slug}/explore`}>
            Explore products
          </Link>
        }
      />
      <LoadGate load={load} what="your products">
        {({ held }) =>
          held.length === 0 ? (
            <div className={local.notice}>
              <p>Your organization doesn&apos;t have any products yet.</p>
              <p>
                <Link href={`/o/${slug}/explore`}>Explore products</Link> to buy one or start a free trial.
              </p>
            </div>
          ) : (
            <ProductGrid slug={slug} rows={held} manageHref={manage} />
          )
        }
      </LoadGate>
    </div>
  );
}

// --- the dashboard (everyone's home) ---------------------------------------------------

/** Everyone's landing page (Phase 9, owner decision): welcome, My products
 * first, the subscription and trials for those who may see them, what else
 * could be added, the person's own recent account activity - and, for
 * owners and administrators, the organization's insights below. */
export function HomeScreen() {
  const { slug, access, can, memberName } = useOrgAccess();
  const canSeePlans = can("commercial.subscription.view");
  const [products] = useProducts(slug);
  const [activity] = useLoad(() => accountApi.activity(), []);
  const [subscriptions] = useLoad(
    () => (canSeePlans ? consoleApi.subscriptions(slug).then((r) => r.results) : Promise.resolve(null)),
    [slug, canSeePlans],
  );
  // From the shell's session (2026-10 audit): a second GET /auth/session here
  // only fetched the greeting's first name.
  const name = memberName?.split(" ")[0];

  return (
    <div className={styles.page}>
      <PageHeader
        title={name ? `Welcome, ${name}` : "Welcome"}
        lead={access?.organization.display_name}
        actions={
          <Link className="w360-btn w360-btn-primary" href={`/o/${slug}/explore`}>
            Explore products
          </Link>
        }
      />
      <Panel title="My products">
        <LoadGate load={products} what="your products">
          {({ held }) =>
            held.length === 0 ? (
              <p className={cp.muted}>
                Nothing here yet. <Link href={`/o/${slug}/explore`}>Explore products</Link> to buy one or start a free
                trial.
              </p>
            ) : (
              <ProductGrid
                slug={slug}
                rows={held.slice(0, 6)}
                manageHref={canSeePlans ? `/o/${slug}/settings/billing` : undefined}
              />
            )
          }
        </LoadGate>
      </Panel>

      <div className={styles.split}>
        {canSeePlans && (
          <Panel title="Subscriptions and trials">
            <LoadGate load={subscriptions} what="your subscription">
              {(subs) => {
                const open = (subs ?? []).filter((s) => s.status !== "expired");
                if (open.length === 0) return <p className={cp.muted}>No active subscription.</p>;
                return (
                  <ul className={local.includes}>
                    {open.map((s) => (
                      <li key={s.id}>
                        <Status value={s.status} /> {intervalLabel(s.billing_interval)} ·{" "}
                        {s.items.filter((i) => !i.removed_at && i.product_code).length} product(s)
                        {s.status === "trialing" && s.trial_ends_at && <> · trial ends {when(s.trial_ends_at)}</>}
                      </li>
                    ))}
                  </ul>
                );
              }}
            </LoadGate>
            <p>
              <Link href={`/o/${slug}/settings/billing`}>Subscription & billing</Link>
              {" · "}
              <Link href={`/o/${slug}/orders`}>Orders</Link>
            </p>
          </Panel>
        )}
        <Panel title="Available products">
          <LoadGate load={products} what="products">
            {({ other }) =>
              other.length === 0 ? (
                <p className={cp.muted}>Your organization has every product available.</p>
              ) : (
                <ul className={local.includes}>
                  {other.slice(0, 5).map((a) => (
                    <li key={a.product_code}>{a.display_name}</li>
                  ))}
                </ul>
              )
            }
          </LoadGate>
          <p>
            <Link href={`/o/${slug}/explore`}>See plans and prices</Link>
          </p>
        </Panel>
        <Panel title="Recent activity">
          <LoadGate load={activity} what="your recent activity">
            {({ events }) =>
              events.length === 0 ? (
                <p className={cp.muted}>Nothing yet.</p>
              ) : (
                <ul className={local.includes}>
                  {events.slice(0, 5).map((e) => (
                    <li key={`${e.kind}-${e.occurred_at}`}>
                      {e.kind.replace(/_/g, " ")} · {when(e.occurred_at)}
                    </li>
                  ))}
                </ul>
              )
            }
          </LoadGate>
        </Panel>
      </div>

      {access?.console === "admin" && (
        <section aria-labelledby="org-insights">
          <h2 id="org-insights" className={local.sectionTitle}>
            Your organization
          </h2>
          <AdminInsights />
        </section>
      )}
    </div>
  );
}

// --- checkout -----------------------------------------------------------------------------

const INTERVALS = new Set(["monthly", "quarterly", "semi_annual", "annual"]);

/** The order request from `?plan=|bundle=|activate=&interval=&trial=1` -
 * identifiers only, each validated before it goes near an API path. */
export function orderRequestFrom(params: URLSearchParams): OrderRequest | null {
  const plan = params.get("plan");
  const bundle = params.get("bundle");
  const activate = params.get("activate");
  const interval = params.get("interval");
  const request: OrderRequest = {};
  if (plan && UUID_RE.test(plan)) request.plan_version_id = plan;
  else if (bundle && UUID_RE.test(bundle)) request.bundle_version_id = bundle;
  else if (activate && UUID_RE.test(activate)) request.activate_subscription_id = activate;
  else return null;
  if (interval && INTERVALS.has(interval)) request.billing_interval = interval;
  if (params.get("trial") === "1" && !request.activate_subscription_id) request.with_trial = true;
  return request;
}

/**
 * Checkout: the server's quote for what was chosen, then "Place order" -
 * which either fulfils it at once (a trial, a free plan) or leaves an order
 * awaiting payment and takes the payer to the provider's hosted page. One
 * command key per visit, so a retry is the same order.
 */
export function CheckoutScreen() {
  const { slug, can } = useOrgAccess();
  const params = useSearchParams();
  const request = orderRequestFrom(params);
  const canBuy = can("commercial.subscription.manage");

  return (
    <div className={styles.page}>
      <PageHeader title="Checkout" lead="Review what you're buying. Nothing is charged until you pay." />
      {!request ? (
        <div role="alert" className={local.problem}>
          <p>Nothing was chosen to buy.</p>
          <p>
            <Link href={`/o/${slug}/explore`}>Explore products</Link>
          </p>
        </div>
      ) : !canBuy ? (
        <p role="alert">Only the organization owner can buy. Ask them to complete this purchase.</p>
      ) : (
        <Checkout slug={slug} request={request} />
      )}
    </div>
  );
}

function Checkout({ slug, request }: { slug: string; request: OrderRequest }) {
  const router = useRouter();
  const [refresh, setRefresh] = useState(0);
  const [account] = useLoad(() => consoleApi.billingAccount(slug), [slug, refresh]);
  const opening = useAction(() => setRefresh((n) => n + 1));

  return (
    <LoadGate load={account} what="your billing account">
      {({ billing_account }) =>
        billing_account ? (
          <Quote slug={slug} request={request} onDone={(order) => router.push(`/o/${slug}/orders?placed=${order.order_id}`)} />
        ) : (
          <Panel title="Billing details">
            {opening.feedback}
            <OpenAccountForm
              busy={opening.busy}
              onOpen={(body) =>
                void opening.act(() => consoleApi.openBillingAccount(slug, body), "Billing details saved.")
              }
            />
          </Panel>
        )
      }
    </LoadGate>
  );
}

function Quote({ slug, request, onDone }: { slug: string; request: OrderRequest; onDone: (order: Order) => void }) {
  const [load] = useLoad(() => consoleApi.quoteOrder(slug, request), [slug, JSON.stringify(request)]);
  const key = useRef(newKey());
  const [placed, setPlaced] = useState<Order | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pay = usePayInvoice(slug, () => undefined);

  async function place() {
    setBusy(true);
    setProblem(null);
    try {
      const order = placed ?? (await consoleApi.placeOrder(slug, request, key.current));
      setPlaced(order);
      if (order.requires_payment && order.invoice_id) {
        pay.start({ invoice_id: order.invoice_id, number: order.invoice_number ?? order.number });
      } else {
        onDone(order);
      }
    } catch (err) {
      setProblem(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <LoadGate load={load} what="the price">
      {(quote) => (
        <Panel title="Order summary">
          <dl className={local.details}>
            <div>
              <dt>Item</dt>
              <dd>{quote.description}</dd>
            </div>
            {quote.is_trial ? (
              <div>
                <dt>Free trial</dt>
                <dd>{quote.trial_days} days - nothing to pay now</dd>
              </div>
            ) : (
              <>
                <div>
                  <dt>Due now</dt>
                  <dd>
                    {quote.requires_payment ? money(quote.amount_due_minor, quote.currency) : "Nothing"}
                    {quote.prorated && quote.requires_payment && (
                      <span className={local.priceNote}> (for the rest of your current billing period)</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Then</dt>
                  <dd>
                    {money(quote.recurring_amount_minor, quote.currency)} {intervalLabel(quote.billing_interval).toLowerCase()}
                  </dd>
                </div>
              </>
            )}
          </dl>
          {pay.feedback}
          {problem && (
            <p role="alert" className="w360-error-text">
              {problem}
            </p>
          )}
          <p className="w360-hint">
            {quote.requires_payment
              ? "You'll pay on our payment provider's secure page. Access starts as soon as the payment is confirmed."
              : "Nothing is charged now."}
          </p>
          <div className={local.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              disabled={busy || pay.busy}
              onClick={() => void place()}
            >
              {quote.requires_payment ? "Place order and pay" : quote.is_trial ? "Start free trial" : "Place order"}
            </button>
          </div>
        </Panel>
      )}
    </LoadGate>
  );
}

// --- orders ----------------------------------------------------------------------------------

const ORDER_STATUS: Record<string, string> = {
  pending_payment: "awaiting payment",
  paid: "paid",
  fulfilled: "complete",
  cancelled: "cancelled",
  expired: "expired",
  failed: "refunded",
};

/** Orders: every purchase, newest first - pay or cancel one awaiting payment. */
export function OrdersScreen() {
  const { slug, can } = useOrgAccess();
  const placed = useSearchParams().get("placed");
  const [refresh, setRefresh] = useState(0);
  const paged = usePaged((cursor) => consoleApi.orders(slug, cursor), [slug, refresh]);
  const pay = usePayInvoice(slug, () => setRefresh((n) => n + 1));
  const cancelling = useAction(() => setRefresh((n) => n + 1));
  const canBuy = can("commercial.subscription.manage");
  const canPay = can("platform.billing.manage");

  return (
    <div className={styles.page}>
      <PageHeader title="Orders" lead="Everything your organization has bought, newest first." />
      {placed && UUID_RE.test(placed) && (
        <p role="status">Your order was placed. Products appear under My products once any payment is confirmed.</p>
      )}
      {pay.feedback}
      {cancelling.feedback}
      {paged.state === "loading" && <p aria-live="polite">Loading orders…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to orders.</p>}
      {paged.state === "error" && <p role="alert">Orders could not be loaded. Try again shortly.</p>}
      {paged.state === "ready" && (
          <>
            <Table
              caption="Orders"
              head={["Order", "Item", "Amount", "Status", "Placed", "Actions"]}
              isEmpty={paged.rows.length === 0}
              empty="No orders yet."
            >
              {paged.rows.map((o: Order) => (
                <tr key={o.order_id}>
                  <th scope="row">{o.number}</th>
                  <td>
                    {o.description}
                    {o.is_trial && <div className={cp.muted}>Free trial</div>}
                  </td>
                  <td className={cp.num}>{o.amount_due_minor ? money(o.amount_due_minor, o.currency) : "—"}</td>
                  <td>
                    <Status value={ORDER_STATUS[o.status] ?? o.status} />
                  </td>
                  <td>{when(o.created_at)}</td>
                  <td>
                    {o.status === "pending_payment" && (
                      <div className={local.actions}>
                        {canPay && o.invoice_id && (
                          <button
                            type="button"
                            className="w360-btn w360-btn-primary"
                            disabled={pay.busy}
                            onClick={() => pay.start({ invoice_id: o.invoice_id ?? "", number: o.invoice_number ?? o.number })}
                            aria-label={`Pay order ${o.number}`}
                          >
                            Pay
                          </button>
                        )}
                        {canBuy && (
                          <button
                            type="button"
                            className="w360-btn w360-btn-secondary"
                            disabled={cancelling.busy}
                            onClick={() =>
                              void cancelling.act(() => consoleApi.cancelOrder(slug, o.order_id), `Order ${o.number} cancelled.`)
                            }
                            aria-label={`Cancel order ${o.number}`}
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <MoreButton hasMore={paged.hasMore} onMore={() => void paged.more()} />
          </>
      )}
    </div>
  );
}

// --- support ------------------------------------------------------------------------------------

/** Support: where to write, and what to quote so support can find you. */
export function HelpScreen() {
  const { slug, access } = useOrgAccess();
  const site = useSiteSettings();
  return (
    <div className={styles.page}>
      <PageHeader title="Support" lead="Help with your organization, your products or your bill." />
      <Panel title="Contact support">
        {site?.support_email ? (
          <p>
            Email <a href={`mailto:${site.support_email}`}>{site.support_email}</a>. Include your organization&apos;s
            reference below so we can find you quickly.
          </p>
        ) : (
          <p className={cp.muted}>Support contact details will be published here soon.</p>
        )}
        <dl className={local.details}>
          <div>
            <dt>Organization</dt>
            <dd>{access?.organization.display_name}</dd>
          </div>
          <div>
            <dt>Reference</dt>
            <dd className={local.nowrap}>{slug}</dd>
          </div>
        </dl>
      </Panel>
      <Panel title="Common questions">
        <ul className={local.includes}>
          <li>
            A product won&apos;t open? Check <Link href={`/o/${slug}/apps`}>My products</Link> for its status and seat.
          </li>
          <li>
            A payment question? See <Link href={`/o/${slug}/orders`}>Orders</Link> and{" "}
            <Link href={`/o/${slug}/settings/billing`}>Subscription & billing</Link>.
          </li>
          <li>
            Signing in? Manage your password and two-step verification in{" "}
            <Link href={`/o/${slug}/settings/security`}>My security</Link>.
          </li>
        </ul>
      </Panel>
    </div>
  );
}
