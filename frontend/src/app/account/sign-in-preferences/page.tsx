import { redirect } from "next/navigation";
import { AccountSignInPreferences } from "@/features/account/AccountHome";
import { MOVED_SECTIONS, centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Product sign-in"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-071: how products sign this person in (ADR-044/066), on WorkEasy360 Accounts.
 * Accounts V3: while Accounts hosts identity, it is a section of Login & Security. */
export default function AccountSignInPreferencesPage() {
  if (centerOrigins()) redirect(MOVED_SECTIONS["/account/sign-in-preferences"]);
  return <AccountSignInPreferences />;
}
