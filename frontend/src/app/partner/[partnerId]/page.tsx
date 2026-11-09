"use client";

import { useEffect } from "react";
import { usePartner } from "@/features/controlplane/ControlShell";
import { PartnerDashboard } from "@/features/partnerpanel/Dashboard";
import { usePortalRouter } from "@/lib/portalRouter";

/** The partner portal's landing page. An account still waiting for approval has no
 * dashboard to show, so it goes to the application that gets it approved. */
export default function Page() {
  const { partner } = usePartner();
  const router = usePortalRouter();
  const applying = partner?.status === "draft" ? partner.partner_id : null;
  useEffect(() => {
    if (applying) router.replace(`/partner/${applying}/kyc`);
  }, [applying, router]);
  return applying ? null : <PartnerDashboard />;
}
