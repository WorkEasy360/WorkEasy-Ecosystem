import { notFound } from "next/navigation";
import { ConnectedLogins } from "@/features/account/ConnectedLogins";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Connected Logins"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: the products this person can use or has signed in to with WorkEasy360. */
export default function AccountConnectedLoginsPage() {
  if (!centerOrigins()) notFound();
  return <ConnectedLogins />;
}
