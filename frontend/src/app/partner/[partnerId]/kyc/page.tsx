"use client";

import { useParams } from "next/navigation";
import { PartnerKycScreen } from "@/features/partnerpanel/KycApplication";

export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  return <PartnerKycScreen partnerId={partnerId} />;
}
