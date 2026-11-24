import { notFound } from "next/navigation";
import { ActiveSessions } from "@/features/account/CenterSessions";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Active Sessions"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: where this person is signed in, one row per sign-in. */
export default function AccountSessionsPage() {
  if (!centerOrigins()) notFound();
  return <ActiveSessions />;
}
