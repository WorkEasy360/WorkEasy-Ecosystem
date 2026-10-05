"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";

/**
 * `/dashboard` - where everyone lands after signing in (Phase 9). It decides
 * *which* dashboard, never what anyone may do there (every page re-checks):
 * Workeasy360 staff to the Super Admin console; one organization straight to
 * its dashboard (which also opens a checkout the person started before
 * signing up); several to the chooser; none to creating the first one - or,
 * for someone who only works for a partner, the partner portal.
 */
export default function DashboardEntryPage() {
  const router = useRouter();
  const query = useSession();

  useEffect(() => {
    if (query.status !== "ready") return;
    if (!query.session.authenticated) {
      router.replace("/login");
      return;
    }
    if (query.session.consoles?.staff) {
      router.replace("/admin");
      return;
    }
    getOrganizations()
      .then(({ organizations, has_more }) => {
        if (organizations.length === 0 && !has_more) {
          router.replace(query.session.consoles?.partner ? "/partner" : "/onboarding");
        } else if (organizations.length === 1 && !has_more) {
          router.replace(`/o/${organizations[0].slug}`);
        } else {
          router.replace("/organizations/select");
        }
      })
      .catch(() => router.replace("/organizations/select"));
  }, [query, router]);

  if (query.status === "error") {
    return (
      <StatusScreen role="alert">
        <p>Workeasy360 could not be reached right now. Please try again shortly.</p>
      </StatusScreen>
    );
  }
  return (
    <StatusScreen>
      <p>Opening your dashboard…</p>
    </StatusScreen>
  );
}
