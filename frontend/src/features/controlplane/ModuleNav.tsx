"use client";

import { useMemo } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { adminModules, locate } from "./adminModules";
import styles from "./ModuleNav.module.css";

/**
 * A module's own navigation, drawn above every page in it: its sections, then the
 * pages of the current section. The sidebar stays at nine entries; this is where
 * everything beneath them lives. A row with a single entry says nothing, so it is
 * not drawn. On a narrow screen each row scrolls sideways by itself.
 */
export function ModuleNav({ permissions }: { permissions: string[] }) {
  const pathname = usePathname() ?? "";
  const here = useMemo(() => locate(pathname, adminModules(permissions)), [pathname, permissions]);
  if (!here) return null;
  const { module, section, page } = here;
  const showSections = module.sections.length > 1;
  const showPages = section.pages.length > 1;
  if (!showSections && !showPages) return null;
  return (
    <div className={styles.wrap}>
      {showSections && (
        <nav aria-label={`${module.label} sections`} className={styles.sections}>
          {module.sections.map((s) => {
            const current = s.label === section.label;
            return (
              <Link
                key={s.label}
                href={s.pages[0].href}
                className={styles.section}
                data-current={current || undefined}
                aria-current={current ? (showPages ? "true" : "page") : undefined}
              >
                {s.label}
              </Link>
            );
          })}
        </nav>
      )}
      {showPages && (
        <nav aria-label={`${section.label} pages`} className={styles.pages}>
          {section.pages.map((p) => (
            <Link
              key={p.href}
              href={p.href}
              className={styles.page}
              data-current={p.href === page.href || undefined}
              aria-current={p.href === page.href ? "page" : undefined}
            >
              {p.label}
            </Link>
          ))}
        </nav>
      )}
    </div>
  );
}
