import { Suspense } from "react";
import { redirect } from "next/navigation";
import { carryQuery } from "@/features/controlplane/carryQuery";
import { ResellerList } from "@/features/controlplane/PartnerConsole";

/** Partners → Resellers. `?scope=all` was the old address of the full list,
 * which is now All Partners; it is sent there with its other filters.
 * Suspense: the list reads its filters from the query. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { scope, ...rest } = await searchParams;
  if (scope === "all") redirect(`/admin/partners/all${carryQuery(rest)}`);
  return (
    <Suspense fallback={<p aria-live="polite">Loading resellers…</p>}>
      <ResellerList />
    </Suspense>
  );
}
