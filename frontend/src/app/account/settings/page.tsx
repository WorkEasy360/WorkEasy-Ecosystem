import { notFound } from "next/navigation";
import { CenterSettings } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Account Settings"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: profile, product sign-in preferences and Switch Account. */
export default function AccountSettingsPage() {
  if (!centerOrigins()) notFound();
  return <CenterSettings />;
}
