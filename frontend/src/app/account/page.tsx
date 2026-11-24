import { AccountHome } from "@/features/account/AccountHome";
import { CenterProfile } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";
import { readPortalOrigins } from "@/lib/portals";

export const metadata = { title: accountsTitle("Your account"), robots: { index: false } };
// The customer portal's origin is runtime configuration.
export const dynamic = "force-dynamic";

/** ADR-071: the person's account, on WorkEasy360 Accounts. ADR-072 / Accounts V3: while
 * Accounts hosts identity, the Accounts Control Center opens on My Profile; otherwise
 * exactly as before. */
export default function AccountPage() {
  if (centerOrigins()) return <CenterProfile />;
  // Organizations open on the customer portal (User Admin); single-host mode: this host.
  const appOrigin = readPortalOrigins(process.env)?.app ?? "";
  return <AccountHome appOrigin={appOrigin} />;
}
