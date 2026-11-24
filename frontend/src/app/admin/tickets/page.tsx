"use client";

import { Suspense } from "react";
import { TicketsScreen } from "@/features/controlplane/AdminSupport";

/** Support › Tickets. Suspense: the list reads Who asked from the query (`?audience=`). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading tickets…</p>}>
      <TicketsScreen />
    </Suspense>
  );
}
