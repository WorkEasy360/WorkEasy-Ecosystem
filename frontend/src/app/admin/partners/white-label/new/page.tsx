import { redirect } from "next/navigation";

/** Old address: adding a partner is the one Add Partner form, with the type preselected (ADR-048). */
export default function Page() {
  redirect("/admin/partners/new?type=white_label");
}
