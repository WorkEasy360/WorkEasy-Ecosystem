import { redirect } from "next/navigation";

/** Old address: a partner's commissions are on its own page; this opens All Partners (ADR-048). */
export default function Page() {
  redirect("/admin/partners/all");
}
