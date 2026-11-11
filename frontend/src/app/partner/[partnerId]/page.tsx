"use client";

import { useEffect } from "react";
import { usePartner } from "@/features/controlplane/ControlShell";
import { openingPath } from "@/features/controlplane/PartnerScreens";
import { PartnerDashboard } from "@/features/partnerpanel/Dashboard";
import { usePortalRouter } from "@/lib/portalRouter";

/** The partner portal's landing page. An account still waiting for approval opens
 * its workspace's onboarding dashboard (ADR-063): business profile, KYC, support. */
export default function Page() {
  const { partner } = usePartner();
  const router = usePortalRouter();
  const applying = partner?.status === "draft" ? openingPath(partner) : null;
  useEffect(() => {
    if (applying) router.replace(applying);
  }, [applying, router]);
  return applying ? null : <PartnerDashboard />;
}
