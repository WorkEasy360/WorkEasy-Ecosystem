"use client";

import { useId, useRef, useState } from "react";
import { formatBytes } from "./model";
import styles from "./ProductManager.module.css";

/** The product page's video, uploaded from the computer (2026-11-04). The
 * server checks the type and size again, on the bytes. */
export const VIDEO_TYPES = ["video/mp4", "video/webm"] as const;
export const MAX_VIDEO_BYTES = 90 * 1024 * 1024;

export function checkVideo(file: File): string | null {
  if (!(VIDEO_TYPES as readonly string[]).includes(file.type)) return "Choose an MP4 or WebM video.";
  if (file.size <= 0) return "This file is empty.";
  if (file.size > MAX_VIDEO_BYTES) return `This video is ${formatBytes(file.size)}; the limit is 90 MB.`;
  return null;
}

/** Drop or browse one video, see the stored one, replace or remove it, with
 * upload progress. `currentUrl` is the stored video's same-origin address. */
export function VideoPicker({
  currentUrl,
  currentSize,
  progress,
  error,
  busy = false,
  onSelect,
  onRemove,
}: {
  currentUrl: string | null;
  currentSize?: number | null;
  progress: number | null;
  error?: string | null;
  busy?: boolean;
  onSelect: (file: File) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [chosen, setChosen] = useState<File | null>(null);
  const message = problem ?? error ?? null;
  const uploading = progress != null;

  function take(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    const why = checkVideo(file);
    setProblem(why ? `${file.name}: ${why}` : null);
    if (why) return;
    setChosen(file);
    onSelect(file);
  }

  return (
    <div className={styles.picker} role="group" aria-labelledby={`${id}-label`}>
      <span id={`${id}-label`} className="w360-label">
        Product video (upload)
      </span>
      <p id={`${id}-hint`} className="w360-hint">
        MP4 or WebM from your computer, up to 90 MB. It plays first in the product page&apos;s gallery, instead of any YouTube or Vimeo link.
        For the widest browser support, export MP4 (H.264).
      </p>
      {(currentUrl || uploading) && (
        <div className={styles.previewRow}>
          {currentUrl && !uploading && (
            <video className={styles.videoPreview} src={currentUrl} controls preload="metadata" aria-label="Current product video" />
          )}
          <div className={styles.previewMeta}>
            {uploading && chosen ? (
              <>
                <strong>{chosen.name}</strong>
                <br />
                {formatBytes(chosen.size)} - uploading {progress}%
                <progress className={styles.progress} max={100} value={progress} aria-label="Video upload">
                  {progress}%
                </progress>
              </>
            ) : (
              <span>Current video{currentSize ? ` (${formatBytes(currentSize)})` : ""}</span>
            )}
          </div>
          {currentUrl && !uploading && (
            <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={onRemove}>
              Remove video
            </button>
          )}
        </div>
      )}
      {!uploading && (
        <div
          className={`${styles.drop} ${over ? styles.dropActive : ""} ${message ? styles.dropError : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            if (!busy) take(e.dataTransfer.files);
          }}
        >
          <p className={styles.dropText}>
            <strong>Drag a video here</strong> or
          </p>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => input.current?.click()}>
            {currentUrl ? "Replace video" : "Upload video from computer"}
          </button>
          <input
            ref={input}
            className={styles.hiddenInput}
            type="file"
            accept={VIDEO_TYPES.join(",")}
            aria-label={currentUrl ? "Replace product video" : "Product video file"}
            aria-describedby={message ? `${id}-hint ${id}-error` : `${id}-hint`}
            tabIndex={-1}
            disabled={busy}
            onChange={(e) => {
              take(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      )}
      {message && (
        <p id={`${id}-error`} className="w360-error-text" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
