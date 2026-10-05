"use client";

import { useParams } from "next/navigation";
import { PartnerShell } from "@/features/controlplane/ControlShell";

export default function PartnerLayout({ children }: { children: React.ReactNode }) {
  const { partnerId } = useParams<{ partnerId: string }>();
  return <PartnerShell partnerId={partnerId}>{children}</PartnerShell>;
}
