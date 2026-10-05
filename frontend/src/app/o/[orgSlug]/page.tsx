"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useOrgAccess } from "@/features/console/OrgAccess";
import { intentQuery, takeIntent } from "@/features/site/SiteShell";
import { consoleApi } from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";

/**
 * The organization's front door. Everyone lands on the dashboard (Phase 9,
 * owner decision: its first section is My products) - unless they chose a
 * plan (or a product's own free trial, Phase 11) on the public site before
 * signing in, in which case that intent is acted on now.
 *
 * A trial intent has no checkout: it is started directly, and My products
 * explains the result - `?trial_started=<code>` on success, `?trial_error=`
 * on a refusal (the trial may no longer be offered by the time the person
 * finished signing up; nothing here assumes it still is).
 */
export default function OrganizationIndexPage() {
  const router = useRouter();
  const { slug, access } = useOrgAccess();

  useEffect(() => {
    if (!access) return;
    const intent = takeIntent();
    if (intent?.productTrial) {
      const { product, plan } = intent.productTrial;
      consoleApi
        .startTrial(slug, product, plan, crypto.randomUUID())
        .then(() => router.replace(`/o/${slug}/apps?trial_started=${product}`))
        .catch((err: unknown) => {
          const reason =
            err instanceof ApiError && typeof err.body === "object" && err.body !== null
              ? (err.body as { reason?: string }).reason
              : undefined;
          router.replace(`/o/${slug}/apps?trial_error=${reason ?? "unknown"}&product=${product}`);
        });
      return;
    }
    router.replace(intent ? `/o/${slug}/checkout?${intentQuery(intent)}` : `/o/${slug}/dashboard`);
  }, [access, router, slug]);

  return <p aria-live="polite">Opening your organization…</p>;
}
