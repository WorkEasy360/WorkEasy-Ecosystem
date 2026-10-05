"use client";

import { useParams } from "next/navigation";
import { CustomerDetailScreen } from "@/features/controlplane/AdminScreens";

export default function Page() {
  const { orgId } = useParams<{ orgId: string }>();
  return <CustomerDetailScreen organizationId={orgId} />;
}
