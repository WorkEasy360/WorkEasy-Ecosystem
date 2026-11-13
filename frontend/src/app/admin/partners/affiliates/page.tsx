import { redirect } from "next/navigation";
import { categoryHref } from "@/features/controlplane/partnerCategories";

/** Old address: Affiliates is now Partners → Affiliate (ADR-062), keeping the filters it understands. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(categoryHref("affiliate", await searchParams));
}
