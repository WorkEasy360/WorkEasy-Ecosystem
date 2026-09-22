"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";
import type { OrganizationSummary } from "@/lib/api/types";

type Load =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; organizations: OrganizationSummary[] };

function Chooser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resume = searchParams.get("resume");
  const session = useSession();
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    if (session.status !== "ready") return;
    if (!session.session.authenticated) {
      router.replace(resume ? `/login?resume=${encodeURIComponent(resume)}` : "/login");
      return;
    }
    getOrganizations()
      .then(({ organizations }) => setLoad({ status: "ready", organizations }))
      .catch(() => setLoad({ status: "error" }));
  }, [session, router, resume]);

  function choose(org: OrganizationSummary) {
    if (resume) {
      // A genuine top-level navigation, not client-side routing (Task 31/
      // 32): this must reach the Django resume endpoint as a real browser
      // request and be free to follow whatever it redirects to next -
      // Next.js's router cannot express that.
      // eslint-disable-next-line react-hooks/immutability, @next/next/no-location-assign-relative-destination
      window.location.href = `/api/v1/integrations/oauth/resume?resume=${encodeURIComponent(
        resume,
      )}&organization_id=${encodeURIComponent(org.organization_id)}`;
    } else {
      router.push(`/o/${org.slug}/apps`);
    }
  }

  if (load.status === "loading" || session.status === "loading") {
    return <StatusScreen>Loading your organizations…</StatusScreen>;
  }
  if (load.status === "error") {
    return (
      <StatusScreen role="alert">
        <div>
          <p>Couldn&apos;t load your organizations.</p>
          <button className="w360-btn w360-btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </StatusScreen>
    );
  }

  if (load.organizations.length === 0) {
    return (
      <StatusScreen>
        <div>
          <h1 style={{ fontSize: 18 }}>No organizations yet</h1>
          <p style={{ color: "var(--w360-text-muted)" }}>
            You aren&apos;t a member of any Workeasy360 organization. Ask an administrator to
            invite you.
          </p>
        </div>
      </StatusScreen>
    );
  }

  return (
    <main id="w360-main" className="w360-container" style={{ paddingTop: "var(--w360-space-7)" }}>
      <h1 style={{ fontSize: 22 }}>Choose an organization</h1>
      <p style={{ color: "var(--w360-text-muted)", marginBottom: "var(--w360-space-5)" }}>
        {resume
          ? "Pick the organization to continue signing in to the product you opened."
          : "Pick which organization you'd like to work in."}
      </p>
      <ul
        style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "var(--w360-space-3)" }}
      >
        {load.organizations.map((org) => (
          <li key={org.organization_id}>
            <button
              className="w360-card"
              onClick={() => choose(org)}
              style={{
                width: "100%",
                textAlign: "left",
                padding: "var(--w360-space-5)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "var(--w360-space-4)",
              }}
            >
              <span>
                <span style={{ fontWeight: 600, fontSize: 16 }}>{org.display_name}</span>
                {org.is_owner && (
                  <span className="w360-badge w360-badge-neutral" style={{ marginLeft: 8 }}>
                    Owner
                  </span>
                )}
              </span>
              <span aria-hidden="true" style={{ color: "var(--w360-text-faint)" }}>
                →
              </span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}

export default function OrganizationSelectPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <Chooser />
    </Suspense>
  );
}
