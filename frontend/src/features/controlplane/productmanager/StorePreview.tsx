"use client";

import { useId } from "react";
import { DialogHead, Modal } from "../Modal";
import { MarkdownPreview } from "../widgets";
import { useDataUrl } from "./ImagePicker";
import { looksLikeHttps } from "./model";
import { ProductMark } from "./ProductMark";
import styles from "./ProductManager.module.css";

/** Everything the preview draws - from the wizard's unsaved fields or from a
 * stored product. Image sources are same-origin URLs or local `data:` URLs. */
export interface ListingPreview {
  name: string;
  icon: string;
  storeTitle: string;
  storeSummary: string;
  tagline: string;
  category: string;
  description: string;
  trialDays: number | null;
  logoSrc: string | null;
  bannerSrc: string | null;
  /** A stored screenshot has `src`; one not yet uploaded has its `file`. */
  shots: { key: string; src: string | null; file?: File | null; caption: string }[];
  learnMoreUrl: string;
  documentationUrl: string;
  supportUrl: string;
}

function PreviewShot({ shot, n }: { shot: ListingPreview["shots"][number]; n: number }) {
  const local = useDataUrl(shot.file ?? null);
  const src = local ?? shot.src;
  return (
    <figure role="listitem">
      {src && (
        // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin image
        <img src={src} alt={shot.caption || `Screenshot ${n}`} />
      )}
      {shot.caption && <figcaption>{shot.caption}</figcaption>}
    </figure>
  );
}

/** Roughly how the product will appear in the future Customer Store: its card,
 * then its listing page. Preview only - nothing here can be bought, and
 * publishing grants nobody access. */
export function StorePreview({ listing, onClose }: { listing: ListingPreview; onClose: () => void }) {
  const titleId = useId();
  const title = listing.storeTitle.trim() || listing.name.trim() || "Untitled product";
  const summary = listing.storeSummary.trim() || listing.tagline.trim();
  const links = [
    { label: "Learn more", href: listing.learnMoreUrl },
    { label: "Documentation", href: listing.documentationUrl },
    { label: "Support", href: listing.supportUrl },
    // Unsaved input reaches here: only an https address is ever made a link.
  ].filter((l) => looksLikeHttps(l.href.trim()));

  return (
    <Modal labelledBy={titleId} onClose={onClose} wide>
      <DialogHead titleId={titleId} title="Store listing preview" onClose={onClose} />
      <div className={styles.previewBody}>
        <p className={styles.previewNote}>A preview of the future Customer Store. Nothing here can be bought.</p>

        <section aria-label="Store card">
          <p className={styles.sectionLabel}>Store card</p>
          <div className={styles.storeCard}>
            <div className={styles.storeCardHead}>
              <ProductMark name={title} icon={listing.icon} src={listing.logoSrc} size={56} />
              <div>
                <h3 className={styles.storeCardTitle}>{title}</h3>
                {listing.category && <span className={styles.pill}>{listing.category}</span>}
              </div>
            </div>
            <p className={styles.storeCardText}>{summary || "No short description yet."}</p>
            {listing.trialDays != null && (
              <span className={`${styles.pill} ${styles.trialPill}`}>{listing.trialDays}-day free trial</span>
            )}
          </div>
        </section>

        <section aria-label="Listing page">
          <p className={styles.sectionLabel}>Listing page</p>
          <article className={styles.listing}>
            {listing.bannerSrc ? (
              // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin image
              <img className={styles.banner} src={listing.bannerSrc} alt="" />
            ) : (
              <div className={styles.bannerEmpty} aria-hidden="true" />
            )}
            <div className={styles.listingInner}>
              <div className={styles.storeCardHead}>
                <ProductMark name={title} icon={listing.icon} src={listing.logoSrc} size={72} />
                <div>
                  <h3 className={styles.cardTitle}>{title}</h3>
                  <p className={styles.storeCardText}>{listing.tagline || summary}</p>
                </div>
              </div>
              {listing.description.trim() ? (
                <div className={styles.listingDescription}>
                  <MarkdownPreview text={listing.description} />
                </div>
              ) : (
                <p className={styles.muted}>No full description yet.</p>
              )}
              {listing.shots.length > 0 && (
                <div className={styles.shots} role="list" aria-label="Screenshots">
                  {listing.shots.map((shot, i) => (
                    <PreviewShot key={shot.key} shot={shot} n={i + 1} />
                  ))}
                </div>
              )}
              {links.length > 0 && (
                <p className={styles.links}>
                  {links.map((l) => (
                    <a key={l.label} href={l.href} target="_blank" rel="noopener noreferrer">
                      {l.label}
                    </a>
                  ))}
                </p>
              )}
            </div>
          </article>
        </section>
      </div>
    </Modal>
  );
}
