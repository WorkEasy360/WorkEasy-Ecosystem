import { StoreHomeScreen } from "@/features/store/StoreHome";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({
    path: "/store",
    title: "WorkEasy360 Store - Business apps, plans and credits",
    description: "Discover WorkEasy360 business apps, compare plans and prices, buy subscriptions, recharge wallets and join the partner programme.",
  });
}

/** `/store` - and the Store host's root, https://store.workeasy360.com/ (ADR-065). */
export default function StoreHomePage() {
  return <StoreHomeScreen />;
}
