import { redirect } from "next/navigation";
import { allPartnersHref } from "@/features/controlplane/oldPartnerRoutes";

/** Old address: a partner's products and prices are on its own page; this opens All Partners (ADR-048). */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(allPartnersHref(await searchParams));
}
