import { redirect } from "next/navigation";
import { allPartnersHref } from "@/features/controlplane/oldPartnerRoutes";

/** Old address: Resellers is now All Partners filtered on the Reseller type (ADR-048). */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(allPartnersHref(await searchParams, "reseller"));
}
