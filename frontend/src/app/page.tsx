"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getOrganizations } from "@/lib/api/auth";
import { useSession } from "@/lib/auth/useSession";
import { StatusScreen } from "@/components/StatusScreen";

/**
 * Task 5: a user with exactly one active organization is taken straight in;
 * a user with several sees the chooser; a user signed in with no membership
 * anywhere gets a safe, explicit empty state rather than a redirect loop.
 */
export default function HomePage() {
  const router = useRouter();
  const query = useSession();

  useEffect(() => {
    if (query.status !== "ready") return;
    if (!query.session.authenticated) {
      router.replace("/login");
      return;
    }
    getOrganizations()
      .then(({ organizations }) => {
        if (organizations.length === 1) {
          router.replace(`/o/${organizations[0].slug}/apps`);
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
      <p>Loading Workeasy360…</p>
    </StatusScreen>
  );
}
