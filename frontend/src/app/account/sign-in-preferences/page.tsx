import { AccountSignInPreferences } from "@/features/account/AccountHome";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Product sign-in"), robots: { index: false } };

/** ADR-071: how products sign this person in (ADR-044/066), on WorkEasy360 Accounts. */
export default function AccountSignInPreferencesPage() {
  return <AccountSignInPreferences />;
}
