"use client";

import { useParams } from "next/navigation";
import { StaffWhiteLabelPage } from "@/features/controlplane/WhiteLabelWizard";

/** Partners → White-label → set up one partner (staff). */
export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  return <StaffWhiteLabelPage partnerId={partnerId} />;
}
