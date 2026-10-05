"use client";

import { Suspense } from "react";
import { MyAppsScreen } from "@/features/console/MyAppsScreen";

/** My products. Suspense: the page reads `?launch_error=` after a refused launch. */
export default function AppsPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading your products…</p>}>
      <MyAppsScreen />
    </Suspense>
  );
}
