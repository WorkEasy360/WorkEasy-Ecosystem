"use client";

import { useParams } from "next/navigation";
import { ConnectionsSettings } from "@/features/integrations/ConnectionsSettings";
import styles from "@/features/integrations/Integrations.module.css";

/** Settings → Integrations (Phase 6, Task 79). */
export default function IntegrationsPage() {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  return (
    <div>
      <h1>Integrations</h1>
      <p className={styles.lead}>
        Send your products&apos; events to systems you control. Every delivery is signed; your
        receiver verifies it with the connection&apos;s secret.
      </p>
      <ConnectionsSettings orgSlug={orgSlug} />
    </div>
  );
}
