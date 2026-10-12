"use client";

import { Suspense } from "react";
import { AddPartner } from "@/features/controlplane/AddPartner";

/** Partners → Add partner: choose a business purpose (a capability preset),
 * then set the partner up. Suspense: the page reads `?purpose=`. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading…</p>}>
      <AddPartner />
    </Suspense>
  );
}
