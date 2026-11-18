import { accountsTitle } from "@/features/signin/AccountsBrand";
import { SwitchAccountFinish } from "@/features/signin/SwitchAccountFinish";

export const metadata = { title: accountsTitle("Switching account"), robots: { index: false } };

/** Switch Account's second step (ADR-066), on the central sign-in host. */
export default function SwitchAccountPage() {
  return <SwitchAccountFinish />;
}
