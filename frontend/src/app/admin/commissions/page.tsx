import { redirect } from "next/navigation";
import { carryQuery } from "@/features/controlplane/carryQuery";

/** The commission ledger now lives in the Partner Control Center. The old
 * address keeps working (bookmarks, links in earlier emails) and carries its
 * filters, e.g. `?unpaid=1`. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(`/admin/partners/commissions${carryQuery(await searchParams)}`);
}
