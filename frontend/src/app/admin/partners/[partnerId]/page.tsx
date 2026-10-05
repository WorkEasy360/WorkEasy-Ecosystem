"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { PartnerDetailScreen } from "@/features/controlplane/AdminPartners";

/** Suspense: the page reads `?tab=` to open on a chosen section. */
export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  return (
    <Suspense fallback={<p aria-live="polite">Loading this partner…</p>}>
      <PartnerDetailScreen partnerId={partnerId} />
    </Suspense>
  );
}
