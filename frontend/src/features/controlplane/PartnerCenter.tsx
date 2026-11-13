"use client";

/**
 * The Partners area's pages (ADR-048). Until 2026-11-07 they sat under a
 * "Partner Control Center" banner and a strip of pill links; that strip
 * repeated the sidebar's Partners section, wrapped to two rows and pushed every
 * title down, so it is gone (2026-11-07 design system): the sidebar's Partners
 * section is the one navigation, and each page's own header starts at the same
 * place as every other console page. The sections and their grants stay in
 * `partnerCenterNav.ts`, which the sidebar reads.
 */
export function PartnerCenterLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
