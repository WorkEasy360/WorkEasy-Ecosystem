import { Suspense } from "react";
import { CheckoutScreen } from "@/features/store/Checkout";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({ path: "/store/checkout", title: "Checkout - WorkEasy360 Store", description: "Complete your order.", index: false });
}

/** The checkout - a step, never a menu (ADR-065). Reads what to buy from its address. */
export default function StoreCheckoutPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading checkout…</p>}>
      <CheckoutScreen />
    </Suspense>
  );
}
