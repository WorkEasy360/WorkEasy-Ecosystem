import { notFound, redirect } from "next/navigation";
import { MOVED_SECTIONS, centerOrigins } from "@/features/account/centerConfig";

export const dynamic = "force-dynamic";

/** ADR-072's page, moved by Accounts V3 into one of the four sections. */
export default function MovedAccountPage() {
  if (!centerOrigins()) notFound();
  redirect(MOVED_SECTIONS["/account/history"]);
}
