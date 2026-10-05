"use client";

import { useParams } from "next/navigation";
import { AiSettings } from "@/features/intelligence/AiSettings";
import styles from "@/features/intelligence/Intelligence.module.css";

/** Settings → AI (Phase 8, Tasks 88-89). */
export default function AiPage() {
  const { orgSlug } = useParams<{ orgSlug: string }>();
  return (
    <div>
      <h1>AI</h1>
      <p className={styles.lead}>
        Whether your products may use AI for this organization, how much it may cost each month, and which providers
        may process your data. Requests are listed as metadata only; no prompt or answer is stored here.
      </p>
      <AiSettings orgSlug={orgSlug} />
    </div>
  );
}
