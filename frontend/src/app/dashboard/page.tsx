"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { usePortals } from "@/lib/portalContext";
import { StatusScreen } from "@/components/StatusScreen";
import { usePortalRouter } from "@/lib/portalRouter";

/**
 * `/dashboard` - where everyone lands after signing in (Phase 9). It decides
 * *which* dashboard, never what anyone may do there (every page re-checks):
 * Workeasy360 staff to the Super Admin console; one organization straight to
 * its dashboard (which also opens a checkout the person started before
 * signing up); several to the chooser; none to creating the first one - or,
 * for someone who only works for a partner, the partner portal.
 *
 * `?mode=user` (the sign-in page's "User" choice, or "Continue as User") skips
 * the first rule: an administrator who chose User gets the same customer
 * experience as anyone else. The parameter only *removes* a redirect to the
 * console; it can never lead anywhere a plain visit could not.
 *
 * Multi-portal mode (ADR-043): this page is on the customer portal, and only
 * the customer experience is here - nobody is sent to another portal. Staff
 * and partners see exactly what their organization memberships give them;
 * with none, they create one like anyone else.
 */
function DashboardEntry() {
  const router = usePortalRouter();
  const query = useSession();
  const portals = usePortals();
  const asUser = useSearchParams().get("mode") === "user" || portals !== null;

  useEffect(() => {
    if (query.status !== "ready") return;
    if (!query.session.authenticated) {
      router.replace(asUser ? "/login?mode=user" : "/login");
      return;
    }
    if (query.session.consoles?.staff && !asUser) {
      router.replace("/admin");
      return;
    }
    getOrganizations()
      .then(({ organizations, has_more }) => {
        if (organizations.length === 0 && !has_more) {
          router.replace(query.session.consoles?.partner && !portals ? "/partner" : "/onboarding");
        } else if (organizations.length === 1 && !has_more) {
          router.replace(`/o/${organizations[0].slug}`);
        } else {
          router.replace("/organizations/select");
        }
      })
      .catch(() => router.replace("/organizations/select"));
  }, [query, router, asUser, portals]);

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

export default function DashboardEntryPage() {
  return (
    <Suspense
      fallback={
        <StatusScreen>
          <p>Opening your dashboard…</p>
        </StatusScreen>
      }
    >
      <DashboardEntry />
    </Suspense>
  );
}
