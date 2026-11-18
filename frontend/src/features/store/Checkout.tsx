"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Icon } from "@/components/sidebar/icons";
import { ApiError } from "@/lib/api/client";
import { consoleApi, type ConsoleAccess, type Order, type OrderQuote, type OrderRequest } from "@/lib/api/console";
import { orgAccountApi, type BillingProfile, type OrgWallets } from "@/lib/api/orgAccount";
import { publicApi, type PublicStoreCard } from "@/lib/api/public";
import { storeHostApi, type PaymentOption, type StorePaymentAttempt } from "@/lib/api/storeHost";
import { describeError } from "@/features/controlplane/ui";
import { toMinor } from "@/features/controlplane/amounts";
import { REFERRAL_TOKEN } from "@/features/auth/SelfService";
import { PAYMENT_POLL, checkoutNavigation, safeCheckoutUrl } from "@/features/console/BillingScreen";
import { shelfPrice } from "@/features/storefront/StoreBrowser";
import { useMyAccount, useStoreAccount } from "./account";
import { checkoutFrom, type CheckoutContext } from "./checkoutLinks";
import { rememberReturnTo } from "./returnTo";
import { METER_LABEL, PERIOD_LABEL, PERIOD_TERM } from "./ProductDetails";
import { ProductTile, tileIcon } from "./ProductCard";
import { StoreSignInForm, StoreSignUpForm } from "./StoreAuth";
import { useShellCatalog } from "./StoreShell";
import { myAccountLinks, storePaths } from "./links";
import styles from "./Checkout.module.css";
import shell from "./Store.module.css";

/**
 * The Store's checkout (ADR-065, the owner's checkout image):
 *
 *   Product -> Plan -> Shared Login -> Organization -> Review -> Payment ->
 *   Verification -> Confirmation
 *
 * One product, one plan and one organization per order. Four steps on one page:
 * 1. Account & Organization - sign in or create an account (the same WorkEasy360
 *    accounts), then the organization you buy for (chosen by itself when you
 *    belong to one).
 * 2. Billing Details - business or individual, the name and GST number on the
 *    invoice, the billing email (the organization's billing profile).
 * 3. Order Summary - the product, plan, period, seats, included usage, features
 *    and validity.
 * 4. Payment - the server's quote (plan amount, GST, total), the Platform Wallet
 *    (all of it, part of it, or none), and the payment provider for the rest.
 *
 * Nothing here decides a price, an eligibility or an outcome: the quote is the
 * server's, the wallet amount is checked again on the server, the order and the
 * payment are idempotent (one key per visit), and only the server's verified
 * payment activates anything - the confirmation page asks the server.
 */

const BUY = "commercial.subscription.manage";
const PAY = "platform.billing.manage";
const VIEW_BILLING = "platform.billing.view";

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function addTerm(start: Date, interval: string): Date {
  const months = { monthly: 1, quarterly: 3, semi_annual: 6, annual: 12 }[interval] ?? 1;
  const end = new Date(start);
  end.setMonth(end.getMonth() + months);
  end.setDate(end.getDate() - 1);
  return end;
}

const dateText = (d: Date) => d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });

/** The checkout's band (the image's dark header): its title and the three steps. */
export function CheckoutBand({ step }: { step: "order" | "payment" | "confirmation" }) {
  const order = ["order", "payment", "confirmation"] as const;
  const at = order.indexOf(step);
  const labels = { order: "Order", payment: "Payment", confirmation: "Confirmation" };
  return (
    <div className={styles.band}>
      <div>
        <h1 className={styles.bandTitle}>{step === "confirmation" ? "Order Confirmation" : "Checkout"}</h1>
        <p className={styles.bandLead}>{step === "confirmation" ? "Your order, its payment and what happens next." : "Complete your order and activate your product."}</p>
      </div>
      <ol className={styles.steps} aria-label="Checkout steps">
        {order.map((s, i) => (
          <li key={s} className={`${styles.step} ${i < at ? styles.stepDone : ""} ${i === at ? styles.stepNow : ""}`} aria-current={i === at ? "step" : undefined}>
            <span className={styles.stepMark}>{i < at ? <Icon name="check" size={18} /> : i + 1}</span>
            <span>{labels[s]}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Card({ n, icon, title, children, id }: { n: number; icon: string; title: string; children: React.ReactNode; id?: string }) {
  const headingId = useId();
  return (
    <section id={id} className={`${shell.card} ${styles.card}`} aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.cardTitle}>
        <span className={styles.cardIcon}>
          <Icon name={icon} size={22} />
        </span>
        {n}. {title}
      </h2>
      {children}
    </section>
  );
}

// --- 1. account & organization ---------------------------------------------------

function AccountStep({ returnPath }: { returnPath: string }) {
  const account = useStoreAccount();
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [sent, setSent] = useState(false);
  const selectId = useId();
  if (account.status === "loading") return <p aria-live="polite">Checking your sign-in…</p>;
  if (account.status === "signed_out") {
    return (
      <>
        <div className={styles.choices} role="radiogroup" aria-label="Your account">
          <button type="button" role="radio" aria-checked={mode === "existing"} className={styles.choice} onClick={() => setMode("existing")}>
            <span className={styles.radioDot} aria-hidden="true" />
            <span>
              <strong>Existing Account (Login)</strong>
              <span>I already have a WorkEasy360 account</span>
            </span>
          </button>
          <button type="button" role="radio" aria-checked={mode === "new"} className={styles.choice} onClick={() => setMode("new")}>
            <Icon name="userPlus" size={26} />
            <span>
              <strong>Create New Account</strong>
              <span>New to WorkEasy360? Create an account</span>
            </span>
          </button>
        </div>
        {mode === "existing" ? (
          <StoreSignInForm compact onSignedIn={account.refresh} />
        ) : sent ? (
          <p role="status" className={shell.notice}>
            <Icon name="mail" size={18} /> Check your email. The link signs you in and brings you back to this checkout with your plan still chosen.
          </p>
        ) : (
          <StoreSignUpForm returnPath={returnPath} onSent={() => setSent(true)} />
        )}
      </>
    );
  }
  const orgs = account.organizations;
  return (
    <>
      <div className={`${styles.choices} ${styles.choicesOne}`}>
        <div className={`${styles.choice} ${styles.choiceOn}`}>
          <span className={styles.radioDot} aria-hidden="true" />
          <span>
            <strong>Signed in{account.session?.name ? ` as ${account.session.name}` : ""}</strong>
            <span>{account.session?.email}</span>
          </span>
          <button type="button" className={styles.linkButton} onClick={() => void account.signOut()}>
            Not you?
          </button>
        </div>
      </div>
      {orgs.length === 0 ? (
        <NoOrganization />
      ) : (
        <div className={styles.field}>
          <label htmlFor={selectId}>
            Organization / Workspace <span aria-hidden="true">*</span>
          </label>
          {orgs.length === 1 ? (
            <p className={styles.orgFixed}>
              <Icon name="building" size={20} /> {orgs[0].display_name} <span className={styles.currentChip}>Current Organization</span>
            </p>
          ) : (
            <div className={styles.orgSelect}>
              <Icon name="building" size={20} />
              <select id={selectId} className="w360-input" value={account.organization?.slug ?? ""} onChange={(e) => account.chooseOrganization(e.target.value)}>
                {orgs.map((o) => (
                  <option key={o.slug} value={o.slug}>
                    {o.display_name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function NoOrganization() {
  const myAccount = useMyAccount();
  return (
    <p className={`${shell.notice} ${shell.warningNotice}`} role="alert">
      <Icon name="info" size={18} />
      <span>
        Your account has no organization yet. Buying is for an organization.{" "}
        <button type="button" className={styles.linkButton} onClick={() => void myAccount.open("/onboarding")}>
          Create one in User Admin
        </button>
        , then come back.
      </span>
    </p>
  );
}

// --- 2. billing details --------------------------------------------------------------

type Billing = { type: "business" | "individual"; name: string; gst: string; email: string };

const GSTIN = /^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]{3}$/;

function billingProblems(b: Billing): string[] {
  const found: string[] = [];
  if (!b.name.trim()) found.push(b.type === "business" ? "Enter the business name for the invoice." : "Enter your full name for the invoice.");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(b.email.trim())) found.push("Enter a valid billing email.");
  if (b.type === "business" && b.gst.trim() && !GSTIN.test(b.gst.trim().toUpperCase())) found.push("That GST number does not look right (15 characters, e.g. 07AAACA1234F1Z5).");
  return found;
}

function BillingStep({
  billing,
  setBilling,
  problems,
  locked,
}: {
  billing: Billing;
  setBilling: (b: Billing) => void;
  problems: string[];
  locked: boolean;
}) {
  const ids = { name: useId(), gst: useId(), email: useId(), type: useId() };
  const set = (patch: Partial<Billing>) => setBilling({ ...billing, ...patch });
  return (
    <fieldset className={styles.billing} disabled={locked}>
      <legend className="w360-visually-hidden">Billing details</legend>
      <div className={styles.field}>
        <span id={ids.type} className={styles.label}>
          Billing Type
        </span>
        <div className={styles.toggle} role="radiogroup" aria-labelledby={ids.type}>
          {(["business", "individual"] as const).map((t) => (
            <button key={t} type="button" role="radio" aria-checked={billing.type === t} className={styles.toggleOption} onClick={() => set({ type: t })}>
              <span className={styles.radioDot} aria-hidden="true" />
              {t === "business" ? "Business" : "Individual"}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.field}>
        <label htmlFor={ids.name}>
          {billing.type === "business" ? "Business Name" : "Full Name"} <span aria-hidden="true">*</span>
        </label>
        <input id={ids.name} className="w360-input" value={billing.name} maxLength={200} autoComplete="organization" onChange={(e) => set({ name: e.target.value })} />
      </div>
      {billing.type === "business" && (
        <div className={styles.field}>
          <label htmlFor={ids.gst}>
            GST Number <span className={styles.optional}>(Optional)</span>
          </label>
          <input id={ids.gst} className="w360-input" value={billing.gst} maxLength={15} autoComplete="off" onChange={(e) => set({ gst: e.target.value.toUpperCase() })} />
        </div>
      )}
      <div className={`${styles.field} ${styles.fieldWide}`}>
        <label htmlFor={ids.email}>
          Billing Email <span aria-hidden="true">*</span>
        </label>
        <span className={styles.iconInput}>
          <Icon name="mail" size={18} />
          <input id={ids.email} className="w360-input" type="email" value={billing.email} maxLength={254} autoComplete="email" onChange={(e) => set({ email: e.target.value })} />
        </span>
      </div>
      {problems.length > 0 && (
        <ul role="alert" className={styles.problems}>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

// --- 3. order summary ----------------------------------------------------------------

type Picked = {
  card: PublicStoreCard & { highlights?: string[] };
  plan: PublicStoreCard["plans"][number] & { included_usage?: Record<string, number> };
};

function findPicked(products: PublicStoreCard[], ctx: CheckoutContext): Picked | null {
  for (const card of products) {
    const plan =
      ctx.target.kind === "plan"
        ? card.plans.find((p) => p.plan_version_id === (ctx.target as { plan: string }).plan)
        : card.code === ctx.target.product
          ? card.plans.find((p) => p.plan_code === (ctx.target as { plan: string }).plan)
          : undefined;
    if (plan) return { card, plan };
  }
  return null;
}

function SummaryStep({ picked, ctx, quote }: { picked: Picked; ctx: CheckoutContext; quote: OrderQuote | null }) {
  const { card, plan } = picked;
  const trial = ctx.target.kind === "trial";
  const interval = ctx.target.kind === "plan" ? ctx.target.interval : "";
  const seats = ctx.target.kind === "plan" ? ctx.target.quantity : 1;
  const usage = Object.entries(plan.included_usage ?? {}).filter(([, n]) => typeof n === "number" && n > 0);
  const today = new Date();
  const trialDays = quote?.trial_days || card.trial?.duration_days || plan.trial_days || 0;
  const end = trial ? new Date(today.getTime() + (trialDays - 1) * 86_400_000) : addTerm(today, interval);
  return (
    <div className={styles.summary}>
      <div className={styles.productRow}>
        <ProductTile code={card.code} logo={card.logo_url} icon={tileIcon(card)} size={64} />
        <div className={styles.productText}>
          <strong>{card.title}</strong>
          <span>{card.summary || card.tagline}</span>
        </div>
        <Link href={storePaths.product(card.slug)} className={styles.remove} aria-label={`Change or remove ${card.title}`}>
          <Icon name="x" size={20} />
        </Link>
      </div>
      <div className={styles.planRow}>
        <span>
          <span className={styles.small}>Selected Plan</span>
          <strong className={styles.planName}>{plan.name}</strong>
        </span>
        <span className={styles.periodChip}>{trial ? `Free trial (${trialDays} days)` : `${PERIOD_LABEL[interval] ?? interval} (${PERIOD_TERM[interval] ?? ""})`}</span>
      </div>
      <dl className={styles.facts}>
        <div>
          <dt>
            <Icon name="users" size={18} /> Users / Seats
          </dt>
          <dd>{seats > 1 ? `${seats} users` : `${plan.included_seats} included${plan.max_seats && plan.max_seats !== plan.included_seats ? ` (up to ${plan.max_seats})` : ""}`}</dd>
        </div>
        {usage.map(([meter, n]) => (
          <div key={meter}>
            <dt>
              <Icon name={meter === "ai_tokens" ? "sparkles" : "message"} size={18} /> Included {METER_LABEL[meter] ?? meter}
            </dt>
            <dd>{n.toLocaleString()} / month</dd>
          </div>
        ))}
      </dl>
      {(card.highlights ?? []).length > 0 && (
        <div className={styles.features}>
          <p className={styles.featuresTitle}>
            <Icon name="settings" size={18} /> Key Features
          </p>
          <ul className={shell.ticks}>
            {(card.highlights ?? []).slice(0, 5).map((f) => (
              <li key={f}>
                <Icon name="check" size={16} />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className={styles.validity}>
        <Icon name="calendar" size={26} />
        <span>
          <strong>{trial ? "Trial Validity" : "Subscription Validity"}</strong>
          <span>
            {dateText(today)} – {dateText(end)} ({trial ? `${trialDays} days` : PERIOD_TERM[interval]}) if activated today
          </span>
        </span>
      </div>
    </div>
  );
}

// --- 4. payment ------------------------------------------------------------------------

function useQuote(slug: string | null, request: OrderRequest | null, ready: boolean) {
  const [state, setState] = useState<{ key: string; quote?: OrderQuote; error?: string; couponProblem?: string }>({ key: "" });
  const key = slug && request && ready ? `${slug}:${JSON.stringify(request)}` : "";
  useEffect(() => {
    if (!key || !slug || !request) return;
    let live = true;
    consoleApi.quoteOrder(slug, request).then(
      (quote) => live && setState({ key, quote }),
      async (err: unknown) => {
        // A refused coupon prices the order without it, saying why.
        if (request.coupon_code && err instanceof ApiError && (err.body as { reason?: string } | undefined)?.reason === "coupon_invalid") {
          const { coupon_code: _drop, ...rest } = request;
          void _drop;
          try {
            const quote = await consoleApi.quoteOrder(slug, rest);
            if (live) setState({ key, quote, couponProblem: describeError(err) });
            return;
          } catch (again) {
            if (live) setState({ key, error: describeError(again) });
            return;
          }
        }
        if (live) setState({ key, error: quoteProblem(err) });
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` names slug and request
  }, [key]);
  return state.key === key ? state : { key };
}

const PERIOD_WORD: Record<string, string> = { monthly: "monthly", quarterly: "quarterly", semi_annual: "every 6 months", annual: "yearly" };

/** Why an order cannot be quoted, in the buyer's words. An organization holds one
 * subscription with one billing period: a product added to it renews with it, so
 * a plan with no price for that period cannot join it. */
export function quoteProblem(err: unknown): string {
  if (err instanceof ApiError && err.code === "price_not_available") {
    const interval = (err.body as { interval?: string } | undefined)?.interval ?? "";
    const word = PERIOD_WORD[interval] ?? interval.replace(/_/g, " ");
    return word
      ? `Your organization's subscription renews ${word}, so a new product joins it ${word} - and this plan has no ${word} price. Choose a plan sold ${word}, or contact us.`
      : "This plan has no price for your organization's billing period. Choose another plan, or contact us.";
  }
  return describeError(err);
}

/** "Pay ₹18,320 & Subscribe". */
function payLabel(quote: OrderQuote, payNow: number, fromWallet: number, trial: boolean): string {
  if (trial || quote.is_trial) return "Start Free Trial";
  if (!quote.requires_payment) return "Activate Free Plan";
  if (payNow === 0 && fromWallet > 0) return "Pay from Wallet & Subscribe";
  return `Pay ${shelfPrice(payNow, quote.currency)} & Subscribe`;
}

export function CheckoutScreen() {
  const params = useSearchParams();
  const ctx = useMemo(() => checkoutFrom(params), [params]);
  if (!ctx) {
    return (
      <div className={shell.container}>
        <CheckoutBand step="order" />
        <div className={`${shell.card} ${shell.empty}`} role="alert">
          <p>Nothing was chosen to buy.</p>
          <p>
            <Link href={storePaths.products} className={shell.primary}>
              Browse products
            </Link>
          </p>
        </div>
      </div>
    );
  }
  return <Checkout ctx={ctx} returnPath={`${storePaths.checkout}?${params.toString()}`} />;
}

function Checkout({ ctx, returnPath }: { ctx: CheckoutContext; returnPath: string }) {
  const router = useRouter();
  const account = useStoreAccount();
  const myAccount = useMyAccount();
  const catalog = useShellCatalog();
  const slug = account.organization?.slug ?? null;
  const picked = catalog ? findPicked(catalog.products, ctx) : null;
  const trial = ctx.target.kind === "trial";

  const [access, setAccess] = useState<{ slug: string; data?: ConsoleAccess; error?: string } | null>(null);
  const [profile, setProfile] = useState<{ slug: string; account: BillingProfile | null } | null>(null);
  const [wallets, setWallets] = useState<{ slug: string; data: OrgWallets | null } | null>(null);
  const [billing, setBilling] = useState<Billing>({ type: "business", name: "", gst: "", email: "" });
  const [billingDirty, setBillingDirty] = useState(false);
  const [billingProblemsShown, setBillingProblemsShown] = useState<string[]>([]);
  const [coupon, setCoupon] = useState(ctx.target.kind === "plan" ? (ctx.target.coupon ?? "") : "");
  const [couponText, setCouponText] = useState("");
  const [useWallet, setUseWallet] = useState(false);
  const [walletText, setWalletText] = useState("");
  const [option, setOption] = useState<PaymentOption | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [returnOk, setReturnOk] = useState(false);
  const orderKey = useRef(newKey());
  const payKey = useRef(newKey());
  const placed = useRef<Order | null>(null);

  // Who may do what in the chosen organization - read again from the server.
  useEffect(() => {
    if (!slug) return;
    let live = true;
    consoleApi.access(slug).then(
      (data) => live && setAccess({ slug, data }),
      (err: unknown) => live && setAccess({ slug, error: describeError(err) }),
    );
    return () => {
      live = false;
    };
  }, [slug]);
  const perms = access?.slug === slug ? (access.data?.permissions ?? []) : [];
  const canBuy = perms.includes(BUY);
  const canPay = perms.includes(PAY);
  const canSeeBilling = perms.includes(VIEW_BILLING);

  // The billing profile and the wallets, when this person may read them.
  useEffect(() => {
    if (!slug || !canSeeBilling) return;
    let live = true;
    consoleApi.billingAccount(slug).then(
      ({ billing_account }) => {
        if (!live) return;
        setProfile({ slug, account: billing_account as BillingProfile | null });
        const b = billing_account as BillingProfile | null;
        setBilling({
          type: "business",
          name: b?.legal_name || account.organization?.display_name || "",
          gst: b?.tax_reference ?? "",
          email: b?.billing_email || account.session?.email || "",
        });
        setBillingDirty(!b);
      },
      () => live && setProfile({ slug, account: null }),
    );
    orgAccountApi.wallets(slug).then(
      (data) => live && setWallets({ slug, data }),
      () => live && setWallets({ slug, data: null }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload per organization
  }, [slug, canSeeBilling]);
  const billingAccount = profile?.slug === slug ? profile.account : undefined;

  // The return address a product sent the buyer with - shown only once approved.
  useEffect(() => {
    if (!ctx.returnTo) return;
    let live = true;
    const back = ctx.returnTo;
    storeHostApi.returnCheck(back.product, back.url).then(
      (r) => {
        if (!live) return;
        setReturnOk(r.allowed);
        if (r.allowed) rememberReturnTo({ url: back.url, label: "the product" });
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [ctx.returnTo]);

  const request: OrderRequest | null =
    ctx.target.kind === "plan"
      ? {
          plan_version_id: ctx.target.plan,
          billing_interval: ctx.target.interval,
          ...(ctx.target.quantity > 1 ? { quantity: ctx.target.quantity } : {}),
          ...(coupon ? { coupon_code: coupon } : {}),
        }
      : null;
  const quoteState = useQuote(slug, request, Boolean(canBuy && billingAccount));
  const quote = quoteState.quote ?? null;

  const currency = quote?.currency ?? picked?.plan.prices[0]?.currency ?? billingAccount?.currency ?? "";
  useEffect(() => {
    if (!currency) return;
    let live = true;
    storeHostApi.paymentOption(currency).then(
      (o) => live && setOption(o),
      () => live && setOption({ available: false, provider: null, label: null, methods: [] }),
    );
    return () => {
      live = false;
    };
  }, [currency]);

  const walletData = wallets?.slug === slug ? wallets.data : null;
  const walletBalance = walletData?.platform.exists && walletData.platform.currency === quote?.currency ? walletData.platform.balance_minor : 0;
  const total = quote?.requires_payment ? quote.amount_due_minor : 0;
  const walletMax = Math.min(walletBalance, total);
  // Exact: the typed amount to minor units with no floating point (amounts.ts).
  const walletWanted = useWallet ? (toMinor(walletText || "0", quote?.currency ?? "INR") ?? -1) : 0;
  const fromWallet = useWallet && walletWanted > 0 ? Math.min(walletMax, walletWanted) : 0;
  const payNow = total - fromWallet;
  const walletAllowed = Boolean(quote?.requires_payment && walletBalance > 0 && canPay);


  const signedIn = account.status === "signed_in";
  const gatewayNeeded = Boolean(quote?.requires_payment && payNow > 0);
  const gatewayMissing = gatewayNeeded && option !== null && !option.available;

  async function saveBilling(): Promise<boolean> {
    if (!slug) return false;
    const found = billingProblems(billing);
    setBillingProblemsShown(found);
    if (found.length) return false;
    const tax_reference = billing.type === "business" ? billing.gst.trim().toUpperCase() : "";
    if (!billingAccount) {
      const code = picked?.plan.prices.find((x) => x.amount_minor !== null)?.currency ?? catalog?.currency ?? "INR";
      await consoleApi.openBillingAccount(slug, { currency: code, billing_email: billing.email.trim() });
      await orgAccountApi.updateBillingProfile(slug, { legal_name: billing.name.trim(), ...(tax_reference ? { tax_reference } : {}) });
    } else if (billingDirty) {
      await orgAccountApi.updateBillingProfile(slug, { legal_name: billing.name.trim(), tax_reference, billing_email: billing.email.trim() });
    }
    const fresh = await consoleApi.billingAccount(slug);
    setProfile({ slug, account: fresh.billing_account as BillingProfile | null });
    setBillingDirty(false);
    return true;
  }

  /** The browser's view of the payment until there is somewhere to go. */
  async function follow(attempt: StorePaymentAttempt): Promise<void> {
    const started = Date.now();
    let current = attempt;
    while (true) {
      if (current.status === "succeeded") {
        router.push(`${storePaths.complete}?payment=${current.attempt_id}&org=${encodeURIComponent(slug ?? "")}`);
        return;
      }
      if (current.status === "awaiting_payment" && current.checkout_url) {
        const url = safeCheckoutUrl(current.checkout_url);
        if (!url) throw new Error("The payment page's address could not be verified, so it was not opened.");
        setStatus("Opening the secure payment page…");
        checkoutNavigation.assign(url);
        return;
      }
      if (["failed", "expired", "cancelled"].includes(current.status)) {
        payKey.current = newKey();
        throw new Error(
          current.status === "failed"
            ? "The payment could not be started. Nothing was charged; any wallet amount was released. Try again."
            : "The payment ended before it was completed. Nothing was charged. Try again.",
        );
      }
      if (Date.now() - started > PAYMENT_POLL.timeoutMs) {
        throw new Error("The payment page is taking longer than usual. Choose Pay again in a minute - it continues the same payment.");
      }
      await new Promise((r) => setTimeout(r, PAYMENT_POLL.intervalMs));
      current = await storeHostApi.payment(slug ?? "", current.attempt_id);
    }
  }

  async function referralFirst(organizationId: string): Promise<void> {
    // Attribution the buyer followed here survives sign-in and checkout: the server
    // converts it only for a new organization's owner (and never twice).
    let token = "";
    try {
      token = sessionStorage.getItem(REFERRAL_TOKEN) ?? "";
    } catch {
      token = "";
    }
    await publicApi.convertReferral(token, organizationId).catch(() => undefined);
  }

  async function submit() {
    if (!slug || busy) return;
    setProblem(null);
    setStatus(null);
    setBusy(true);
    try {
      // A trial needs no billing account; a purchase is quoted on the saved one.
      if (!trial && (!billingAccount || billingDirty)) {
        if (await saveBilling()) setStatus("Billing details saved. Review the total, then pay.");
        setBusy(false);
        return;
      }
      if (account.organization) await referralFirst(account.organization.organization_id);
      if (ctx.target.kind === "trial") {
        const started = await consoleApi.startTrial(slug, ctx.target.product, ctx.target.plan, orderKey.current);
        router.push(`${storePaths.complete}?trial=${started.id}&org=${encodeURIComponent(slug)}`);
        return;
      }
      if (!request || !quote) return;
      if (useWallet && (walletWanted < 0 || walletWanted > walletMax)) {
        setProblem(`Enter a wallet amount of at most ${shelfPrice(walletMax, quote.currency)}.`);
        setBusy(false);
        return;
      }
      if (gatewayMissing) {
        setProblem("Online payment is not available right now. Pay the full amount from the Platform Wallet, or contact support.");
        setBusy(false);
        return;
      }
      const order = placed.current ?? (await consoleApi.placeOrder(slug, quoteState.couponProblem ? { ...request, coupon_code: undefined } : request, orderKey.current));
      placed.current = order;
      if (!order.requires_payment || !order.invoice_id) {
        router.push(`${storePaths.complete}?order=${order.order_id}&org=${encodeURIComponent(slug)}`);
        return;
      }
      if (!canPay) {
        setProblem("Your order is placed. Paying it needs the owner or a Billing Manager of this organization.");
        setBusy(false);
        return;
      }
      setStatus(fromWallet > 0 && payNow > 0 ? "Reserving the wallet amount and opening the payment page…" : "Processing your payment…");
      const attempt = await storeHostApi.payInvoice(slug, order.invoice_id, payKey.current, fromWallet);
      await follow(attempt);
    } catch (err) {
      setStatus(null);
      if (err instanceof ApiError && (err.body as { reason?: string } | undefined)?.reason === "order_pending") {
        setProblem("Another order of this organization is waiting for payment. Pay or cancel it in My Account first.");
      } else {
        setProblem(err instanceof ApiError ? describeError(err) : err instanceof Error ? err.message : "Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  const blockers: string[] = [];
  if (signedIn && slug && access?.slug === slug && access.data && !canBuy) blockers.push("Only the organization's owner can buy for it. Ask them to complete this purchase.");
  if (signedIn && slug && access?.slug === slug && access.error) blockers.push(access.error);

  return (
    <div className={shell.container}>
      <CheckoutBand step="order" />
      {ctx.returnTo && returnOk && (
        <p className={styles.returnNote}>
          <Icon name="chevronLeft" size={16} /> <a href={ctx.returnTo.url}>Back to {picked?.card.title ?? "the product"}</a>
        </p>
      )}
      {catalog && !picked && (
        <p role="alert" className={`${shell.notice} ${shell.warningNotice}`}>
          <Icon name="alert" size={18} /> This plan is not on sale any more. <Link href={storePaths.products}>Choose another</Link>.
        </p>
      )}
      <div className={styles.grid}>
        <div className={styles.colLeft}>
          <Card n={1} icon="user" title="Account & Organization">
            <AccountStep returnPath={returnPath} />
          </Card>
          <Card n={2} icon="receipt" title="Billing Details">
            {!signedIn || !slug ? (
              <p className={styles.muted}>Sign in and choose your organization first.</p>
            ) : !canSeeBilling ? (
              <p className={styles.muted}>Billing details are kept by the owner and Billing Managers of this organization.</p>
            ) : billingAccount === undefined ? (
              <p aria-live="polite">Loading billing details…</p>
            ) : (
              <BillingStep
                billing={billing}
                setBilling={(b) => {
                  setBilling(b);
                  setBillingDirty(true);
                }}
                problems={billingProblemsShown}
                locked={busy || !canPay}
              />
            )}
          </Card>
        </div>
        <div className={styles.colMid}>
          <Card n={3} icon="cart" title="Order Summary">
            {picked ? <SummaryStep picked={picked} ctx={ctx} quote={quote} /> : <p aria-live="polite">{catalog ? "This plan is not on sale." : "Loading your order…"}</p>}
          </Card>
        </div>
        <div className={styles.colRight}>
          <Card n={4} icon="card" title="Payment" id="payment">
            {!signedIn ? (
              <p className={styles.muted}>Sign in to see the final amount and pay.</p>
            ) : blockers.length > 0 ? (
              <p role="alert" className={`${shell.notice} ${shell.warningNotice}`}>
                {blockers[0]}
              </p>
            ) : trial ? (
              <TrialPay />
            ) : !billingAccount ? (
              <p className={styles.muted}>Save your billing details to see the final amount, including GST.</p>
            ) : quoteState.error ? (
              <p role="alert" className="w360-error-text">
                {quoteState.error}
              </p>
            ) : !quote ? (
              <p aria-live="polite">Pricing your order…</p>
            ) : (
              <div className={styles.pay}>
                <dl className={styles.amounts}>
                  <div>
                    <dt>
                      Plan Amount ({picked?.plan.name ?? quote.description}
                      {ctx.target.kind === "plan" ? ` – ${PERIOD_LABEL[ctx.target.interval] ?? ""}` : ""})
                    </dt>
                    <dd>{shelfPrice((quote.subtotal_minor ?? quote.amount_due_minor) - (quote.setup_fee_minor ?? 0), quote.currency)}</dd>
                  </div>
                  {(quote.setup_fee_minor ?? 0) > 0 && (
                    <div>
                      <dt>One-time set-up fee</dt>
                      <dd>{shelfPrice(quote.setup_fee_minor ?? 0, quote.currency)}</dd>
                    </div>
                  )}
                  {quote.coupon && (
                    <div className={styles.discount}>
                      <dt>Coupon {quote.coupon.code}</dt>
                      <dd>−{shelfPrice(quote.coupon.discount_minor, quote.currency)}</dd>
                    </div>
                  )}
                  {quote.tax && (
                    <div>
                      <dt>{quote.tax.description}</dt>
                      <dd>{shelfPrice(quote.tax.amount_minor, quote.currency)}</dd>
                    </div>
                  )}
                  <div className={styles.total}>
                    <dt>Total Amount</dt>
                    <dd>{quote.requires_payment ? shelfPrice(quote.amount_due_minor, quote.currency) : "Free"}</dd>
                  </div>
                </dl>
                {quote.prorated && quote.requires_payment && <p className={styles.small}>Prorated for the rest of your current billing period.</p>}
                {quoteState.couponProblem && (
                  <p role="alert" className="w360-error-text">
                    {quoteState.couponProblem} The order is priced without it.
                  </p>
                )}
                {quote.requires_payment && (
                  <form
                    className={styles.coupon}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const code = couponText.trim().toUpperCase();
                      if (/^[A-Z0-9][A-Z0-9_-]{2,31}$/.test(code)) {
                        placed.current = null;
                        orderKey.current = newKey();
                        setCoupon(code);
                        setCouponText("");
                      }
                    }}
                  >
                    {quote.coupon ? (
                      <button
                        type="button"
                        className={styles.linkButton}
                        onClick={() => {
                          placed.current = null;
                          orderKey.current = newKey();
                          setCoupon("");
                        }}
                      >
                        Remove coupon {quote.coupon.code}
                      </button>
                    ) : (
                      <>
                        <label className="w360-visually-hidden" htmlFor="store-coupon">
                          Coupon code
                        </label>
                        <input id="store-coupon" className="w360-input" placeholder="Have a coupon?" value={couponText} maxLength={32} autoComplete="off" onChange={(e) => setCouponText(e.target.value)} />
                        <button type="submit" className={shell.secondary} disabled={!couponText.trim()}>
                          Apply
                        </button>
                      </>
                    )}
                  </form>
                )}

                {walletAllowed && (
                  <div className={styles.wallet}>
                    <div className={styles.walletHead}>
                      <span className={styles.walletIcon}>
                        <Icon name="wallet" size={24} />
                      </span>
                      <span className={styles.walletText}>
                        <strong>Use Platform Wallet</strong>
                        <span>Available Wallet Balance: {shelfPrice(walletBalance, quote.currency)}</span>
                      </span>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={useWallet}
                        aria-label="Use Platform Wallet"
                        className={styles.switch}
                        onClick={() => {
                          const on = !useWallet;
                          setUseWallet(on);
                          // Turned on: as much of the total as the wallet can pay.
                          setWalletText(on && walletMax > 0 ? (walletMax / 100).toFixed(walletMax % 100 ? 2 : 0) : "");
                        }}
                      >
                        <span />
                      </button>
                    </div>
                    {useWallet && (
                      <div className={styles.walletAmount}>
                        <label htmlFor="store-wallet-amount">Wallet Amount to Use</label>
                        <span className={styles.walletInput}>
                          <span aria-hidden="true">{quote.currency === "INR" ? "₹" : quote.currency}</span>
                          <input
                            id="store-wallet-amount"
                            inputMode="decimal"
                            value={walletText}
                            onChange={(e) => setWalletText(e.target.value.replace(/[^0-9.]/g, ""))}
                            aria-describedby="wallet-max"
                          />
                        </span>
                        <span id="wallet-max" className={styles.small}>
                          Up to {shelfPrice(walletMax, quote.currency)}. It is reserved, not taken, until the payment is confirmed.
                        </span>
                      </div>
                    )}
                  </div>
                )}
                {quote.requires_payment && (
                  <p className={styles.payNow}>
                    <span>Amount Payable Now</span>
                    <strong>{shelfPrice(payNow, quote.currency)}</strong>
                  </p>
                )}
                {gatewayNeeded && (
                  <fieldset className={styles.payVia}>
                    <legend>Pay via</legend>
                    {option === null ? (
                      <p aria-live="polite">Checking payment methods…</p>
                    ) : option.available ? (
                      <label className={styles.method}>
                        <input type="radio" name="pay-via" checked readOnly />
                        <span>
                          <strong>{option.label}</strong>
                          {option.methods.length > 0 && <span className={styles.small}> ({option.methods.join(", ")})</span>}
                          {option.test_mode && <span className={`${shell.chip} ${shell.chipSoon}`}>Test mode</span>}
                        </span>
                        <Icon name="lock" size={18} />
                      </label>
                    ) : (
                      <p role="alert" className={`${shell.notice} ${shell.warningNotice}`}>
                        Online payment in {quote.currency} is not available yet. Pay the full amount from the Platform Wallet, or contact support.
                      </p>
                    )}
                    {option?.available && <p className={styles.small}>You choose {option.methods.join(", ") || "how to pay"} on {option.label}&apos;s secure page.</p>}
                  </fieldset>
                )}
                {!canPay && quote.requires_payment && (
                  <p className={`${shell.notice} ${shell.warningNotice}`}>Paying needs the owner or a Billing Manager of this organization.</p>
                )}
              </div>
            )}
            {problem && (
              <p role="alert" className="w360-error-text">
                {problem}
              </p>
            )}
            {status && (
              <p role="status" className={styles.small}>
                {status}
              </p>
            )}
            {signedIn && slug && (
              <button
                type="button"
                className={`${shell.primary} ${styles.payButton}`}
                disabled={busy || !canBuy || !picked || (!trial && Boolean(billingAccount) && !quote) || (gatewayMissing && payNow > 0)}
                onClick={() => void submit()}
              >
                <Icon name="lock" size={20} />
                {trial
                  ? "Start Free Trial"
                  : !billingAccount || billingDirty
                    ? "Save Billing Details"
                    : quote
                    ? payLabel(quote, payNow, fromWallet, trial)
                    : "Continue"}
                <Icon name="chevronRight" size={18} />
              </button>
            )}
            {signedIn && slug && (
              <p className={styles.small}>
                Orders and invoices are kept in{" "}
                <button type="button" className={styles.linkButton} onClick={() => void myAccount.open(myAccountLinks(slug)[1].path)}>
                  My Account → Billing &amp; Subscriptions
                </button>
                .
              </p>
            )}
          </Card>
        </div>
      </div>
      <TrustStrip />
    </div>
  );
}

function TrialPay() {
  return (
    <div className={styles.pay}>
      <p className={styles.payNow}>
        <span>Amount Payable Now</span>
        <strong>Nothing</strong>
      </p>
      <p className={styles.small}>A free trial needs no payment and no card. When it ends, you can choose a plan from My Products.</p>
    </div>
  );
}

export function TrustStrip() {
  const items = [
    { icon: "shield", title: "Secure Payment", text: "Paid on the provider's secure page" },
    { icon: "bolt", title: "Instant Activation", text: "Starts once payment is confirmed" },
    { icon: "receipt", title: "Invoices & Renewals", text: "Kept in My Account" },
    { icon: "headset", title: "Support", text: "Help from our team" },
  ];
  return (
    <ul className={`${shell.card} ${styles.trust}`} aria-label="Buying with WorkEasy360">
      {items.map((i) => (
        <li key={i.title}>
          <Icon name={i.icon} size={28} />
          <span>
            <strong>{i.title}</strong>
            <span>{i.text}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
