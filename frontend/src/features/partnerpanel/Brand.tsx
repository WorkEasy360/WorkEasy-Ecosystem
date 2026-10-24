"use client";

import { PartnerBranding, PartnerDomains } from "@/features/controlplane/PartnerScreens";
import { SubNav, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/** The tabs of Brand Settings. Branding is the look; the URL Manager is where
 * that look is served from (verified custom domains and the branded sign-in
 * address). The white-label setup is the guided way to do both at once. */
function BrandNav() {
  const { base, can } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/brand-settings`, label: "Branding", show: can("branding.view") },
        { href: `${base}/brand-settings/url-manager`, label: "URL Manager", show: can("domain.view") },
        {
          href: `${base}/brand-settings/white-label-setup`,
          label: "White-label setup",
          show: can("branding.manage") && can("domain.manage"),
        },
      ]}
    />
  );
}

/** Branding: reuses the partner's one `PartnerBrand`. */
export function BrandSettings() {
  return (
    <div className={styles.page}>
      <BrandNav />
      <PartnerBranding />
    </div>
  );
}

/** URL Manager: reuses `PartnerDomain`, with its DNS ownership proof untouched. */
export function UrlManager() {
  return (
    <div className={styles.page}>
      <BrandNav />
      <PartnerDomains />
    </div>
  );
}

export { BrandNav };
