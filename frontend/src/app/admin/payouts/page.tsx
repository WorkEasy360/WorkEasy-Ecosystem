import { redirect } from "next/navigation";
import { carryQuery } from "@/features/controlplane/carryQuery";

/** Payouts now live in the Partner Control Center. The old address keeps
 * working and carries its filters, e.g. `?status=open`. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(`/admin/partners/payouts${carryQuery(await searchParams)}`);
}
