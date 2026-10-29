"use client";

import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { initials } from "@/components/sidebar/SidebarShell";
import styles from "./ProductManager.module.css";

const SIZES = { 32: styles.mark32, 40: styles.mark40, 56: styles.mark56, 72: styles.mark72 } as const;

/** A product's mark: its uploaded logo, else its built-in icon, else its
 * initials. Decorative - the product's name is always beside it - so the image
 * has empty alt text. `src` is a same-origin URL or a local object URL. */
export function ProductMark({
  name,
  icon,
  src,
  size = 40,
}: {
  name: string;
  icon?: string;
  src?: string | null;
  size?: keyof typeof SIZES;
}) {
  return (
    <span aria-hidden="true" className={`${styles.mark} ${SIZES[size]}`}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- a same-origin or local preview image, not a static asset
        <img src={src} alt="" />
      ) : productIconKnown(icon) ? (
        <ProductIcon icon={icon} size={Math.round(size * 0.55)} />
      ) : (
        initials(name || "?")
      )}
    </span>
  );
}
