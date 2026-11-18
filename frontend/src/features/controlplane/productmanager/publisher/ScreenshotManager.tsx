"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ACCEPTED_TYPES, MAX_SCREENSHOTS, MEDIA_HINT, checkImage, readDataUrl } from "../model";
import type { ShotDraft } from "./model";
import styles from "./Publisher.module.css";

let shotSeq = 0;
const shotKey = () => `shot-${Date.now().toString(36)}-${(shotSeq += 1)}`;

/**
 * The product's screenshots on the Publisher page: add several at once (drop
 * or browse), each checked before it is staged; thumbnails in the order the
 * Store shows them; drag to reorder (or Move earlier/later); "Make cover" puts
 * one first - the Store's primary screenshot; a click enlarges it; a caption
 * each. Chosen files are uploaded when the page is saved, with progress.
 */
export function ScreenshotManager({
  shots,
  busy,
  error,
  onChange,
  onRemoveStored,
}: {
  shots: ShotDraft[];
  busy: boolean;
  error?: string;
  onChange: (shots: ShotDraft[]) => void;
  /** A stored screenshot taken off the page (deleted on save). */
  onRemoveStored: (mediaId: string) => void;
}) {
  const hintId = useId();
  const input = useRef<HTMLInputElement | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [dragging, setDragging] = useState<number | null>(null);
  const [enlarged, setEnlarged] = useState<number | null>(null);
  const room = MAX_SCREENSHOTS - shots.length;

  async function addFiles(list: FileList | File[]) {
    setProblem(null);
    const files = [...list];
    if (files.length === 0) return;
    if (files.length > room) {
      setProblem(room > 0 ? `Only ${room} more screenshot${room === 1 ? "" : "s"} can be added (at most ${MAX_SCREENSHOTS}).` : `The maximum of ${MAX_SCREENSHOTS} screenshots is reached.`);
      return;
    }
    const added: ShotDraft[] = [];
    for (const file of files) {
      const refused = await checkImage(file, "screenshot");
      if (refused) {
        setProblem(`${file.name}: ${refused}`);
        return;
      }
      added.push({ key: shotKey(), mediaId: null, file, src: await readDataUrl(file), caption: "", savedCaption: "", progress: null });
    }
    onChange([...shots, ...added]);
  }

  const move = (from: number, to: number) => {
    if (to < 0 || to >= shots.length || from === to) return;
    const next = [...shots];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };
  const remove = (index: number) => {
    const shot = shots[index];
    if (shot.mediaId) onRemoveStored(shot.mediaId);
    onChange(shots.filter((_, i) => i !== index));
  };
  const message = problem ?? error ?? null;

  return (
    <div className={styles.shots} role="group" aria-labelledby={`${hintId}-label`}>
      <span id={`${hintId}-label`} className="w360-label">
        Product screenshots
      </span>
      <p id={hintId} className="w360-hint">
        {MEDIA_HINT.screenshot} Up to {MAX_SCREENSHOTS}. The first is the cover the Store shows largest; drag to change the order.
      </p>
      {shots.length > 0 && (
        <ol className={styles.shotGrid} aria-label="Screenshots, in Store order">
          {shots.map((shot, i) => (
            <li
              key={shot.key}
              className={`${styles.shot} ${dragging === i ? styles.shotDragging : ""}`}
              draggable={!busy}
              onDragStart={(e) => {
                setDragging(i);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(e) => {
                if (dragging !== null) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging !== null) move(dragging, i);
                setDragging(null);
              }}
              onDragEnd={() => setDragging(null)}
            >
              <button type="button" className={styles.shotImage} aria-label={`Enlarge screenshot ${i + 1}`} onClick={() => setEnlarged(i)}>
                {shot.src && (
                  // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin staff image
                  <img src={shot.src} alt="" />
                )}
                {i === 0 && <span className={styles.coverTag}>Cover</span>}
              </button>
              {shot.progress !== null && (
                <progress className={styles.progress} max={100} value={shot.progress} aria-label={`Screenshot ${i + 1} upload`}>
                  {shot.progress}%
                </progress>
              )}
              <label className="w360-visually-hidden" htmlFor={`${shot.key}-caption`}>
                Caption for screenshot {i + 1}
              </label>
              <input
                id={`${shot.key}-caption`}
                className={`w360-input ${styles.shotCaption}`}
                value={shot.caption}
                maxLength={140}
                placeholder="Caption (optional)"
                disabled={busy}
                onChange={(e) => onChange(shots.map((s, j) => (j === i ? { ...s, caption: e.target.value } : s)))}
              />
              <div className={styles.shotTools}>
                {i > 0 && (
                  <button type="button" className={styles.textButton} disabled={busy} onClick={() => move(i, 0)}>
                    Make cover
                  </button>
                )}
                <button type="button" className={styles.iconButton} aria-label={`Move screenshot ${i + 1} earlier`} disabled={busy || i === 0} onClick={() => move(i, i - 1)}>
                  ←
                </button>
                <button type="button" className={styles.iconButton} aria-label={`Move screenshot ${i + 1} later`} disabled={busy || i === shots.length - 1} onClick={() => move(i, i + 1)}>
                  →
                </button>
                <button type="button" className={styles.iconButton} aria-label={`Delete screenshot ${i + 1}`} disabled={busy} onClick={() => remove(i)}>
                  ✕
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}
      {room > 0 && (
        <div
          className={`${styles.drop} ${over ? styles.dropOver : ""}`}
          onDragOver={(e) => {
            if (dragging !== null) return;
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            if (dragging !== null) return;
            e.preventDefault();
            setOver(false);
            void addFiles(e.dataTransfer.files);
          }}
        >
          <p>Drag screenshots here, or</p>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => input.current?.click()}>
            Browse files
          </button>
          <input
            ref={input}
            type="file"
            className="w360-visually-hidden"
            accept={ACCEPTED_TYPES.join(",")}
            multiple
            aria-label="Add screenshots"
            aria-describedby={hintId}
            tabIndex={-1}
            onChange={(e) => {
              if (e.target.files) void addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      )}
      {message && (
        <p className="w360-error-text" role="alert">
          {message}
        </p>
      )}
      {enlarged !== null && shots[enlarged] && <Enlarged shots={shots} index={enlarged} onIndex={setEnlarged} onClose={() => setEnlarged(null)} />}
    </div>
  );
}

/** One screenshot full size over the page: Previous / Next, Escape closes. */
function Enlarged({ shots, index, onIndex, onClose }: { shots: ShotDraft[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const close = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowRight") onIndex(Math.min(shots.length - 1, index + 1));
      else if (e.key === "ArrowLeft") onIndex(Math.max(0, index - 1));
    };
    // Captured first, so Escape closes this view and not the page's popup under it.
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [index, shots.length, onClose, onIndex]);
  const shot = shots[index];
  // On the page's body, above the popup - never inside its card.
  return createPortal(
    <div className={styles.viewer} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <p id={titleId} className={styles.viewerTitle}>
        Screenshot {index + 1} of {shots.length}
        {shot.caption ? `: ${shot.caption}` : ""}
      </p>
      {shot.src && (
        // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin staff image
        <img className={styles.viewerImage} src={shot.src} alt={shot.caption || `Screenshot ${index + 1}`} />
      )}
      <div className={styles.viewerTools}>
        <button type="button" className="w360-btn w360-btn-secondary" disabled={index === 0} onClick={() => onIndex(index - 1)}>
          Previous
        </button>
        <button ref={close} type="button" className="w360-btn w360-btn-primary" onClick={onClose}>
          Close
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" disabled={index === shots.length - 1} onClick={() => onIndex(index + 1)}>
          Next
        </button>
      </div>
    </div>,
    document.body,
  );
}
