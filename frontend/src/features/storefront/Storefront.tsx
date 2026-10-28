"use client";

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { getOrganizations } from "@/lib/api/auth";
import { publicApi, type Storefront } from "@/lib/api/public";
import { useSession } from "@/lib/auth/useSession";
import { usePortalRouter } from "@/lib/portalRouter";
import { contrastOn, safeColor } from "@/features/controlplane/useBranding";
import { money } from "@/features/controlplane/ui";
import { StatusScreen } from "@/components/StatusScreen";
import styles from "./Storefront.module.css";

/**
 * A white-label partner's branded Store (ADR-053, Store Working Flow page 7).
 *
 * The partner's name, logo and colour over the **central** catalog: only the
 * products the partner may sell, at its prices - both decided by the server.
 * Buying is the platform's own checkout with `storefront=<partner>` added; the
 * server verifies the store again, prices the order from the partner's book and
 * makes the buyer the partner's customer when the order is fulfilled. Branding
 * here is presentation only: it never decides what anyone may buy or use.
 */

const INTERVAL_LABEL: Record<string, string> = {
  monthly: "per month",
  quarterly: "per quarter",
  semi_annual: "every six months",
  annual: "per year",
};

type Orgs = { slug: string; name: string }[];

export function StorefrontScreen({ slug }: { slug: string }) {
  const router = usePortalRouter();
  const session = useSession();
  const [store, setStore] = useState<Storefront | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [orgs, setOrgs] = useState<Orgs | null>(null);
  const [org, setOrg] = useState("");
  const orgId = useId();

  useEffect(() => {
    let live = true;
    publicApi.storefront(slug).then(
      (data) => {
        if (!live) return;
        setStore(data);
        setState("ready");
      },
      (err: unknown) => {
        if (!live) return;
        setState(err instanceof ApiError && err.status === 404 ? "missing" : "error");
      },
    );
    return () => {
      live = false;
    };
  }, [slug]);

  const signedIn = session.status === "ready" && session.session.authenticated;
  useEffect(() => {
    if (!signedIn) return;
    getOrganizations().then(
      ({ organizations }) => {
        const list = organizations.map((o) => ({ slug: o.slug, name: o.display_name }));
        setOrgs(list);
        if (list.length === 1) setOrg(list[0].slug);
      },
      () => setOrgs([]),
    );
  }, [signedIn]);

  if (state === "loading") {
    return (
      <StatusScreen>
        <p>Opening the store…</p>
      </StatusScreen>
    );
  }
  if (state !== "ready" || !store) {
    return (
      <StatusScreen role="alert">
        <p>{state === "missing" ? "This store is not available." : "The store could not be reached right now. Please try again shortly."}</p>
      </StatusScreen>
    );
  }

  // A signed-in buyer buys for one of its organizations: Buy waits until they
  // are known (and one is chosen when there are several).
  const waiting = signedIn && (orgs === null || (!org && orgs.length > 1));
  const accent = safeColor(store.brand.accent_color) ?? safeColor(store.brand.theme?.primary) ?? "#0f766e";
  const onAccent = contrastOn(accent);
  const portal = store.brand.portal;

  function buy(planVersionId: string, interval: string, trial: boolean) {
    const query = new URLSearchParams({ plan: planVersionId, interval, storefront: store!.slug });
    if (trial) query.set("trial", "1");
    if (!signedIn) {
      router.push(`/login?mode=user&next=${encodeURIComponent(`/store/${store!.slug}`)}`);
      return;
    }
    if (!org) {
      if (orgs && orgs.length === 0) router.push("/onboarding");
      return;
    }
    router.push(`/o/${org}/checkout?${query.toString()}`);
  }

  return (
    <div className={styles.page} style={{ ["--store-accent" as string]: accent, ["--store-on-accent" as string]: onAccent }}>
      <header className={styles.header}>
        {store.brand.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- same-origin brand logo
          <img className={styles.logo} src={store.brand.logo_url} alt={`${store.brand.brand_name} logo`} />
        ) : (
          <span className={styles.monogram} aria-hidden="true">
            {(store.brand.brand_name.trim()[0] ?? "?").toUpperCase()}
          </span>
        )}
        <div>
          <h1 className={styles.title}>{store.brand.brand_name} Store</h1>
          {portal.welcome_message && <p className={styles.lead}>{portal.welcome_message}</p>}
        </div>
      </header>

      {signedIn && orgs && orgs.length > 1 && (
        <div className={styles.orgPicker}>
          <label className="w360-label" htmlFor={orgId}>
            Buying for
          </label>
          <select id={orgId} className="w360-input" value={org} onChange={(e) => setOrg(e.target.value)}>
            <option value="">Choose an organization…</option>
            {orgs.map((o) => (
              <option key={o.slug} value={o.slug}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {!signedIn && <p className={styles.note}>Sign in or create a Workeasy360 account to buy. You can browse without one.</p>}

      {store.products.length === 0 ? (
        <p>No products are on sale in this store right now.</p>
      ) : (
        <ul className={styles.products} aria-label="Products">
          {store.products.map((product) => (
            <li key={product.code} className={styles.product}>
              <div className={styles.productHead}>
                {product.logo_url && (
                  // eslint-disable-next-line @next/next/no-img-element -- same-origin product logo
                  <img className={styles.productLogo} src={product.logo_url} alt="" />
                )}
                <h2 className={styles.productName}>{product.name}</h2>
              </div>
              {product.summary && <p>{product.summary}</p>}
              <ul className={styles.plans} aria-label={`${product.name} plans`}>
                {product.plans.map((plan) => (
                  <li key={plan.plan_version_id} className={styles.plan}>
                    <h3 className={styles.planName}>{plan.plan_name}</h3>
                    {plan.prices.length === 0 ? (
                      <p className={styles.note}>Contact {store.brand.brand_name} for pricing.</p>
                    ) : (
                      plan.prices.map((price) => (
                        <div key={`${price.currency}-${price.interval}`} className={styles.priceRow}>
                          <span>
                            <strong>{money(price.amount_minor, price.currency)}</strong> {INTERVAL_LABEL[price.interval] ?? price.interval}
                          </span>
                          <button
                            type="button"
                            className={styles.buy}
                            disabled={waiting}
                            onClick={() => buy(plan.plan_version_id, price.interval, false)}
                          >
                            Buy<span className="w360-visually-hidden"> {product.name} {plan.plan_name} {INTERVAL_LABEL[price.interval] ?? price.interval}</span>
                          </button>
                        </div>
                      ))
                    )}
                    {plan.trial_days > 0 && plan.prices.length > 0 && (
                      <button
                        type="button"
                        className="w360-btn w360-btn-secondary"
                        disabled={waiting}
                        onClick={() => buy(plan.plan_version_id, plan.prices[0].interval, true)}
                      >
                        Start {plan.trial_days}-day free trial<span className="w360-visually-hidden"> of {product.name} {plan.plan_name}</span>
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}

      <footer className={styles.footer}>
        {portal.footer_text && <p>{portal.footer_text}</p>}
        <ul className={styles.footerLinks}>
          {store.brand.support_email && (
            <li>
              <a href={`mailto:${store.brand.support_email}`}>Support: {store.brand.support_email}</a>
            </li>
          )}
          {portal.support_phone && <li>Phone: {portal.support_phone}</li>}
          {portal.website_url && (
            <li>
              <a href={portal.website_url} rel="noopener noreferrer">
                Website
              </a>
            </li>
          )}
          {portal.terms_url && (
            <li>
              <a href={portal.terms_url} rel="noopener noreferrer">
                Terms
              </a>
            </li>
          )}
          {portal.privacy_url && (
            <li>
              <a href={portal.privacy_url} rel="noopener noreferrer">
                Privacy
              </a>
            </li>
          )}
        </ul>
        <p className={styles.note}>Checkout, payment and your account are provided by Workeasy360.</p>
      </footer>
    </div>
  );
}
