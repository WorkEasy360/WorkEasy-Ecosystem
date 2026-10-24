"use client";

import Link from "next/link";
import { useLoad } from "@/features/controlplane/ui";
import { panelApi, type WhiteLabelStatus } from "@/lib/api/partnerPanel";
import { PartnerDashboard } from "./Dashboard";
import { Kpi, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

const BRAND: Record<"not_started" | "in_progress" | "complete", string> = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
};

const DOMAIN: Record<string, string> = {
  none: "No domain requested",
  requested: "Requested",
  verification_pending: "Verification pending",
  verified: "Verified, not active yet",
  active: "Active",
};

function Status({ s, base }: { s: WhiteLabelStatus; base: string }) {
  return (
    <section aria-labelledby="wl-status">
      <h2 id="wl-status">White-label status</h2>
      <ul className={styles.kpis} aria-label="White-label status">
        {s.brand && (
          <Kpi
            icon="palette"
            label="Brand setup"
            value={BRAND[s.brand.state]}
            note={
              s.brand.state === "complete"
                ? s.brand.name
                : s.brand.state === "in_progress"
                  ? "Add a logo to finish"
                  : "Set your brand name and colours"
            }
          />
        )}
        {s.domain && (
          <Kpi icon="network" label="Custom domain" value={DOMAIN[s.domain.state] ?? s.domain.state} note={s.domain.hostname || undefined} />
        )}
      </ul>
      <p>
        {s.brand && <Link href={`${base}/brand-settings`}>Open brand settings</Link>}
        {s.brand && s.domain && " · "}
        {s.domain && <Link href={`${base}/brand-settings/url-manager`}>Open custom domain</Link>}
      </p>
    </section>
  );
}

/** The white-label dashboard: the shared one, with where the brand setup and the
 * custom domain stand. Each half needs its own permission; without either, this is
 * the plain dashboard. */
export function WhiteLabelDashboard() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.whiteLabelStatus(id), [id]);
  const status = load.status === "ready" && (load.data.brand || load.data.domain) ? load.data : null;
  return <PartnerDashboard extra={status ? <Status s={status} base={base} /> : undefined} />;
}
