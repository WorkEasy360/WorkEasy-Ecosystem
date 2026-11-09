"use client";

import { useParams } from "next/navigation";
import { KycApplicationScreen } from "@/features/controlplane/AdminKyc";

export default function Page() {
  const { partnerId } = useParams<{ partnerId: string }>();
  return <KycApplicationScreen partnerId={partnerId} />;
}
