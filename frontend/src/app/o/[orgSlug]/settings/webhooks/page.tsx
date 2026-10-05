"use client";

import { useParams } from "next/navigation";
import { DeliveryLog } from "@/features/integrations/DeliveryLog";
import styles from "@/features/integrations/Integrations.module.css";

/** Settings → Webhook deliveries (Phase 6, Task 81). */
export default function WebhookDeliveriesPage() {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  return (
    <div>
      <h1>Webhook deliveries</h1>
      <p className={styles.lead}>
        Deliveries are retried automatically with backoff. A delivery that has failed for good can be
        replayed once the endpoint is fixed.
      </p>
      <DeliveryLog orgSlug={orgSlug} />
    </div>
  );
}
