import { Suspense } from "react";
import { StatusScreen } from "@/components/StatusScreen";
import { accountsTitle } from "@/features/signin/AccountsBrand";
import { SignOutConfirm } from "@/features/signin/SignOutConfirm";

export const metadata = { title: accountsTitle("Sign out"), robots: { index: false } };

/** A product's "sign out of Workeasy360 too" lands here (ADR-044). */
export default function SignOutPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <SignOutConfirm />
    </Suspense>
  );
}
