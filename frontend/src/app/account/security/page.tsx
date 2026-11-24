import { notFound } from "next/navigation";
import { CenterSecurity } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Security"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-072: password, recovery, sessions, sign out everywhere, security activity. */
export default function AccountSecurityPage() {
  if (!centerOrigins()) notFound();
  return <CenterSecurity />;
}
