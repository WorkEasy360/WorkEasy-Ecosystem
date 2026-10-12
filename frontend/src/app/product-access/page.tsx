import { Suspense } from "react";
import { StatusScreen } from "@/components/StatusScreen";
import { ProductAccessChooser } from "@/features/signin/ProductAccessChooser";

/** The access chooser (ADR-044), on the central sign-in host. */
export default function ProductAccessPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <ProductAccessChooser />
    </Suspense>
  );
}
