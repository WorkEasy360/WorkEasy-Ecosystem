"use client";

import { CommercialRules } from "@/features/controlplane/productmanager/CommercialRules";

/** Super Admin → Store → Commercial rules (ADR-056). */
export default function Page() {
  return <CommercialRules />;
}
