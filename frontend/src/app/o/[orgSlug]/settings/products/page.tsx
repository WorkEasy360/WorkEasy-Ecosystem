"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { SeatTable } from "@/features/launcher/SeatTable";

type Load = { status: "loading" } | { status: "error" } | { status: "ready"; apps: AppDirectoryEntry[] };

/** Task 13/14: product access and seat assignment, one product at a time.
 * Only per-seat products the organization actually holds are listed - a
 * product nobody has bought has no seats to assign. */
export default function ProductsSettingsPage() {
  const params = useParams<{ orgSlug: string }>();
  const orgSlug = params.orgSlug;
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    getAppDirectory(orgSlug)
      .then(({ apps }) => {
        const seated = apps.filter(
          (a) => a.licensing_mode === "per_seat" && (a.state === "active" || a.state === "no_seat"),
        );
        setLoad({ status: "ready", apps: seated });
        setSelected((current) => current ?? seated[0]?.product_code ?? null);
      })
      .catch(() => setLoad({ status: "error" }));
  }, [orgSlug]);

  return (
    <div>
      <h1 style={{ fontSize: 22, marginBottom: "var(--w360-space-2)" }}>Product access</h1>
      <p style={{ color: "var(--w360-text-muted)", marginBottom: "var(--w360-space-5)" }}>
        Manage who holds a seat for each per-seat product your organization owns.
      </p>

      {load.status === "loading" && <p aria-live="polite">Loading products…</p>}
      {load.status === "error" && <p role="alert">Couldn&apos;t load products right now.</p>}

      {load.status === "ready" && load.apps.length === 0 && (
        <div className="w360-card" style={{ padding: "var(--w360-space-5)" }}>
          <p>Your organization doesn&apos;t hold any per-seat products yet.</p>
        </div>
      )}

      {load.status === "ready" && load.apps.length > 0 && (
        <div>
          <div
            role="tablist"
            aria-label="Products"
            style={{ display: "flex", gap: "var(--w360-space-2)", marginBottom: "var(--w360-space-5)" }}
          >
            {load.apps.map((app) => (
              <button
                key={app.product_code}
                role="tab"
                aria-selected={selected === app.product_code}
                className="w360-btn w360-btn-secondary"
                style={
                  selected === app.product_code
                    ? { background: "var(--w360-brand)", color: "var(--w360-brand-contrast)" }
                    : undefined
                }
                onClick={() => setSelected(app.product_code)}
              >
                {app.display_name}
              </button>
            ))}
          </div>
          {selected && <SeatTable orgSlug={orgSlug} productCode={selected} />}
        </div>
      )}
    </div>
  );
}
