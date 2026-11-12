import { Suspense } from "react";
import { CheckoutCompleteScreen } from "@/features/store/CheckoutComplete";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({ path: "/store/checkout/complete", title: "Order confirmation - WorkEasy360 Store", description: "Your order.", index: false });
}

/** Where the payment provider returns a Store payer (ADR-065). The return proves
 * nothing: the page asks the server what happened. */
export default function StoreCheckoutCompletePage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading…</p>}>
      <CheckoutCompleteScreen />
    </Suspense>
  );
}
