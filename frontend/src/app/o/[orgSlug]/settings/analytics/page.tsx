"use client";

import { useParams } from "next/navigation";
import { AnalyticsShell } from "@/features/intelligence/AnalyticsShell";
import styles from "@/features/intelligence/Intelligence.module.css";

/** Settings → Analytics (Phase 8, Task 90): the metrics your products define. */
export default function AnalyticsPage() {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  return (
    <div>
      <h1>Analytics</h1>
      <p className={styles.lead}>
        Metrics your products publish, aggregated from their events. Each product owns what its metrics mean; its own
        dashboards live in the product.
      </p>
      <AnalyticsShell orgSlug={orgSlug} />
    </div>
  );
}
