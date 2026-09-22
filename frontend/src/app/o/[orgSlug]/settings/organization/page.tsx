"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getOrganizationDetail } from "@/lib/api/organizations";
import type { OrganizationDetail } from "@/lib/api/types";

type Load = { status: "loading" } | { status: "error" } | { status: "ready"; org: OrganizationDetail };

/** Task 39: platform-level organization settings - display only. Seats and
 * members live on their own pages; reseller/white-label settings are not
 * Phase 4 (Task 42). */
export default function OrganizationSettingsPage() {
  const params = useParams<{ orgSlug: string }>();
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getOrganizationDetail(params.orgSlug)
      .then((org) => !cancelled && setLoad({ status: "ready", org }))
      .catch(() => !cancelled && setLoad({ status: "error" }));
    return () => {
      cancelled = true;
    };
  }, [params.orgSlug]);

  return (
    <div>
      <h1 style={{ fontSize: 22, marginBottom: "var(--w360-space-5)" }}>Organization settings</h1>
      <div className="w360-card" style={{ padding: "var(--w360-space-5)", maxWidth: 480 }}>
        {load.status === "loading" && <p>Loading…</p>}
        {load.status === "error" && <p role="alert">Couldn&apos;t load organization settings.</p>}
        {load.status === "ready" && (
          <dl style={{ display: "grid", gap: "var(--w360-space-4)", margin: 0 }}>
            <div>
              <dt className="w360-label">Name</dt>
              <dd style={{ margin: 0 }}>{load.org.display_name}</dd>
            </div>
            <div>
              <dt className="w360-label">Slug</dt>
              <dd style={{ margin: 0 }}>{load.org.slug}</dd>
            </div>
            <div>
              <dt className="w360-label">Status</dt>
              <dd style={{ margin: 0 }}>
                <span
                  className={`w360-badge ${
                    load.org.status === "active" ? "w360-badge-success" : "w360-badge-warning"
                  }`}
                >
                  {load.org.status}
                </span>
              </dd>
            </div>
          </dl>
        )}
      </div>
    </div>
  );
}
