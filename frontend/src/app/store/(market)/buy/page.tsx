import { Suspense } from "react";
import { BuyEntryScreen } from "@/features/store/BuyEntry";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({ path: "/store/buy", title: "Opening checkout - WorkEasy360 Store", description: "Opening your checkout.", index: false });
}

/** Where a product's own site sends a buyer, with codes only (ADR-065). */
export default function StoreBuyPage() {
  return (
    <Suspense fallback={null}>
      <BuyEntryScreen />
    </Suspense>
  );
}
