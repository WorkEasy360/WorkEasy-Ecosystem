import { notFound } from "next/navigation";
import { CenterSettings } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Settings"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** Accounts V3: notifications, security activity, links to User Admin, account closure. */
export default function AccountSettingsPage() {
  const center = centerOrigins();
  if (!center) notFound();
  return <CenterSettings appOrigin={center.appOrigin} />;
}
