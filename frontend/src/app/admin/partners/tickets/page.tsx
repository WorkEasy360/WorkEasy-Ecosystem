import { redirect } from "next/navigation";

/** Old address (2026-11-03): Partner tickets are in Support › Tickets now, with Who
 * asked set to Partners (admin-ux phase 1). Staff who hold only the partner grant
 * see the same partner desk there as here before. */
export default function Page() {
  redirect("/admin/tickets?audience=partner");
}
