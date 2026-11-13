import { SwitchAccountFinish } from "@/features/signin/SwitchAccountFinish";

export const metadata = { title: "Switching account - WorkEasy360", robots: { index: false } };

/** Switch Account's second step (ADR-066), on the central sign-in host. */
export default function SwitchAccountPage() {
  return <SwitchAccountFinish />;
}
