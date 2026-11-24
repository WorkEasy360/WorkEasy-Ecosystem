import { notFound } from "next/navigation";
import { LoginHistory } from "@/features/account/CenterSessions";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Login History"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: sign-ins, kept and refused, to WorkEasy360 and to products. */
export default function AccountHistoryPage() {
  if (!centerOrigins()) notFound();
  return <LoginHistory />;
}
