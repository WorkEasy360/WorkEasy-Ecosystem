import { redirect } from "next/navigation";
import { allPartnersHref } from "@/features/controlplane/oldPartnerRoutes";

/** Old address: White-label is now All Partners filtered on the White-label type (ADR-048). */
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(allPartnersHref(await searchParams, "white_label"));
}
