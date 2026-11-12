import { RechargeScreen } from "@/features/store/Recharge";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({
    path: "/store/recharge",
    title: "Recharge - WorkEasy360 Store",
    description: "Recharge your Platform Wallet, buy AI credit packs and fund your Consumption Wallet for messaging, API and webhooks.",
  });
}

export default function StoreRechargePage() {
  return <RechargeScreen />;
}
