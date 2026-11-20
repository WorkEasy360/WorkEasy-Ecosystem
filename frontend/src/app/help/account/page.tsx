import { AccountHelpScreen } from "@/features/account/AccountHelp";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("Help & Support") };

/** Accounts V3: account-security help, signed in or signed out (public in proxy.ts). */
export default function AccountHelpPage() {
  return <AccountHelpScreen />;
}
