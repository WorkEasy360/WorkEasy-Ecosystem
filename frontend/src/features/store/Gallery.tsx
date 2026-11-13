"use client";

import { useState } from "react";
import type { PublicStoreImage, PublicStoreVideo } from "@/lib/api/public";
import { ProductMark } from "@/features/controlplane/productmanager/ProductMark";
import styles from "./Gallery.module.css";

/**
 * A Store product's video and pictures (moved from the old public product page,
 * ADR-065): a large stage and thumbnails that choose what it shows. The video
 * comes first; its player loads only when the visitor presses Play, so a page
 * view never contacts YouTube or Vimeo by itself (the CSP's `frame-src` names
 * exactly those two players).
 */

type Slide = { kind: "video"; video: PublicStoreVideo } | { kind: "image"; image: PublicStoreImage };

function PlayMark({ size }: { size: number }) {
  return (
    <span className={styles.playMark} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox="0 0 24 24" width={size * 0.42} height={size * 0.42}>
        <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
      </svg>
    </span>
  );
}

/** The player's address: the server's fixed one, asked to start at once (the
 * visitor has just pressed Play) and, on YouTube, to suggest only this channel. */
function playerSrc(video: Extract<PublicStoreVideo, { embed_url: string }>): string {
  return `${video.embed_url}?${video.provider === "youtube" ? "autoplay=1&rel=0&modestbranding=1" : "autoplay=1&dnt=1"}`;
}

/** The product's video and pictures: a large stage, and thumbnails that choose
 * what it shows. The video comes first; its player loads only when the visitor
 * presses Play, so a page view never contacts YouTube or Vimeo by itself. */
export function Gallery({
  title,
  icon,
  logo,
  images,
  video,
  code,
}: {
  title: string;
  icon: string;
  logo: string | null;
  images: PublicStoreImage[];
  video: PublicStoreVideo | null;
  code: string;
}) {
  const slides: Slide[] = [...(video ? [{ kind: "video" as const, video }] : []), ...images.map((image) => ({ kind: "image" as const, image }))];
  const [shown, setShown] = useState(0);
  const [playing, setPlaying] = useState(false);
  const current = slides[Math.min(shown, slides.length - 1)];
  const poster = images[0];
  // A picture fills the stage edge to edge, at its own shape kept between 3:2 and 8:3; a video is 16:9.
  const ratio =
    current?.kind === "video"
      ? 16 / 9
      : current && current.image.width > 0 && current.image.height > 0
        ? Math.min(8 / 3, Math.max(3 / 2, current.image.width / current.image.height))
        : undefined;
  const choose = (i: number) => {
    setShown(i);
    setPlaying(false);
  };
  const caption = current?.kind === "image" ? current.image.caption : "";

  return (
    <div className={styles.gallery}>
      <div
        className={`${styles.stage} ${current ? styles.stageFilled : toneClass(code)} ${current?.kind === "video" ? styles.stageVideo : ""}`}
        style={ratio ? { aspectRatio: String(ratio) } : undefined}
      >
        {current?.kind === "video" ? (
          playing && current.video.provider === "upload" ? (
            <video className={styles.player} src={current.video.url} controls autoPlay playsInline preload="auto" aria-label={`${title} - video`}>
              <a href={current.video.url}>Download the video</a>
            </video>
          ) : playing && current.video.provider !== "upload" ? (
            <iframe
              className={styles.player}
              src={playerSrc(current.video)}
              title={`${title} - video`}
              allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              sandbox="allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox"
            />
          ) : (
            <button type="button" className={`${styles.poster} ${poster ? "" : toneClass(code)}`} onClick={() => setPlaying(true)} aria-label={`Play video: ${title}`}>
              {poster ? (
                <>
                  {/* The picture whole, over a blurred copy that fills the 16:9 stage. */}
                  {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                  <img className={styles.posterFill} src={poster.url} alt="" />
                  {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                  <img className={styles.posterImage} src={poster.url} alt="" />
                </>
              ) : (
                <span className={styles.posterName}>{title}</span>
              )}
              <span className={styles.posterPlay}>
                <PlayMark size={76} />
                <span className={styles.posterLabel}>Watch video</span>
              </span>
            </button>
          )
        ) : current ? (
          // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
          <img className={styles.stageImage} src={current.image.url} alt={current.image.caption || title} width={current.image.width} height={current.image.height} />
        ) : (
          <div className={styles.stageMark}>
            <ProductMark name={title} icon={icon} src={logo} size={72} />
            <span className={styles.stageName}>{title}</span>
          </div>
        )}
      </div>
      {caption && <p className={styles.stageCaption}>{caption}</p>}
      {slides.length > 1 && (
        <ul className={styles.thumbs} aria-label="Video and pictures">
          {slides.map((slide, i) => (
            <li key={slide.kind === "video" ? "video" : slide.image.url}>
              <button
                type="button"
                className={styles.thumb}
                aria-pressed={i === shown}
                aria-label={
                  slide.kind === "video"
                    ? "Show the video"
                    : `Show picture ${video ? i : i + 1}${slide.image.caption ? `: ${slide.image.caption}` : ""}`
                }
                onClick={() => choose(i)}
              >
                {slide.kind === "video" ? (
                  <span className={`${styles.thumbVideo} ${poster ? "" : toneClass(code)}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image */}
                    {poster && <img src={poster.url} alt="" />}
                    <PlayMark size={34} />
                  </span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element -- a same-origin listing image
                  <img src={slide.image.url} alt="" />
                )}
              </button>
            </li>
          ))}
        </ul>
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
