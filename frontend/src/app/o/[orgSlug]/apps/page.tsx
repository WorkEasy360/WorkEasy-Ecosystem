"use client";

import { Suspense, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { getAppDirectory } from "@/lib/api/launcher";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { AppCard } from "@/features/launcher/AppCard";

type Load =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; apps: AppDirectoryEntry[] };

const LAUNCH_ERROR_COPY: Record<string, string> = {
  no_seat: "You don't have a seat for that product yet. Ask an administrator to assign you one.",
  no_entitlement: "Your organization doesn't own that product yet.",
  integration_suspended: "That product is temporarily unavailable. Please try again shortly.",
  product_unavailable: "That product isn't available yet.",
};

function LaunchErrorBanner() {
  const searchParams = useSearchParams();
  const error = searchParams.get("launch_error");
  if (!error) return null;
  return (
    <div
      role="alert"
      className="w360-card"
      style={{
        padding: "var(--w360-space-4)",
        marginBottom: "var(--w360-space-5)",
        borderColor: "var(--w360-danger)",
        background: "var(--w360-danger-bg)",
        color: "var(--w360-danger)",
      }}
    >
      {LAUNCH_ERROR_COPY[error] ?? "That app couldn't be opened right now."}
    </div>
  );
}

export default function AppsPage() {
  const params = useParams<{ orgSlug: string }>();
  const orgSlug = params.orgSlug;
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getAppDirectory(orgSlug)
      .then(({ apps }) => {
        if (!cancelled) setLoad({ status: "ready", apps });
      })
      .catch(() => {
        if (!cancelled) setLoad({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  return (
    <div>
      <Suspense fallback={null}>
        <LaunchErrorBanner />
      </Suspense>
      <h1 style={{ fontSize: 22, marginBottom: "var(--w360-space-5)" }}>All apps</h1>

      {load.status === "loading" && <p aria-live="polite">Loading your apps…</p>}

      {load.status === "error" && (
        <div role="alert" className="w360-card" style={{ padding: "var(--w360-space-5)" }}>
          <p>Couldn&apos;t load your apps right now.</p>
          <button className="w360-btn w360-btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      )}

      {load.status === "ready" && load.apps.length === 0 && (
        <div className="w360-card" style={{ padding: "var(--w360-space-5)" }}>
          <p>No products are available yet.</p>
        </div>
      )}

      {load.status === "ready" && load.apps.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
            gap: "var(--w360-space-4)",
          }}
        >
          {load.apps.map((app) => (
            <AppCard key={app.product_code} orgSlug={orgSlug} app={app} />
          ))}
        </div>
      )}
    </div>
  );
}
