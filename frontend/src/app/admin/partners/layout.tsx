import { PartnerCenterLayout } from "@/features/controlplane/PartnerCenter";

/** Every page under /admin/partners is part of the Partner Control Center:
 * one header and one local navigation around it (inside the Super Admin shell). */
export default function PartnersLayout({ children }: { children: React.ReactNode }) {
  return <PartnerCenterLayout>{children}</PartnerCenterLayout>;
}
