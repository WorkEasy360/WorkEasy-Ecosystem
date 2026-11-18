"use client";

import { Suspense } from "react";
import { ReportsScreen } from "@/features/controlplane/AdminReports";

/** Reports (admin-ux phase 1): one page, the report chosen on it (`?report=`, Sales by
 * default). Suspense: the page reads the report from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading the report…</p>}>
      <ReportsScreen />
    </Suspense>
  );
}
