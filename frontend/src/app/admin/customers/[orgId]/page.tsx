"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { CustomerDetailScreen } from "@/features/controlplane/AdminScreens";

/** Suspense: the page reads `?tab=` to open on a chosen tab. */
export default function Page() {
  const { orgId } = useParams<{ orgId: string }>();
  return (
    <Suspense fallback={<p aria-live="polite">Loading this customer…</p>}>
      <CustomerDetailScreen organizationId={orgId} />
    </Suspense>
  );
}
