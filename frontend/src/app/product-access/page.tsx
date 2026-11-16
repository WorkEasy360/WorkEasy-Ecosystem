import { Suspense } from "react";
import { StatusScreen } from "@/components/StatusScreen";
import { accountsTitle } from "@/features/signin/AccountsBrand";
import { ProductAccessChooser } from "@/features/signin/ProductAccessChooser";

export const metadata = { title: accountsTitle("Continue"), robots: { index: false } };

/** The access chooser (ADR-044), on the central sign-in host. */
export default function ProductAccessPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <ProductAccessChooser />
    </Suspense>
  );
}
