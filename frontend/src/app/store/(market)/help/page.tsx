import { HelpScreen } from "@/features/store/Help";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({
    path: "/store/help",
    title: "Help & Policies - WorkEasy360 Store",
    description: "How buying, payments, GST, renewals, refunds and recharges work in the WorkEasy360 Store, and how to contact support.",
  });
}

export default function StoreHelpPage() {
  return <HelpScreen />;
}
