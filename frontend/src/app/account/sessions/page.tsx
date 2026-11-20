import { notFound } from "next/navigation";
import { CenterDevices } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Devices & Sessions"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** Accounts V3: active sessions, the products signed in to, and login history. */
export default function AccountSessionsPage() {
  if (!centerOrigins()) notFound();
  return <CenterDevices />;
}
