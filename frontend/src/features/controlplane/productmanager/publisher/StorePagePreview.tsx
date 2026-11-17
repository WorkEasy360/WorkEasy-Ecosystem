"use client";

import { useId } from "react";
import type { PublicStoreProductPage } from "@/lib/api/public";
import { Modal, useClosing } from "@/features/controlplane/Modal";
import { StoreProductPage } from "@/features/store/ProductDetails";
import shell from "@/features/store/Store.module.css";
import styles from "./Publisher.module.css";

/**
 * The Store product page exactly as the Digital Store draws it (the Store's own
 * component, with the server's own page data for this product - a draft too),
 * for staff only: its pictures come from the staff media endpoint, nothing is
 * made public, and its purchase buttons do nothing.
 */
export function StorePagePreview({ data, onClose }: { data: PublicStoreProductPage & { preview?: boolean }; onClose: () => void }) {
  const titleId = useId();
  const { closing, close } = useClosing(onClose);
  return (
    <Modal labelledBy={titleId} onClose={close} size="xl" closing={closing}>
      <header className={styles.head}>
        <div>
          <h2 id={titleId} className={styles.title}>
            Store preview
          </h2>
          <p className={styles.subtitle}>How the product page will look in the Digital Store. Visible to staff only; nothing can be bought here.</p>
        </div>
        <button type="button" className={styles.close} aria-label="Close preview" onClick={close}>
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <div className={styles.previewBody}>
        <div className={shell.shell}>
          <div className={shell.container}>
            <StoreProductPage data={data} preview />
          </div>
        </div>
      </div>
      <footer className={styles.foot}>
        <div className={styles.footStatus} />
        <div className={styles.footActions}>
          <button type="button" className="w360-btn w360-btn-primary" onClick={close}>
            Back to editing
          </button>
        </div>
      </footer>
    </Modal>
  );
}
