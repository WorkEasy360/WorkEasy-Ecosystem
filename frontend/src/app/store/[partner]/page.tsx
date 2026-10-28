"use client";

import { useParams } from "next/navigation";
import { StorefrontScreen } from "@/features/storefront/Storefront";

/** ADR-053: a white-label partner's branded Store, `/store/<partner>`. Public to
 * browse; buying signs in and checks out on the platform. */
export default function PartnerStorePage() {
  const params = useParams<{ partner: string }>();
  return <StorefrontScreen slug={String(params?.partner ?? "")} />;
}
