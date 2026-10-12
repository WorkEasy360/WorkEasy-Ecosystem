"use client";

import { useParams } from "next/navigation";
import { WhiteLabelSetup } from "@/features/controlplane/WhiteLabelSetup";

/** Partners → White-label → set up one partner (staff): the ten-step wizard,
 * with this partner already chosen, so it opens at the brand identity step. */
export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  // Keyed by the partner, so moving between two partners never carries a draft over.
  return <WhiteLabelSetup key={partnerId} partnerId={partnerId} />;
}
