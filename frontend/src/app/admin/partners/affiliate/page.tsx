import { redirect } from "next/navigation";
import { typePageHref } from "@/features/controlplane/partnerCategories";

/** Old address (ADR-062): Partners → Affiliate is now Partners › Partners with the
 * Affiliate chip chosen (admin-ux phase 1), keeping the filters it understands and a
 * guest session's return. Still the path a guest session may come back to. */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(typePageHref("affiliate", await searchParams));
}
