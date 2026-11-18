import { PartnersScreen } from "@/features/store/Partners";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({
    path: "/store/partners",
    title: "Partners - WorkEasy360 Store",
    description: "Join the WorkEasy360 Affiliate or Reseller programme: one application, KYC review and your own partner dashboard.",
  });
}

export default function StorePartnersPage() {
  return <PartnersScreen />;
}
