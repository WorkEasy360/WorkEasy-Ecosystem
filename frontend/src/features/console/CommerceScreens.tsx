"use client";

import { useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import { accountApi, consoleApi, type Order, type OrderQuote, type OrderRequest } from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";
import { newKey } from "@/lib/api/controlPlane";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { AppCard } from "@/features/launcher/AppCard";
import { LoadGate, Status, Table, describeError, money, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { DialogHead, Modal, dialog } from "@/features/controlplane/Modal";
import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  Page,
  SearchInput,
  Stack,
  StatusBadge,
  StatusChips,
  productName,
  useConfirm,
  type Column,
} from "@/features/controlplane/kit";
import { cleanCoupon, useSiteSettings } from "@/features/site/SiteShell";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { InvoicesSection, OpenAccountForm, usePayInvoice } from "./BillingScreen";
import { useCart } from "./cart";
import { AdminInsights } from "./DashboardScreen";
import { ContactOrgAdmin, PageHeader, Panel, UUID_RE, day, intervalLabel } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";
import { HelpLibrary, NewTicketDialog, SupportTickets, setupOpen, useSetupRecords, useSupportDesk } from "./CustomerDesk";

export { MyProductsScreen } from "./MyProductsScreen";

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
  const cart = params.get("cart");
  const request: OrderRequest = {};
  const cartIds = cart ? cart.split(",") : [];
  if (cartIds.length >= 2 && cartIds.length <= 10 && cartIds.every((id) => UUID_RE.test(id))) {
    request.cart_items = cartIds.map((id) => ({ plan_version_id: id }));
  } else if (plan && UUID_RE.test(plan)) request.plan_version_id = plan;
  else if (bundle && UUID_RE.test(bundle)) request.bundle_version_id = bundle;
  else if (activate && UUID_RE.test(activate)) request.activate_subscription_id = activate;
  else return null;
  if (interval && INTERVALS.has(interval)) request.billing_interval = interval;
  // 2026-11-03: the Store's checkout box sends how many of one plan.
  const quantity = Number(params.get("quantity") ?? "1");
  if (request.plan_version_id && Number.isInteger(quantity) && quantity > 1 && quantity <= 100_000) request.quantity = quantity;
  if (params.get("trial") === "1" && !request.activate_subscription_id && !request.cart_items) request.with_trial = true;
  // ADR-053: a purchase that started in a partner's branded Store says which one.
  const storefront = params.get("storefront");
  if (storefront && /^[a-z0-9-]{1,48}$/.test(storefront)) request.storefront = storefront;
  // ADR-058: the coupon typed on the Store's product page - a code only.
  const coupon = cleanCoupon(params.get("coupon"));
  if (coupon && !request.with_trial) request.coupon_code = coupon;
  return request;
}

/** The server refused the order's coupon (`pricing.coupons.CouponRejected`). */
function couponRefused(err: unknown): boolean {
  return err instanceof ApiError && typeof err.body === "object" && err.body !== null && (err.body as { reason?: unknown }).reason === "coupon_invalid";
}

/** The quote for `request`; a coupon the server refuses is dropped (with the
 * reason shown) rather than hiding the whole order summary. */
async function quoteWithCoupon(slug: string, request: OrderRequest): Promise<{ quote: OrderQuote; couponProblem: string | null }> {
  try {
    return { quote: await consoleApi.quoteOrder(slug, request), couponProblem: null };
  } catch (err) {
    if (!request.coupon_code || !couponRefused(err)) throw err;
    const { coupon_code: _dropped, ...rest } = request;
    void _dropped;
    return { quote: await consoleApi.quoteOrder(slug, rest), couponProblem: describeError(err) };
  }
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
  // ADR-058: the coupon may be changed here; a new code is a new quote (and a
  // new command key, since the request it would place is a different one).
  const [coupon, setCoupon] = useState(request.coupon_code ?? "");
  const withCoupon: OrderRequest = { ...request, coupon_code: coupon || undefined };
  const [account] = useLoad(() => consoleApi.billingAccount(slug), [slug, refresh]);
  const opening = useAction(() => setRefresh((n) => n + 1));

  return (
    <LoadGate load={account} what="your billing account">
      {({ billing_account }) =>
        billing_account ? (
          <Quote
            key={coupon}
            slug={slug}
            request={withCoupon}
            onCoupon={setCoupon}
            onDone={(order) => router.push(`/o/${slug}/orders?placed=${order.order_id}`)}
          />
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

/** "Have a coupon?" - a code goes back to the checkout, which quotes it. */
function CouponField({ applied, onApply }: { applied: string | null; onApply: (code: string) => void }) {
  const [code, setCode] = useState("");
  const [invalid, setInvalid] = useState(false);
  if (applied) {
    return (
      <p className={local.priceNote}>
        Coupon <strong>{applied}</strong> applied.{" "}
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onApply("")}>
          Remove coupon
        </button>
      </p>
    );
  }
  return (
    <form
      className={local.actions}
      onSubmit={(e) => {
        e.preventDefault();
        const clean = cleanCoupon(code);
        setInvalid(!clean);
        if (clean) onApply(clean);
      }}
    >
      <label className="w360-visually-hidden" htmlFor="checkout-coupon">
        Coupon code
      </label>
      <input
        id="checkout-coupon"
        className="w360-input"
        placeholder="Coupon code"
        value={code}
        maxLength={32}
        autoComplete="off"
        onChange={(e) => setCode(e.target.value)}
      />
      <button type="submit" className="w360-btn w360-btn-secondary" disabled={!code.trim()}>
        Apply
      </button>
      {invalid && (
        <span role="alert" className="w360-error-text">
          That coupon code is not valid.
        </span>
      )}
    </form>
  );
}

function Quote({
  slug,
  request,
  onDone,
  onCoupon,
}: {
  slug: string;
  request: OrderRequest;
  onDone: (order: Order) => void;
  onCoupon?: (code: string) => void;
}) {
  const [load] = useLoad(() => quoteWithCoupon(slug, request), [slug, JSON.stringify(request)]);
  const key = useRef(newKey());
  const [placed, setPlaced] = useState<Order | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pay = usePayInvoice(slug, () => undefined);
  const cartStore = useCart(slug);

  async function place(toPlace: OrderRequest) {
    setBusy(true);
    setProblem(null);
    try {
      const order = placed ?? (await consoleApi.placeOrder(slug, toPlace, key.current));
      setPlaced(order);
      // The order now holds the cart's lines; the server re-priced each one.
      if (request.cart_items) cartStore.clear();
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
      {({ quote, couponProblem }) => (
        <Panel title="Order summary">
          {quote.lines && quote.lines.length > 0 && (
            <Table
              caption="Products in this order"
              head={["Plan", quote.prorated && quote.requires_payment ? "Due now" : "Price"]}
            >
              {quote.lines.map((line) => (
                <tr key={line.product_code}>
                  <th scope="row">
                    {line.plan_name}
                    {line.quantity > 1 ? ` × ${line.quantity}` : ""}
                  </th>
                  <td className={cp.num}>
                    {money(quote.prorated && quote.requires_payment ? line.amount_due_minor : line.recurring_amount_minor, quote.currency)}
                  </td>
                </tr>
              ))}
            </Table>
          )}
          <dl className={local.details}>
            <div>
              <dt>{quote.lines && quote.lines.length > 0 ? "Items" : "Item"}</dt>
              <dd>{quote.description}</dd>
            </div>
            {quote.is_trial ? (
              <div>
                <dt>Free trial</dt>
                <dd>{quote.trial_days} days - nothing to pay now</dd>
              </div>
            ) : (
              <>
                {quote.requires_payment && (quote.coupon || quote.tax || (quote.setup_fee_minor ?? 0) > 0) && (
                  <div>
                    <dt>Subtotal</dt>
                    <dd>{money(quote.subtotal_minor ?? quote.amount_due_minor, quote.currency)}</dd>
                  </div>
                )}
                {quote.requires_payment && quote.coupon && (
                  <div>
                    <dt>
                      Coupon {quote.coupon.code} ({quote.coupon.label})
                    </dt>
                    <dd>−{money(quote.coupon.discount_minor, quote.currency)}</dd>
                  </div>
                )}
                {quote.requires_payment && quote.tax && (
                  <div>
                    <dt>{quote.tax.description}</dt>
                    <dd>{money(quote.tax.amount_minor, quote.currency)}</dd>
                  </div>
                )}
                <div>
                  <dt>{quote.tax ? "Total payable now" : "Due now"}</dt>
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
                    {quote.tax && <span className={local.priceNote}> + {quote.tax.label}</span>}
                  </dd>
                </div>
              </>
            )}
          </dl>
          {couponProblem && (
            <p role="alert" className="w360-error-text">
              {couponProblem} The order is priced without it.
            </p>
          )}
          {onCoupon && quote.requires_payment && !placed && (
            <CouponField applied={quote.coupon?.code ?? null} onApply={onCoupon} />
          )}
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
            {quote.channel === "storefront" && " Bought through your partner's store: they will be your partner for this account."}
          </p>
          <div className={local.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-primary"
              disabled={busy || pay.busy}
              onClick={() => void place(couponProblem ? { ...request, coupon_code: undefined } : request)}
            >
              {quote.requires_payment ? "Place order and pay" : quote.is_trial ? "Start free trial" : "Place order"}
            </button>
          </div>
        </Panel>
      )}
    </LoadGate>
  );
}

// --- orders & payments ---------------------------------------------------------------------

/** Payment status: was the order paid. */
function paymentState(o: Order): { value: string; label: string } {
  if (o.status === "pending_payment") return { value: "awaiting_payment", label: "Awaiting payment" };
  if (o.status === "paid" || o.status === "fulfilled") return { value: "paid", label: o.amount_due_minor > 0 ? "Paid" : "No charge" };
  if (o.status === "failed") return { value: "failed", label: "Failed - refunded" };
  return { value: o.status, label: o.status === "expired" ? "Expired" : "Cancelled" };
}

/** Activation status: did the order's products switch on. */
function activationState(o: Order): { value: string; label: string } {
  if (o.status === "fulfilled") return { value: "active", label: "Activated" };
  if (o.status === "paid") return { value: "in_progress", label: "Activating" };
  if (o.status === "pending_payment") return { value: "pending", label: "After payment" };
  return { value: "inactive", label: "Not activated" };
}

type OrderFilter = "" | "pending_payment" | "paid" | "fulfilled" | "cancelled";

const ORDER_CHIPS: { value: OrderFilter; label: string; match: (o: Order) => boolean }[] = [
  { value: "", label: "All", match: () => true },
  { value: "pending_payment", label: "Awaiting payment", match: (o) => o.status === "pending_payment" },
  { value: "paid", label: "Paid", match: (o) => o.status === "paid" },
  { value: "fulfilled", label: "Complete", match: (o) => o.status === "fulfilled" },
  { value: "cancelled", label: "Cancelled or expired", match: (o) => ["cancelled", "expired", "failed"].includes(o.status) },
];

/** A paid order a refund may be asked for: money changed hands and it was not a trial. */
const refundable = (o: Order) => (o.status === "paid" || o.status === "fulfilled") && o.amount_due_minor > 0 && !o.is_trial;

/**
 * Orders & Payments (2026-11-11, the User Admin brief Part 2 section 03): every
 * purchase with its payment and activation status, its payment details, the
 * invoices, and a refund request. Paying and cancelling are as before; a refund is
 * asked for through a support ticket to whoever sold the order (the purchase
 * channel), since the platform has no customer refund endpoint - support decides
 * against the product's refund policy.
 */
export function OrdersScreen() {
  const { slug, can } = useOrgAccess();
  const placed = useSearchParams().get("placed");
  const [refresh, setRefresh] = useState(0);
  const paged = usePaged((cursor) => consoleApi.orders(slug, cursor), [slug, refresh]);
  const pay = usePayInvoice(slug, () => setRefresh((n) => n + 1));
  const cancelling = useAction(() => setRefresh((n) => n + 1));
  const confirm = useConfirm();
  const canBuy = can("commercial.subscription.manage");
  const canPay = can("platform.billing.manage");
  const [filter, setFilter] = useState<OrderFilter>("");
  const [q, setQ] = useState("");
  const [details, setDetails] = useState<Order | null>(null);
  const [refund, setRefund] = useState<Order | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const chip = ORDER_CHIPS.find((c) => c.value === filter) ?? ORDER_CHIPS[0];
  const needle = q.toLowerCase();
  const rows = paged.rows.filter(
    (o) => chip.match(o) && (!needle || `${o.number} ${o.description} ${o.invoice_number ?? ""}`.toLowerCase().includes(needle)),
  );

  const columns: Column<Order>[] = [
    { key: "order", header: "Order", value: (o) => o.number, render: (o) => <span className={cp.mono}>{o.number}</span> },
    {
      key: "item",
      header: "Item",
      value: (o) => o.description,
      render: (o) => <Stack main={o.description} sub={o.is_trial ? "Free trial" : o.invoice_number ? `Invoice ${o.invoice_number}` : undefined} />,
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (o) => o.amount_due_minor,
      render: (o) => (o.amount_due_minor ? money(o.amount_due_minor, o.currency) : "—"),
    },
    {
      key: "payment",
      header: "Payment",
      value: (o) => paymentState(o).label,
      render: (o) => <StatusBadge value={paymentState(o).value} label={paymentState(o).label} />,
    },
    {
      key: "activation",
      header: "Activation",
      value: (o) => activationState(o).label,
      render: (o) => <StatusBadge value={activationState(o).value} label={activationState(o).label} />,
    },
    { key: "placed", header: "Placed", value: (o) => o.created_at, render: (o) => when(o.created_at) },
    {
      key: "actions",
      header: "Actions",
      render: (o) => (
        <div className={local.actions}>
          {o.status === "pending_payment" && canPay && o.invoice_id && (
            <Button
              size="sm"
              variant="primary"
              disabled={pay.busy}
              onClick={() => pay.start({ invoice_id: o.invoice_id ?? "", number: o.invoice_number ?? o.number })}
              aria-label={`Pay order ${o.number}`}
            >
              Pay
            </Button>
          )}
          {o.status === "pending_payment" && canBuy && (
            <Button
              size="sm"
              disabled={cancelling.busy}
              aria-label={`Cancel order ${o.number}`}
              onClick={() =>
                confirm.ask({
                  title: `Cancel order ${o.number}?`,
                  message: "The order and its invoice are cancelled. Nothing has been charged, and you can order again at any time.",
                  confirmLabel: "Cancel order",
                  danger: true,
                  onConfirm: () => {
                    void cancelling.act(() => consoleApi.cancelOrder(slug, o.order_id), `Order ${o.number} cancelled.`);
                  },
                })
              }
            >
              Cancel
            </Button>
          )}
          <Button size="sm" aria-haspopup="dialog" aria-label={`Payment details of order ${o.number}`} onClick={() => setDetails(o)}>
            Details
          </Button>
          {refundable(o) && canPay && (
            <Button size="sm" variant="ghost" aria-haspopup="dialog" aria-label={`Request a refund for order ${o.number}`} onClick={() => setRefund(o)}>
              Request refund
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <Page>
      <PageHeader
        title="Orders & Payments"
        lead="Everything your organization has bought, how it was paid, and its invoices."
        primary={
          <ButtonLink variant="primary" icon="store" href={`/o/${slug}/store`}>
            Explore Products
          </ButtonLink>
        }
      />
      {placed && UUID_RE.test(placed) && (
        <Alert tone="success" title="Your order was placed.">
          Products appear under My Products once any payment is confirmed.
        </Alert>
      )}
      {notice && <Alert tone="success" title={notice} />}
      {pay.feedback}
      {cancelling.feedback}
      {confirm.dialog}
      <Card title="Orders" description="Newest first. Search, filter or sort; open Details for the payment.">
        <div className={local.section}>
          {paged.state === "ready" && paged.rows.length > 0 && (
            <>
              <StatusChips
                label="Order status"
                value={filter}
                onChange={setFilter}
                chips={ORDER_CHIPS.map((c) => ({ value: c.value, label: c.label, count: paged.rows.filter(c.match).length }))}
              />
              <FilterBar
                label="Find an order"
                onClear={
                  filter || q
                    ? () => {
                        setFilter("");
                        setQ("");
                      }
                    : undefined
                }
              >
                <SearchInput label="Search by order, invoice or item" value={q} onSearch={setQ} />
              </FilterBar>
            </>
          )}
          {paged.state === "forbidden" ? (
            <ContactOrgAdmin what="orders and payments" />
          ) : (
            <DataTable
              caption="Orders"
              columns={columns}
              rows={rows}
              rowKey={(o) => o.order_id}
              rowLabel={(o) => o.number}
              loading={paged.state === "loading"}
              error={paged.state === "error" ? "Orders could not be loaded. Try again shortly." : null}
              onLoadMore={paged.hasMore ? () => void paged.more() : undefined}
              hasMore={paged.hasMore}
              maxHeight={false}
              empty={
                <EmptyState
                  icon="cart"
                  title={filter || q ? "No orders match." : "No orders yet."}
                  hint={filter || q ? "Try another status or search." : "Orders you place in the Store appear here."}
                />
              }
            />
          )}
          {!canPay && paged.rows.some((o) => o.status === "pending_payment") && <ContactOrgAdmin what="paying for orders" />}
        </div>
      </Card>
      <InvoicesSection refresh={refresh} />
      {details && <OrderDetails slug={slug} order={details} onClose={() => setDetails(null)} />}
      {refund && (
        <RefundRequest
          slug={slug}
          order={refund}
          onClose={() => setRefund(null)}
          onSent={() => setNotice(`Refund request for order ${refund.number} sent. Support replies in Help.`)}
        />
      )}
    </Page>
  );
}

/** An order's payment details: its lines, amounts, the dates it moved, and why it failed. */
function OrderDetails({ slug, order: o, onClose }: { slug: string; order: Order; onClose: () => void }) {
  const titleId = useId();
  const { can } = useOrgAccess();
  const [invoice] = useLoad(
    () => (o.invoice_id && can("platform.billing.view") ? consoleApi.invoice(slug, o.invoice_id) : Promise.resolve(null)),
    [slug, o.invoice_id],
  );
  const attempts = invoice.status === "ready" ? (invoice.data?.attempts ?? []) : [];
  return (
    <Modal labelledBy={titleId} onClose={onClose} size="lg">
      <DialogHead titleId={titleId} title={`Order ${o.number}`} onClose={onClose} />
      <div className={dialog.body}>
        <div className={local.section}>
          <DescriptionList
            columns={2}
            items={[
              { label: "Item", value: o.description },
              { label: "Billing", value: `${intervalLabel(o.billing_interval)}${o.quantity > 1 ? ` · quantity ${o.quantity}` : ""}` },
              { label: "Payment", value: <StatusBadge value={paymentState(o).value} label={paymentState(o).label} /> },
              { label: "Activation", value: <StatusBadge value={activationState(o).value} label={activationState(o).label} /> },
              { label: "Amount due", value: o.amount_due_minor ? money(o.amount_due_minor, o.currency) : "No charge" },
              { label: "Coupon", value: o.coupon_code ?? null, when: Boolean(o.coupon_code) },
              { label: "Discount", value: o.discount_minor ? money(o.discount_minor, o.currency) : null, when: Boolean(o.discount_minor) },
              { label: "Tax", value: o.tax_minor ? money(o.tax_minor, o.currency) : null, when: Boolean(o.tax_minor) },
              { label: "Invoice", value: o.invoice_number },
              { label: "Placed", value: when(o.created_at) },
              { label: "Paid", value: o.paid_at ? when(o.paid_at) : null },
              { label: "Activated", value: o.fulfilled_at ? when(o.fulfilled_at) : null },
              { label: "Cancelled", value: o.cancelled_at ? when(o.cancelled_at) : null, when: Boolean(o.cancelled_at) },
              { label: "Pay by", value: o.expires_at && o.status === "pending_payment" ? when(o.expires_at) : null, when: o.status === "pending_payment" },
              { label: "Problem", value: o.failure_reason ? o.failure_reason.replace(/_/g, " ") : null, when: Boolean(o.failure_reason) },
            ]}
          />
          {(o.lines ?? []).length > 0 && (
            <Table caption={`Products in order ${o.number}`} head={["Product", "Plan", "Quantity", "Amount"]} numeric={["Quantity", "Amount"]}>
              {(o.lines ?? []).map((line, i) => (
                <tr key={`${line.product_code}-${i}`}>
                  <th scope="row">{productName(line.product_code)}</th>
                  <td>{line.plan_name || "—"}</td>
                  <td className={cp.num}>{line.quantity}</td>
                  <td className={cp.num}>{money(line.amount_due_minor, o.currency)}</td>
                </tr>
              ))}
            </Table>
          )}
          {attempts.length > 0 && (
            <Table caption={`Payment attempts for order ${o.number}`} head={["Status", "Amount", "Provider", "Problem"]} numeric={["Amount"]}>
              {attempts.map((a) => (
                <tr key={a.attempt_id}>
                  <th scope="row">
                    <Status value={a.status} />
                  </th>
                  <td className={cp.num}>{money(a.amount_minor, a.currency)}</td>
                  <td>{a.provider}</td>
                  <td>{a.failure_code ? a.failure_code.replace(/_/g, " ") : "—"}</td>
                </tr>
              ))}
            </Table>
          )}
        </div>
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

/** A refund request: a billing ticket to whoever sold the order, prefilled. */
function RefundRequest({ slug, order: o, onClose, onSent }: { slug: string; order: Order; onClose: () => void; onSent: () => void }) {
  const [desk] = useSupportDesk(slug);
  if (desk.status === "loading") return null;
  if (desk.status !== "ready") {
    return (
      <Modal labelledBy="w360-refund-unavailable" onClose={onClose}>
        <DialogHead titleId="w360-refund-unavailable" title="Request a refund" onClose={onClose} />
        <div className={dialog.body}>
          <p role="alert">Support can&apos;t be reached right now, so the request was not sent. Try again in a moment.</p>
        </div>
        <div className={dialog.foot}>
          <Button onClick={onClose}>Close</Button>
        </div>
      </Modal>
    );
  }
  return (
    <NewTicketDialog
      slug={slug}
      desk={desk.data}
      title={`Request a refund · ${o.number}`}
      initial={{
        category: "billing",
        priority: "normal",
        subject: `Refund request: order ${o.number}`,
        message: `Please refund order ${o.number} (${o.description}, ${money(o.amount_due_minor, o.currency)}, paid ${o.paid_at ? day(o.paid_at) : "recently"}).\n\nReason: `,
      }}
      onClose={onClose}
      onCreated={onSent}
    />
  );
}

// --- help ----------------------------------------------------------------------------------------

/**
 * Help (2026-11-11, the User Admin brief Part 2 section 06): a support center - create
 * a ticket, follow My Tickets and their conversation, who answers (Workeasy360, or the
 * organization's partner), and the published Setup and Billing guides. Product setup
 * is handled in My Products; this page points there when a product is waiting.
 * `?new=1` opens Create Support Ticket on arrival (the dashboard's quick action), and
 * `?ticket=<id>` opens that ticket's conversation (a reply announced by the bell).
 */
export function HelpScreen() {
  const { slug, access } = useOrgAccess();
  const site = useSiteSettings();
  const params = useSearchParams();
  const [desk, reloadDesk] = useSupportDesk(slug);
  const [setup] = useSetupRecords(slug);
  const waiting = setup.status === "ready" ? setup.data.filter(setupOpen).length : 0;
  const [creating, setCreating] = useState(() => params.get("new") === "1");
  const [linked, setLinked] = useState(params.get("new"));
  if (linked !== params.get("new")) {
    setLinked(params.get("new"));
    if (params.get("new") === "1") setCreating(true);
  }
  const [opened, setOpened] = useState<string | null>(null);
  // Only an id-shaped value is looked up; the server decides whether it is this member's.
  const linkedTicket = UUID_RE.test(params.get("ticket") ?? "") ? params.get("ticket") : null;
  const ready = desk.status === "ready" ? desk.data : null;

  return (
    <Page>
      <PageHeader
        title="Help & Support"
        lead="Ask for help, follow your tickets, and read the setup and billing guides."
        primary={
          ready ? (
            <Button variant="primary" icon="plus" aria-haspopup="dialog" onClick={() => setCreating(true)}>
              Create Support Ticket
            </Button>
          ) : undefined
        }
      />
      {waiting > 0 && (
        <Alert
          tone="warning"
          title={waiting === 1 ? "A product is waiting for your setup details." : `${waiting} products are waiting for your setup details.`}
          actions={
            <ButtonLink size="sm" href={`/o/${slug}/apps?filter=setup`}>
              Open Pending Setup
            </ButtonLink>
          }
        />
      )}
      {opened && <Alert tone="success" title="Your ticket was sent. Its conversation is open under My tickets." />}
      <div className={local.cardRow}>
        <Card title="Your support contact" description="Who answers your tickets.">
          {ready ? (
            <div className={local.section}>
              <p className={local.contactLine}>
                <strong>{ready.provider}</strong>
                <Badge tone={ready.kind === "partner" ? "info" : "brand"}>{ready.kind === "partner" ? "Partner support" : "WorkEasy360 support"}</Badge>
              </p>
              <p className={local.muted}>
                {ready.kind === "partner"
                  ? `${ready.provider} looks after your WorkEasy360 subscription and answers your tickets.`
                  : "The WorkEasy360 team answers your tickets here."}
              </p>
            </div>
          ) : (
            <p className={local.muted}>Loading who answers your tickets…</p>
          )}
        </Card>
        <Card title="WorkEasy360 support" description="Write to us, quoting your reference.">
          <div className={local.section}>
            {site?.support_email ? (
              <p className={local.contactLine}>
                <span className={local.muted}>Email</span>
                <a href={`mailto:${site.support_email}`}>{site.support_email}</a>
              </p>
            ) : (
              <p className={local.muted}>Support contact details will be published here soon.</p>
            )}
            <DescriptionList
              items={[
                { label: "Organization", value: access?.organization.display_name },
                { label: "Reference", value: <span className={local.nowrap}>{slug}</span> },
              ]}
            />
          </div>
        </Card>
      </div>
      <LoadGate load={desk} what="your support tickets">
        {(d) => <SupportTickets key={opened ?? linkedTicket ?? "list"} slug={slug} desk={d} reload={reloadDesk} initialOpen={opened ?? linkedTicket} />}
      </LoadGate>
      <HelpLibrary />
      {creating && ready && (
        <NewTicketDialog
          slug={slug}
          desk={ready}
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            reloadDesk();
            setOpened(t.id);
          }}
        />
      )}
    </Page>
  );
}
