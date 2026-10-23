"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { CompleteProfile } from "@/features/controlplane/profile/CompleteProfile";

/** Partners -> one partner -> Complete Your Profile (ADR-048). Suspense: the
 * page reads `?step=` to open on a chosen step. Keyed by the partner, so moving
 * between two partners never carries a step's typing over. */
export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  return (
    <Suspense fallback={<p aria-live="polite">Loading the profile…</p>}>
      <CompleteProfile key={partnerId} partnerId={partnerId} />
    </Suspense>
  );
}
