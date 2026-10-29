"use client";

import { useId } from "react";
import { PRODUCT_ICON_KEYS, ProductIcon } from "@/components/ProductIcon";
import styles from "./ProductManager.module.css";

/** The optional product icon: one of the built-in drawings (never an upload or
 * a URL), as native radio buttons with a live preview of each. */
export function IconPicker({ value, onChange, disabled = false }: { value: string; onChange: (key: string) => void; disabled?: boolean }) {
  const name = useId();
  return (
    <fieldset className={styles.picker} aria-describedby={`${name}-hint`} disabled={disabled}>
      <legend className="w360-label">Product icon (optional)</legend>
      <p id={`${name}-hint`} className="w360-hint">
        Shown where a logo is too small to read. Built-in icons only.
      </p>
      <div className={styles.iconGrid}>
        <label className={styles.iconChoice} title="No icon">
          <input type="radio" name={name} checked={value === ""} onChange={() => onChange("")} aria-label="No icon" />
          <span aria-hidden="true">—</span>
        </label>
        {PRODUCT_ICON_KEYS.map((key) => (
          <label key={key} className={styles.iconChoice} title={key}>
            <input type="radio" name={name} checked={value === key} onChange={() => onChange(key)} aria-label={`Icon: ${key}`} />
            <ProductIcon icon={key} size={22} />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
