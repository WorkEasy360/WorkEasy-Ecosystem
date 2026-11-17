"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import type { PublicStoreImage } from "@/lib/api/public";
import { Chevron } from "./Gallery";
import { useSwipe } from "./useSwipe";
import styles from "./Lightbox.module.css";

/**
 * A product's pictures full screen (2026-11-16): one picture at a time, its
 * caption and "3 / 8", Previous / Next (and the arrow keys, and a sideways
 * swipe), and Close (and Escape, and a click beside the picture).
 *
 * A modal dialog on the page's body, the same way as the consoles' `Modal`:
 * focus moves to Close and stays inside while it is open, the page behind does
 * not scroll, and focus returns where it was when it closes. Its colours are
 * the platform's own tokens - it is drawn outside the Store's frame.
 */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const noSubscription = () => () => {};

export function Lightbox({ title, images, start, onClose }: { title: string; images: PublicStoreImage[]; start: number; onClose: () => void }) {
  const count = images.length;
  const [index, setIndex] = useState(() => Math.max(0, Math.min(start, count - 1)));
  const box = useRef<HTMLDivElement | null>(null);
  const close = useRef(onClose);
  const total = useRef(count);
  useEffect(() => {
    close.current = onClose;
    total.current = count;
  }, [onClose, count]);
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false);
  const go = (step: 1 | -1) => setIndex((i) => (i + step + total.current) % total.current);
  const swipe = useSwipe((step) => {
    if (count > 1) go(step);
  });

  useEffect(() => {
    if (!inBrowser) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    box.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close.current();
        return;
      }
      if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && total.current > 1) {
        e.preventDefault();
        const step = e.key === "ArrowLeft" ? -1 : 1;
        setIndex((i) => (i + step + total.current) % total.current);
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (!box.current.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      before?.focus();
    };
  }, [inBrowser]);

  if (!inBrowser || count === 0) return null;
  const image = images[Math.min(index, count - 1)];
  const label = image.caption || `${title} - picture ${index + 1}`;

  return createPortal(
    <div className={styles.backdrop}>
      <div ref={box} role="dialog" aria-modal="true" aria-label={`${title} - pictures`} className={styles.dialog}>
        <div className={styles.bar}>
          <p className={styles.counter} aria-live="polite">
            <span aria-hidden="true">
              {index + 1} / {count}
            </span>
            <span className="w360-visually-hidden">
              Picture {index + 1} of {count}
            </span>
          </p>
          <button type="button" className={styles.close} aria-label="Close" data-autofocus onClick={() => close.current()}>
            <svg viewBox="0 0 24 24" width={22} height={22} aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        {/* A click beside the picture closes the view; the picture and the buttons do not. */}
        <div
          className={styles.stage}
          onClick={(e) => {
            if (e.target === e.currentTarget && !swipe.wasSwipe()) close.current();
          }}
          {...swipe.handlers}
        >
          <figure className={styles.figure}>
            {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
            <img key={image.url} className={styles.image} src={image.url} alt={label} width={image.width || undefined} height={image.height || undefined} draggable={false} />
            {image.caption && <figcaption className={styles.caption}>{image.caption}</figcaption>}
          </figure>
          {count > 1 && (
            <>
              <button type="button" className={`${styles.nav} ${styles.prev}`} aria-label="Previous picture" onClick={() => go(-1)}>
                <Chevron dir="left" size={26} />
              </button>
              <button type="button" className={`${styles.nav} ${styles.next}`} aria-label="Next picture" onClick={() => go(1)}>
                <Chevron dir="right" size={26} />
              </button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
