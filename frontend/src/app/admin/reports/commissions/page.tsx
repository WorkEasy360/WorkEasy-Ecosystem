import { redirect } from "next/navigation";

/** Old address: Reports is one page now, this report chosen on it (admin-ux phase 1). */
export default function Page() {
  redirect("/admin/reports?report=commissions");
}
