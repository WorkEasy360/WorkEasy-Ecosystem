"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NavIcon } from "@/components/sidebar/icons";
import { useAdmin } from "./ControlShell";
import { centerItems, isCurrent } from "./partnerCenterNav";
import local from "./PartnerConsole.module.css";

/**
 * The Partner Control Center: one place for the whole partner programme.
 *
 * The Super Admin sidebar carries a single "Partners" entry; everything about
 * partners lives under `/admin/partners` behind this local navigation instead
 * of seven sidebar links. Dashboard, All Partners, Resellers, White-label and
 * Affiliates are **views of the same partner accounts** - the last three are
 * filters on the capabilities a partner holds, decided by the server - and
 * Customers, Products & Pricing, Commissions and Payouts are the programme's
 * existing screens, reached from here.
 *
 * An entry appears only when the staff member holds the grant its page needs
 * (`partnerCenterNav.ts`). That is navigation convenience, never security:
 * each page's data comes from an endpoint that re-checks the permission on
 * every request.
 */

export function PartnerCenterNav() {
  const { permissions } = useAdmin();
  const pathname = usePathname() ?? "";
  const current = useRef<HTMLAnchorElement | null>(null);

  // On a narrow screen the strip scrolls sideways: keep the current section in
  // view so the page's place is visible without hunting for it.
  useEffect(() => {
    current.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [pathname]);

  const items = centerItems(permissions);
  if (items.length === 0) return null;
  return (
    <nav aria-label="Partner Control Center" className={local.centerNav}>
      <ul>
        {items.map((item) => {
          const active = isCurrent(pathname, item);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                ref={active ? current : undefined}
                aria-current={active ? "page" : undefined}
                className={local.centerLink}
              >
                <NavIcon name={item.icon} />
                <span>{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** The layout around every Partner Control Center page. Each page keeps its own
 * `h1`; this header is a landmark-free banner text, not a second heading. */
export function PartnerCenterLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={local.center}>
      <div className={local.centerHeader}>
        <p className={local.centerTitle}>Partner Control Center</p>
        <p className={local.centerLead}>Manage your reseller, white-label and affiliate ecosystem.</p>
      </div>
      <PartnerCenterNav />
      <div className={local.centerBody}>{children}</div>
    </div>
  );
}
