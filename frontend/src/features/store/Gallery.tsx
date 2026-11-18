"use client";

import { useState, type KeyboardEvent } from "react";
import type { PublicStoreImage, PublicStoreVideo } from "@/lib/api/public";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import { useSwipe } from "./useSwipe";
import styles from "./Gallery.module.css";

/**
 * A Store product's pictures and its demo video (moved from the old public
 * product page, ADR-065; redesigned 2026-11-16).
 *
 * `Gallery`: a large stage with the banner first, then the screenshots in the
 * order the Super Admin set; thumbnails, Previous / Next, the arrow keys while
 * focus is in the gallery, and a sideways swipe choose what it shows; the
 * picture itself opens the full-screen view (`Lightbox`, owned by the page).
 * Without a picture it is the product's mark on a colour tile - never an
 * invented image.
 *
 * `DemoVideo`: the product's video. Its player loads only when the visitor
 * presses Play, so a page view never contacts YouTube or Vimeo by itself (the
 * CSP's `frame-src` names exactly those two players, at the server's fixed
 * address); an uploaded video plays in the browser's own player.
 */

function PlayMark({ size }: { size: number }) {
  return (
    <span className={styles.playMark} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width={size * 0.42} height={size * 0.42}>
        <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
      </svg>
    </span>
  );
}

/** A left or right chevron, drawn in `currentColor`. */
export function Chevron({ dir, size = 22 }: { dir: "left" | "right"; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d={dir === "left" ? "M15 5l-7 7 7 7" : "M9 5l7 7-7 7"} />
    </svg>
  );
}

function ExpandMark() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
    </svg>
  );
}

/** The player's address: the server's fixed one, asked to start at once (the
 * visitor has just pressed Play) and, on YouTube, to suggest only this channel. */
function playerSrc(video: Extract<PublicStoreVideo, { embed_url: string }>): string {
  return `${video.embed_url}?${video.provider === "youtube" ? "autoplay=1&rel=0&modestbranding=1" : "autoplay=1&dnt=1"}`;
}

/** A picture's shape on the stage: its own, kept between 3:2 and 8:3. */
function stageRatio(image: PublicStoreImage | undefined): number | undefined {
  if (!image || image.width <= 0 || image.height <= 0) return undefined;
  return Math.min(8 / 3, Math.max(3 / 2, image.width / image.height));
}

export function Gallery({
  title,
  icon,
  logo,
  images,
  code,
  onOpen,
}: {
  title: string;
  icon: string;
  logo: string | null;
  images: PublicStoreImage[];
  code: string;
  /** Opens the full-screen view at this picture. */
  onOpen: (index: number) => void;
}) {
  const [shown, setShown] = useState(0);
  const count = images.length;
  const index = count ? Math.min(shown, count - 1) : 0;
  const current = images[index];
  const go = (step: 1 | -1) => setShown((i) => (Math.min(i, count - 1) + step + count) % count);
  const swipe = useSwipe((step) => {
    if (count > 1) go(step);
  });

  if (!current) {
    return (
      <div className={styles.gallery}>
        <div className={`${styles.stage} ${toneClass(code)}`}>
          <div className={styles.stageMark}>
            <ProductMark name={title} icon={icon} src={logo} size={72} />
            <span className={styles.stageName}>{title}</span>
          </div>
        </div>
      </div>
    );
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (count < 2 || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    go(e.key === "ArrowLeft" ? -1 : 1);
  };
  const ratio = stageRatio(current);

  return (
    <div className={styles.gallery} role="group" aria-label={`${title} pictures`} onKeyDown={onKeyDown}>
      <div className={`${styles.stage} ${styles.stageFilled}`} style={ratio ? { aspectRatio: String(ratio) } : undefined} {...swipe.handlers}>
        <button
          type="button"
          className={styles.stageButton}
          aria-label={`View full screen: ${current.caption || title}`}
          onClick={() => {
            if (!swipe.wasSwipe()) onOpen(index);
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
          <img key={current.url} className={styles.stageImage} src={current.url} alt="" width={current.width || undefined} height={current.height || undefined} draggable={false} />
          <span className={styles.expand} aria-hidden="true">
            <ExpandMark />
          </span>
        </button>
        {count > 1 && (
          <>
            <button type="button" className={`${styles.stageNav} ${styles.stagePrev}`} aria-label="Previous picture" onClick={() => go(-1)}>
              <Chevron dir="left" />
            </button>
            <button type="button" className={`${styles.stageNav} ${styles.stageNext}`} aria-label="Next picture" onClick={() => go(1)}>
              <Chevron dir="right" />
            </button>
            <span className={styles.stageCount} aria-hidden="true">
              {index + 1} / {count}
            </span>
          </>
        )}
      </div>
      {current.caption && <p className={styles.stageCaption}>{current.caption}</p>}
      {count > 1 && (
        <>
          <p className="w360-visually-hidden" aria-live="polite">
            Picture {index + 1} of {count}
          </p>
          <ul className={styles.thumbs} aria-label="Pictures">
            {images.map((image, i) => (
              <li key={`${image.url}-${i}`}>
                <button
                  type="button"
                  className={styles.thumb}
                  aria-pressed={i === index}
                  aria-label={`Show picture ${i + 1}${image.caption ? `: ${image.caption}` : ""}`}
                  onClick={() => setShown(i)}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                  <img src={image.url} alt="" draggable={false} />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** Only an address on this site: the CSP's `img-src` is `'self'`, and a poster
 * from anywhere else would be blocked (or would tell another site of the visit). */
function sameOrigin(url: string | null | undefined): string | null {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : null;
}

/** The product's demo video, behind a poster: the video's own poster, else the
 * product's first picture, else a colour tile with the product's name. */
export function DemoVideo({ title, video, fallbackPoster, code }: { title: string; video: PublicStoreVideo; fallbackPoster: string | null; code: string }) {
  const [playing, setPlaying] = useState(false);
  const poster = sameOrigin(video.poster) ?? fallbackPoster;
  const name = video.title || title;
  return (
    <div className={`${styles.stage} ${styles.stageVideo}`}>
      {playing && video.provider === "upload" ? (
        <video className={styles.player} src={video.url} poster={poster ?? undefined} controls autoPlay playsInline preload="auto" aria-label={`${name} - video`}>
          <a href={video.url}>Download the video</a>
        </video>
      ) : playing && video.provider !== "upload" ? (
        <iframe
          className={styles.player}
          src={playerSrc(video)}
          title={`${name} - video`}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
          sandbox="allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox"
        />
      ) : (
        <button type="button" className={`${styles.poster} ${poster ? "" : toneClass(code)}`} onClick={() => setPlaying(true)} aria-label={`Play video: ${name}`}>
          {poster ? (
            <>
              {/* The picture whole, over a blurred copy that fills the 16:9 stage. */}
              {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
              <img className={styles.posterFill} src={poster} alt="" />
              {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
              <img className={styles.posterImage} src={poster} alt="" />
            </>
          ) : (
            <span className={styles.posterName}>{title}</span>
          )}
          <span className={styles.posterPlay}>
            <PlayMark size={76} />
            <span className={styles.posterLabel}>Watch video</span>
          </span>
        </button>
      )}
    </div>
  );
}

const TONES = [styles.toneBlue, styles.toneGreen, styles.toneOrange, styles.tonePurple, styles.toneTeal];

function toneClass(code: string): string {
  // FNV-1a: similar codes (easy_crm, easy_project) still land on different colours.
  let hash = 2166136261;
  for (const ch of code) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0;
  return TONES[hash % TONES.length];
}
