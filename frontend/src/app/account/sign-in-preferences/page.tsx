import { AccountSignInPreferences } from "@/features/account/AccountHome";
import { CenterSignInPreferences } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Product sign-in"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** ADR-071: how products sign this person in (ADR-044/066), on WorkEasy360 Accounts -
 * inside the Accounts Control Center (ADR-072) while Accounts hosts identity. */
export default function AccountSignInPreferencesPage() {
  return centerOrigins() ? <CenterSignInPreferences /> : <AccountSignInPreferences />;
}
