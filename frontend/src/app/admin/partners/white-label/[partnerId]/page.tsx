import { redirect } from "next/navigation";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Old address: a partner's white-label setup is part of its Complete Your Profile
 * (the brand step). Only a well-formed id is put in the new address. */
export default async function Page({ params }: { params: Promise<{ partnerId: string }> }) {
  const { partnerId } = await params;
  redirect(UUID.test(partnerId) ? `/admin/partners/${partnerId}/profile?step=brand` : "/admin/partners/all");
}
