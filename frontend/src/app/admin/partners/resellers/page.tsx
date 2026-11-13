import { redirect } from "next/navigation";
import { categoryHref } from "@/features/controlplane/partnerCategories";

/** Old address: Resellers is now Partners → Reseller (ADR-062), keeping the filters it understands. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(categoryHref("reseller", await searchParams));
}
