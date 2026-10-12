import { Suspense } from "react";
import { StatusScreen } from "@/components/StatusScreen";
import { SignOutConfirm } from "@/features/signin/SignOutConfirm";

/** A product's "sign out of Workeasy360 too" lands here (ADR-044). */
export default function SignOutPage() {
  return (
    <Suspense fallback={<StatusScreen>Loading…</StatusScreen>}>
      <SignOutConfirm />
    </Suspense>
  );
}
