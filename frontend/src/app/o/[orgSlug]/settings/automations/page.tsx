"use client";

import { useParams } from "next/navigation";
import { AutomationsSettings } from "@/features/integrations/AutomationsSettings";
import styles from "@/features/integrations/Integrations.module.css";

/** Settings → Automations (Phase 6, Task 80). */
export default function AutomationsPage() {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  return (
    <div>
      <h1>Automations</h1>
      <p className={styles.lead}>
        When an event arrives - or on a schedule - check a few conditions and send the event to one of
        your connections. New automations start as drafts.
      </p>
      <AutomationsSettings orgSlug={orgSlug} />
    </div>
  );
}
