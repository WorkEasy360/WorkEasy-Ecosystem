"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ACCEPTED_TYPES, MAX_SCREENSHOTS, MEDIA_HINT, checkImage, formatBytes, readDataUrl, type MediaKind } from "./model";
import styles from "./ProductManager.module.css";

/** A local file's preview, as a `data:` URL (see `readDataUrl`). */
export function useDataUrl(file: File | null): string | null {
  const [state, setState] = useState<{ file: File; url: string } | null>(null);
  useEffect(() => {
    if (!file) return;
    let live = true;
    void readDataUrl(file)
      .then((url) => {
        if (live) setState({ file, url });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [file]);
  return file && state?.file === file ? state.url : null;
}

/** Drag-and-drop or browse, keyboard reachable through its button. Hands over
 * only files that pass the client-side check; the server checks them again. */
function DropZone({
  kind,
  multiple = false,
  disabled = false,
  describedBy,
  inputLabel,
  onFiles,
  onProblem,
  invalid,
}: {
  kind: MediaKind;
  multiple?: boolean;
  disabled?: boolean;
  describedBy: string;
  inputLabel: string;
  onFiles: (files: File[]) => void;
  onProblem: (message: string | null) => void;
  invalid: boolean;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);

  async function take(list: FileList | File[] | null) {
    const files = [...(list ?? [])];
    if (files.length === 0) return;
    const accepted: File[] = [];
    for (const file of multiple ? files : files.slice(0, 1)) {
      const problem = await checkImage(file, kind);
      if (problem) {
        onProblem(`${file.name}: ${problem}`);
        return;
      }
      accepted.push(file);
    }
    onProblem(null);
    onFiles(accepted);
  }

  return (
    <div
      className={`${styles.drop} ${over ? styles.dropActive : ""} ${invalid ? styles.dropError : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) void take(e.dataTransfer.files);
      }}
    >
      <p className={styles.dropText}>
        <strong>Drag {multiple ? "images" : "an image"} here</strong> or
      </p>
      <button type="button" className="w360-btn w360-btn-secondary" disabled={disabled} onClick={() => input.current?.click()}>
        Browse file{multiple ? "s" : ""}
      </button>
      <input
        ref={input}
        className={styles.hiddenInput}
        type="file"
        accept={ACCEPTED_TYPES.join(",")}
        multiple={multiple}
        aria-label={inputLabel}
        aria-describedby={describedBy}
        tabIndex={-1}
        disabled={disabled}
        onChange={(e) => {
          void take(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function UploadProgress({ label, value }: { label: string; value: number }) {
  return (
    <progress className={styles.progress} max={100} value={value} aria-label={`${label} upload`}>
      {value}%
    </progress>
  );
}

/** One image (a logo or a banner): drop or browse, preview, remove, progress
 * and validation. `currentUrl` is the image already stored for the product. */
export function ImagePicker({
  label,
  kind,
  required = false,
  file,
  currentUrl,
  onSelect,
  onRemove,
  progress,
  error,
  busy = false,
}: {
  label: string;
  kind: "logo" | "banner";
  required?: boolean;
  file: File | null;
  currentUrl?: string | null;
  onSelect: (file: File) => void;
  onRemove: () => void;
  progress?: number | null;
  error?: string | null;
  busy?: boolean;
}) {
  const hintId = useId();
  const errorId = useId();
  const [problem, setProblem] = useState<string | null>(null);
  const preview = useDataUrl(file);
  const shown = preview ?? (file ? null : currentUrl ?? null);
  const message = problem ?? error ?? null;

  return (
    <div className={styles.picker} role="group" aria-labelledby={`${hintId}-label`}>
      <span id={`${hintId}-label`} className="w360-label">
        {label}
        {required && (
          <span aria-hidden="true" className={styles.muted}>
            {" *"}
          </span>
        )}
      </span>
      <p id={hintId} className="w360-hint">
        {MEDIA_HINT[kind]}
      </p>
      {shown || file ? (
        <div className={styles.previewRow}>
          {shown && (
            // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin image
            <img className={`${styles.thumb} ${kind === "logo" ? styles.thumbSquare : ""}`} src={shown} alt={`${label} preview`} />
          )}
          <div className={styles.previewMeta}>
            {file ? (
              <>
                <strong>{file.name}</strong>
                <br />
                {formatBytes(file.size)}
              </>
            ) : (
              <span>Current {label.toLowerCase()}</span>
            )}
            {progress != null && <UploadProgress label={label} value={progress} />}
          </div>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={onRemove}>
            Remove
          </button>
        </div>
      ) : null}
      {!file && (
        <DropZone
          kind={kind}
          describedBy={message ? `${hintId} ${errorId}` : hintId}
          inputLabel={shown ? `Replace ${label.toLowerCase()}` : label}
          disabled={busy}
          invalid={!!message}
          onProblem={setProblem}
          onFiles={(files) => onSelect(files[0])}
        />
      )}
      {message && (
        <p id={errorId} className="w360-error-text" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}

/** One screenshot in a list: a stored one (`url`) or one waiting to upload (`file`). */
export interface ShotItem {
  key: string;
  file?: File;
  url?: string;
  caption: string;
  progress?: number | null;
}

function ShotRow({
  shot,
  index,
  count,
  busy,
  onMove,
  onRemove,
  onCaption,
  onCaptionCommit,
}: {
  shot: ShotItem;
  index: number;
  count: number;
  busy: boolean;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
  onCaption: (index: number, caption: string) => void;
  onCaptionCommit?: (index: number) => void;
}) {
  const preview = useDataUrl(shot.file ?? null);
  const captionId = useId();
  const src = preview ?? shot.url ?? null;
  const n = index + 1;
  return (
    <li className={styles.previewRow}>
      {src && (
        // eslint-disable-next-line @next/next/no-img-element -- a local data: preview or a same-origin image
        <img className={styles.thumb} src={src} alt={`Screenshot ${n} preview`} />
      )}
      <div className={styles.previewMeta}>
        <label className="w360-label" htmlFor={captionId}>
          Caption for screenshot {n} (optional)
        </label>
        <input
          id={captionId}
          className="w360-input"
          maxLength={140}
          value={shot.caption}
          disabled={busy}
          onChange={(e) => onCaption(index, e.target.value)}
          onBlur={() => onCaptionCommit?.(index)}
        />
        {shot.progress != null && <UploadProgress label={`Screenshot ${n}`} value={shot.progress} />}
      </div>
      <div className={styles.toolbar}>
        <button type="button" className="w360-btn w360-btn-secondary" aria-label={`Move screenshot ${n} up`} disabled={busy || index === 0} onClick={() => onMove(index, index - 1)}>
          ↑
        </button>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-label={`Move screenshot ${n} down`}
          disabled={busy || index === count - 1}
          onClick={() => onMove(index, index + 1)}
        >
          ↓
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" aria-label={`Delete screenshot ${n}`} disabled={busy} onClick={() => onRemove(index)}>
          Delete
        </button>
      </div>
    </li>
  );
}

/** An ordered list of screenshots: add (several at once), reorder, caption, delete. */
export function ScreenshotsField({
  shots,
  onAdd,
  onMove,
  onRemove,
  onCaption,
  onCaptionCommit,
  error,
  busy = false,
}: {
  shots: ShotItem[];
  onAdd: (files: File[]) => void;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
  onCaption: (index: number, caption: string) => void;
  onCaptionCommit?: (index: number) => void;
  error?: string | null;
  busy?: boolean;
}) {
  const hintId = useId();
  const [problem, setProblem] = useState<string | null>(null);
  const room = MAX_SCREENSHOTS - shots.length;
  const message = problem ?? error ?? null;
  return (
    <div className={styles.picker} role="group" aria-labelledby={`${hintId}-label`}>
      <span id={`${hintId}-label`} className="w360-label">
        Screenshots
      </span>
      <p id={hintId} className="w360-hint">
        {MEDIA_HINT.screenshot} Up to {MAX_SCREENSHOTS}; shown in this order.
      </p>
      {shots.length > 0 && (
        <ol className={styles.shotList} aria-label="Screenshots">
          {shots.map((shot, i) => (
            <ShotRow
              key={shot.key}
              shot={shot}
              index={i}
              count={shots.length}
              busy={busy}
              onMove={onMove}
              onRemove={onRemove}
              onCaption={onCaption}
              onCaptionCommit={onCaptionCommit}
            />
          ))}
        </ol>
      )}
      {room > 0 ? (
        <DropZone
          kind="screenshot"
          multiple
          describedBy={hintId}
          inputLabel="Add screenshots"
          disabled={busy}
          invalid={!!message}
          onProblem={setProblem}
          onFiles={(files) => {
            if (files.length > room) {
              setProblem(`Only ${room} more screenshot${room === 1 ? "" : "s"} can be added.`);
              return;
            }
            onAdd(files);
          }}
        />
      ) : (
        <p className={styles.muted}>The maximum of {MAX_SCREENSHOTS} screenshots is reached.</p>
      )}
      {message && (
        <p className="w360-error-text" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
