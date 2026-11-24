import { notFound } from "next/navigation";
import { CenterLoginSecurity } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Login & Security"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** Accounts V3: sign-in email, password, two-step verification, product sign-in preferences. */
export default function AccountSecurityPage() {
  if (!centerOrigins()) notFound();
  return <CenterLoginSecurity />;
}
