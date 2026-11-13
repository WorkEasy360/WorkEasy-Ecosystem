import { redirect } from "next/navigation";

/** Reports has no page of its own: it opens on the first report. */
export default function Page() {
  redirect("/admin/reports/sales");
}
