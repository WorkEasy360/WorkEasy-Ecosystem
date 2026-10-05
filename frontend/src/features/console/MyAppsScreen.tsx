"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import { SeatTable } from "@/features/launcher/SeatTable";
import { launchDenialMessage } from "@/features/launcher/launchCopy";
import { MyProductsScreen } from "./CommerceScreens";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/** Why a launch the person just tried was refused (`?launch_error=`) - the
 * pre-JS fallback path (a bookmarked or shared link); the button itself
 * shows the same message inline without a redirect. Never the raw code. */
function LaunchErrorBanner() {
  const error = useSearchParams().get("launch_error");
  if (!error) return null;
  return (
    <div role="alert" className={local.problem}>
      <p>{launchDenialMessage(error)}</p>
    </div>
  );
}

/** My products (Phase 9: only what the organization holds - the rest is in
 * Explore), with why a launch the person just tried was refused. */
export function MyAppsScreen() {
  return <MyProductsScreen banner={<LaunchErrorBanner />} />;
}

/** Product access: who holds a seat for each per-seat product the
 * organization owns. A product nobody has bought has no seats to assign. */
export function ProductAccessScreen() {
  const { slug } = useOrgAccess();
  const [load] = useLoad(async () => {
    const { apps } = await getAppDirectory(slug);
    return apps.filter((a) => a.licensing_mode === "per_seat" && (a.state === "active" || a.state === "no_seat"));
  }, [slug]);
  const [chosen, setChosen] = useState<string | null>(null);

  return (
    <div className={styles.page}>
      <PageHeader
        title="Product access"
        lead="Choose who holds a seat for each per-seat product your organization owns."
      />
      <LoadGate load={load} what="products">
        {(apps) => {
          if (apps.length === 0) {
            return <p className={cp.muted}>Your organization doesn&apos;t hold any per-seat products yet.</p>;
          }
          const selected = apps.find((a) => a.product_code === chosen) ?? apps[0];
          return (
            <>
              {apps.length > 1 && (
                <div>
                  <div className={styles.segmented} role="group" aria-label="Product">
                    {apps.map((app) => (
                      <button
                        key={app.product_code}
                        type="button"
                        aria-pressed={selected.product_code === app.product_code}
                        onClick={() => setChosen(app.product_code)}
                      >
                        {app.display_name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className={local.section}>
                <h2 className={local.sectionTitle}>{selected.display_name}</h2>
                <SeatTable key={selected.product_code} orgSlug={slug} productCode={selected.product_code} />
              </div>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
