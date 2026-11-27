import { notFound } from "next/navigation";
import { CenterProfile } from "@/features/account/AccountsCenter";
import { centerOrigins } from "@/features/account/centerConfig";
import { accountsTitle } from "@/features/signin/AccountsBrand";

export const metadata = { title: accountsTitle("My Profile"), robots: { index: false } };
export const dynamic = "force-dynamic";

/** Accounts V3 / 2026-11-27: My Profile - your photo, name, email, WorkEasy360 ID and
 * the details you can edit. Home (`/account`) carries the overview. */
export default function AccountProfilePage() {
  if (!centerOrigins()) notFound();
  return <CenterProfile />;
}
