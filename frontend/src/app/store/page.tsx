"use client";

import { useEffect } from "react";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";
import { usePortalRouter } from "@/lib/portalRouter";

/**
 * `/store` - the Store's address (Store Working Flow PDF, page 2:
 * "app.workeasy360.com/store"). The store itself is an organization's Explore
 * page, because a purchase belongs to an organization: one organization opens
 * its store, several choose first, a signed-out visitor signs in. It decides
 * where to go, never what anyone may buy - Explore and checkout re-check.
 */
export default function StoreEntry() {
  const router = usePortalRouter();
  const query = useSession();

  useEffect(() => {
    if (query.status !== "ready") return;
    if (!query.session.authenticated) {
      router.replace("/login?mode=user");
      return;
    }
    getOrganizations()
      .then(({ organizations, has_more }) => {
        if (organizations.length === 0 && !has_more) router.replace("/onboarding");
        else if (organizations.length === 1 && !has_more) router.replace(`/o/${organizations[0].slug}/explore`);
        else router.replace("/organizations/select");
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
      <p>Opening the store…</p>
    </StatusScreen>
  );
}
