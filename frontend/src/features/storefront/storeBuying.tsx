"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { usePortalRouter } from "@/lib/portalRouter";
import { Modal } from "@/features/controlplane/Modal";
import { SignupForm } from "@/features/auth/SelfService";
import {
  MAX_CART_ITEMS,
  PUBLIC_CART,
  clearPublicCart,
  useCart,
} from "@/features/console/cart";
import {
  intentQuery,
  rememberIntent,
  takeIntent,
  type PurchaseIntent,
} from "@/features/site/SiteShell";
import styles from "./PublicStore.module.css";

/**
 * Buying from the public Store (2026-11-02), shared by the Store and a
 * product's page. What was chosen - a plan, a product's trial, or the cart - is
 * only an intent: identifiers, never a price. The checkout prices every line
 * again on the server and the trial is re-checked when it starts.
 *
 * - A visitor without a session gets the signup form in a dialog (with a way
 *   to sign in instead); the intent waits in this browser, and their
 *   organization's front door acts on it once they are in.
 * - Someone signed in with one organization goes straight to its checkout (a
 *   trial starts at its front door); with none or several, the dashboard picks.
 */
export function useStoreBuy() {
  const router = usePortalRouter();
  const session = useSession();
  const signedIn = session.status === "ready" && session.session.authenticated;
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const titleId = useId();

  async function buy(intent: PurchaseIntent) {
    rememberIntent(intent);
    if (!signedIn) {
      setAsking(true);
      return;
    }
    setBusy(true);
    try {
      const { organizations, has_more } = await getOrganizations();
      if (organizations.length === 1 && !has_more) {
        const slug = organizations[0].slug;
        if (intent.productTrial) {
          router.push(`/o/${slug}`); // starts the trial, then opens My products
          return;
        }
        // Straight to the checkout: the remembered intent is no longer needed.
        takeIntent();
        if (intent.cart) clearPublicCart();
        router.push(`/o/${slug}/checkout?${intentQuery(intent)}`);
        return;
      }
    } catch {
      // The dashboard below decides instead.
    } finally {
      setBusy(false);
    }
    router.push("/dashboard?mode=user");
  }

  const dialog = asking ? (
    <Modal labelledBy={titleId} onClose={() => setAsking(false)}>
      <div className={styles.dialogBody}>
        <div className={styles.dialogHead}>
          <h2 id={titleId} className={styles.dialogTitle}>
            Create your account to continue
          </h2>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => setAsking(false)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className={styles.dialogLead}>
          Your choice is saved. Confirm your email and your checkout opens - or{" "}
          <Link href="/login?mode=user">sign in</Link> if you already have an
          account.
        </p>
        <SignupForm signInHref="/login?mode=user" />
      </div>
    </Modal>
  ) : null;

  return { buy, busy, signedIn, dialog };
}

/** The public Store's cart: what is in it, and one checkout for all of it. */
export function PublicCartBar({
  onCheckout,
  busy,
}: {
  onCheckout: (intent: PurchaseIntent) => void;
  busy: boolean;
}) {
  const { cart, remove, clear } = useCart(PUBLIC_CART);
  const titleId = useId();
  if (cart.items.length === 0) return null;
  const count = cart.items.length;
  return (
    <section id="cart" className={styles.cart} aria-labelledby={titleId}>
      <div className={styles.cartHead}>
        <h2 id={titleId} className={styles.cartTitle}>
          Your cart ({count} product{count === 1 ? "" : "s"})
        </h2>
        <div className={styles.cartActions}>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            disabled={busy}
            onClick={() =>
              onCheckout({
                cart: cart.items.map((i) => i.plan_version_id),
                interval: cart.interval,
              })
            }
          >
            Buy all selected ({count})
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={clear}
          >
            Empty cart
          </button>
        </div>
      </div>
      <ul className={styles.cartItems}>
        {cart.items.map((item) => (
          <li key={item.plan_version_id} className={styles.cartItem}>
            <span>
              <strong>{item.product_name}</strong> - {item.plan_name}
            </span>
            <button
              type="button"
              className={styles.cartRemove}
              aria-label={`Remove ${item.product_name} from the cart`}
              onClick={() => remove(item.plan_version_id)}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      <p className={styles.cartHint}>
        One order and one payment for everything here, billed{" "}
        {cart.interval.replace(/_/g, "-")}. Up to {MAX_CART_ITEMS} products, one
        plan each; the checkout shows the final price.
      </p>
    </section>
  );
}

/** "Add to cart" for one plan of one product (one plan per product). */
export function AddToCart({
  planVersionId,
  productCode,
  productName,
  planName,
  interval,
  label,
}: {
  planVersionId: string;
  productCode: string;
  productName: string;
  planName: string;
  interval: string;
  label: string;
}) {
  const cart = useCart(PUBLIC_CART);
  const inCart = cart.has(planVersionId);
  const full =
    !inCart &&
    cart.cart.items.filter((i) => i.product_code !== productCode).length >=
      MAX_CART_ITEMS;
  return (
    <button
      type="button"
      className="w360-btn w360-btn-secondary"
      disabled={inCart || full}
      aria-label={inCart ? `In cart: ${label}` : `Add to cart: ${label}`}
      onClick={() =>
        cart.add(
          {
            plan_version_id: planVersionId,
            product_code: productCode,
            product_name: productName,
            plan_name: planName,
          },
          interval,
        )
      }
    >
      {inCart ? "✓ In cart" : "Add to cart"}
    </button>
  );
}
