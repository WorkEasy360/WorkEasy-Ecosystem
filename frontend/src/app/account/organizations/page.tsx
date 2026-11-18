import { notFound } from "next/navigation";
import { CenterOrganizations } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Organizations"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: memberships, roles and product access per organization. */
export default function AccountOrganizationsPage() {
  const center = centerOrigins();
  if (!center) notFound();
  return <CenterOrganizations appOrigin={center.appOrigin} />;
}
