"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountCenterApi, type AccountProfile, type ProfileChanges, type ProfileOptions } from "@/lib/api/console";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { Badge, Button, CopyButton, initialsOf } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { countryName } from "@/features/controlplane/countries";
import styles from "./Account.module.css";

/**
 * My Profile (Accounts V3, the note's 04 and 8.1): the person's name, photo, country,
 * language and personal time zone - the profile WorkEasy360 Accounts is authoritative
 * for. Products read it at their next sign-in. Language and time zone guide display
 * only; they never change an organization's reporting or billing.
 *
 * The sign-in email is not edited here: it has its own verified change on Login &
 * Security.
 */

// --- names for codes ------------------------------------------------------------------

let languageNames: Intl.DisplayNames | null | undefined;

/** A language code's English name, or the code itself. */
export function languageName(code: string): string {
  if (!code) return "";
  if (languageNames === undefined) {
    try {
      languageNames = new Intl.DisplayNames(["en"], { type: "language" });
    } catch {
      languageNames = null;
    }
  }
  try {
    return languageNames?.of(code) ?? code;
  } catch {
    return code;
  }
}

/** An IANA zone as people read it: "Asia/Kolkata" -> "Asia / Kolkata". */
export function timeZoneLabel(zone: string): string {
  return zone.replace(/_/g, " ").replace(/\//g, " / ");
}

/** This device's own time zone, when the server offers it. */
function deviceTimeZone(zones: string[]): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone && zones.includes(zone) ? zone : null;
  } catch {
    return null;
  }
}

const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label);

// --- the photo ------------------------------------------------------------------------

export const MAX_PHOTO_BYTES = 262144;
export const MAX_PHOTO_SIDE = 512;

export interface ScaledPhoto {
  /** What is uploaded: WebP or JPEG, at most 512x512 and 256 KB, no camera metadata. */
  blob: Blob;
  /** A `data:` URL of the same picture, for the preview (the page's CSP allows `data:` images). */
  preview: string;
}

export class PhotoProblem extends Error {}

async function decode(file: Blob): Promise<{ source: CanvasImageSource; width: number; height: number; done: () => void }> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    return { source: bitmap, width: bitmap.width, height: bitmap.height, done: () => bitmap.close() };
  }
  const url = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new PhotoProblem("unreadable"));
    reader.readAsDataURL(file);
  });
  const image = new Image();
  image.src = url;
  await image.decode();
  return { source: image, width: image.naturalWidth, height: image.naturalHeight, done: () => undefined };
}

/** Scales a chosen picture to at most 512x512 on a canvas - which also drops its camera
 * metadata (location, device) - and exports WebP, else JPEG, small enough to send. */
async function scalePhoto(file: File): Promise<ScaledPhoto> {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) throw new PhotoProblem("type");
  let picture;
  try {
    picture = await decode(file);
  } catch {
    throw new PhotoProblem("unreadable");
  }
  const scale = Math.min(1, MAX_PHOTO_SIDE / Math.max(picture.width, picture.height, 1));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(picture.width * scale));
  canvas.height = Math.max(1, Math.round(picture.height * scale));
  const context = canvas.getContext("2d");
  if (!context) throw new PhotoProblem("unreadable");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(picture.source, 0, 0, canvas.width, canvas.height);
  picture.done();
  for (const type of ["image/webp", "image/jpeg"]) {
    for (const quality of [0.9, 0.8, 0.7, 0.6, 0.5]) {
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));
      // A browser without a WebP encoder answers PNG instead: try the next type.
      if (blob && blob.type === type && blob.size <= MAX_PHOTO_BYTES) return { blob, preview: canvas.toDataURL(type, quality) };
    }
  }
  throw new PhotoProblem("too_large");
}

/** The browser seam for the picture (jsdom draws nothing): tests replace `scale`. */
export const photoScaler = { scale: scalePhoto };

const PHOTO_PROBLEMS: Record<string, string> = {
  type: "Choose a PNG, JPEG or WebP picture.",
  unreadable: "That picture could not be read. Choose another one.",
  too_large: "That picture is too detailed to send. Choose a smaller one.",
};

// --- the header card --------------------------------------------------------------------

export function ProfilePicture({ profile, preview }: { profile: Pick<AccountProfile, "name" | "email" | "photo_url">; preview?: string | null }) {
  const src = preview === undefined ? profile.photo_url : preview;
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element -- a same-origin capability URL or a data: preview
    <img className={styles.photo} src={src} alt="" width={72} height={72} />
  ) : (
    <span className={styles.initials} aria-hidden="true">
      {initialsOf(profile.name || profile.email)}
    </span>
  );
}

/** Who this account is: photo or initials, name, sign-in email and WorkEasy360 ID. */
export function ProfileHeader({ profile }: { profile: AccountProfile }) {
  return (
    <Panel title="Your WorkEasy360 account">
      <div className={styles.identity}>
        <ProfilePicture profile={profile} />
        <div className={styles.identityText}>
          <p>
            <strong>{profile.name || "Name not set"}</strong>
          </p>
          <p className={styles.inline}>
            <span>{profile.email}</span>
            {profile.email_verified ? <Badge tone="success">Verified</Badge> : <Badge tone="warning">Not verified</Badge>}
          </p>
          {profile.pending_email && (
            <p className={styles.inline}>
              <Badge tone="info">Pending verification</Badge>
              <span>
                Changing to {profile.pending_email.new_email} - <a href="/account/security">see Login &amp; Security</a>
              </span>
            </p>
          )}
          <p className={styles.inline}>
            <span className={styles.muted}>WorkEasy360 ID</span>
            <span className={styles.mono}>{profile.user_id}</span>
            <CopyButton value={profile.user_id} label="Copy WorkEasy360 ID" />
          </p>
        </div>
      </div>
    </Panel>
  );
}

// --- the profile card -------------------------------------------------------------------

export const PROFILE_SAVED = "Saved. Products show your new details the next time you open them.";

/** The profile card: read mode with Edit, then Save / Cancel. Cancel restores the saved
 * values - nothing is sent until Save. */
export function ProfileCard({
  profile,
  onSaved,
  editing: editingProp,
  onEditingChange,
}: {
  profile: AccountProfile;
  onSaved: (profile: AccountProfile) => void;
  /** Controlled edit mode (My Profile's "Edit Profile" opens it from the card above). */
  editing?: boolean;
  onEditingChange?: (editing: boolean) => void;
}) {
  const [ownEditing, setOwnEditing] = useState(false);
  const editing = editingProp ?? ownEditing;
  const setEditing = onEditingChange ?? setOwnEditing;
  const [saved, setSaved] = useState(false);
  return (
    <Panel
      title="Profile"
      description="Products show your new details the next time you open them."
      actions={
        editing ? undefined : (
          <Button
            icon="edit"
            onClick={() => {
              setSaved(false);
              setEditing(true);
            }}
          >
            Edit
          </Button>
        )
      }
    >
      {editing ? (
        <ProfileEditor
          profile={profile}
          onCancel={() => setEditing(false)}
          onChanged={onSaved}
          onSaved={(next) => {
            onSaved(next);
            setEditing(false);
            setSaved(true);
          }}
        />
      ) : (
        <>
          <dl className={styles.facts}>
            <dt>Name</dt>
            <dd>{profile.name || "Not set"}</dd>
            <dt>Photo</dt>
            <dd>{profile.photo_url ? "Added" : "None"}</dd>
            <dt>Country</dt>
            <dd>{profile.country ? countryName(profile.country) : "Not set"}</dd>
            <dt>Language</dt>
            <dd>{profile.language ? languageName(profile.language) : "Not set"}</dd>
            <dt>Time zone</dt>
            <dd>{profile.time_zone ? timeZoneLabel(profile.time_zone) : "Not set"}</dd>
          </dl>
          {saved && <p role="status">{PROFILE_SAVED}</p>}
        </>
      )}
    </Panel>
  );
}

type PhotoDraft = { kind: "keep" } | { kind: "remove" } | ({ kind: "new" } & ScaledPhoto);

function ProfileEditor({
  profile,
  onCancel,
  onChanged,
  onSaved,
}: {
  profile: AccountProfile;
  onCancel: () => void;
  /** A part was saved although the rest failed (the photo went, the details did not). */
  onChanged: (profile: AccountProfile) => void;
  onSaved: (profile: AccountProfile) => void;
}) {
  const [options] = useLoad(() => accountCenterApi.profileOptions(), []);
  return (
    <LoadGate load={options} what="the profile choices">
      {(o) => <ProfileFields profile={profile} options={o} onCancel={onCancel} onChanged={onChanged} onSaved={onSaved} />}
    </LoadGate>
  );
}

function ProfileFields({
  profile,
  options,
  onCancel,
  onChanged,
  onSaved,
}: {
  profile: AccountProfile;
  options: ProfileOptions;
  onCancel: () => void;
  onChanged: (profile: AccountProfile) => void;
  onSaved: (profile: AccountProfile) => void;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const [name, setName] = useState(profile.name);
  const [country, setCountry] = useState(profile.country || "");
  const [language, setLanguage] = useState(profile.language || "");
  const [timeZone, setTimeZone] = useState(profile.time_zone || "");
  const [photo, setPhoto] = useState<PhotoDraft>({ kind: "keep" });
  const [preparing, setPreparing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ text: string; field?: string } | null>(null);

  const countries = options.countries.map((code) => ({ value: code, label: countryName(code) })).sort(byLabel);
  const languages = options.languages.map((code) => ({ value: code, label: languageName(code) })).sort(byLabel);
  const device = deviceTimeZone(options.time_zones);
  const hasPhoto = photo.kind === "new" || (photo.kind === "keep" && Boolean(profile.photo_url));
  const preview = photo.kind === "new" ? photo.preview : photo.kind === "remove" ? null : undefined;

  async function choose(file: File | undefined) {
    if (!file) return;
    setProblem(null);
    setPreparing(true);
    try {
      const scaled = await photoScaler.scale(file);
      setPhoto({ kind: "new", ...scaled });
    } catch (err) {
      const reason = err instanceof PhotoProblem ? err.message : "unreadable";
      setProblem({ text: PHOTO_PROBLEMS[reason] ?? PHOTO_PROBLEMS.unreadable, field: "photo" });
    } finally {
      setPreparing(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const fullName = name.trim().replace(/\s+/g, " ");
    if (!fullName) {
      setProblem({ text: "Enter your name.", field: "full_name" });
      return;
    }
    const changes: ProfileChanges = {};
    if (fullName !== profile.name) changes.full_name = fullName;
    if (country !== (profile.country || "")) changes.country = country;
    if (language !== (profile.language || "")) changes.language = language;
    if (timeZone !== (profile.time_zone || "")) changes.time_zone = timeZone;
    setBusy(true);
    setProblem(null);
    let latest = profile;
    let step: "photo" | "details" = "photo";
    try {
      if (photo.kind === "new") latest = await accountCenterApi.uploadPhoto(photo.blob);
      else if (photo.kind === "remove") latest = await accountCenterApi.removePhoto();
      if (latest !== profile) {
        setPhoto({ kind: "keep" });
        onChanged(latest);
      }
      step = "details";
      if (Object.keys(changes).length > 0) latest = await accountCenterApi.updateProfile(changes);
      onSaved(latest);
    } catch (err) {
      const body = err instanceof ApiError ? (err.body as { detail?: string; field?: string } | undefined) : undefined;
      if (err instanceof ApiError && err.code === "photo_invalid") {
        setProblem({ text: body?.detail || "That picture could not be used. Choose a PNG, JPEG or WebP picture.", field: "photo" });
      } else if (err instanceof ApiError && err.status === 429) {
        setProblem({ text: "Too many changes in a short time. Wait a minute and try again." });
      } else {
        const what = step === "photo" ? "Your photo" : "Your details";
        setProblem({ text: body?.detail || `${what} could not be saved. Try again.`, field: body?.field });
      }
      setBusy(false);
    }
  }

  const invalid = (field: string) => (problem?.field === field ? true : undefined);
  const describedBy = (field: string) => (problem?.field === field ? errorId : undefined);
  return (
    <form method="post" className={styles.form} onSubmit={save} noValidate>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={`${id}-name`}>
          Full name
        </label>
        <input
          id={`${id}-name`}
          className="w360-input"
          name="full_name"
          autoComplete="name"
          maxLength={201}
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={invalid("full_name")}
          aria-describedby={describedBy("full_name")}
        />
      </div>

      <fieldset className={styles.field}>
        <legend className="w360-label">Photo</legend>
        <div className={styles.photoRow}>
          <ProfilePicture profile={profile} preview={preview} />
          <div className={styles.field}>
            <label className="w360-label" htmlFor={`${id}-photo`}>
              Choose a photo
            </label>
            <input
              id={`${id}-photo`}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy || preparing}
              onChange={(e) => {
                void choose(e.target.files?.[0]);
                e.target.value = "";
              }}
              aria-invalid={invalid("photo")}
              aria-describedby={describedBy("photo") ?? `${id}-photo-hint`}
            />
            <p id={`${id}-photo-hint`} className="w360-hint">
              PNG, JPEG or WebP. It is made smaller on this device before it is sent.
            </p>
          </div>
          {hasPhoto && (
            <Button size="sm" variant="ghost" icon="trash" disabled={busy || preparing} onClick={() => setPhoto({ kind: "remove" })}>
              Remove photo
            </Button>
          )}
        </div>
        {preparing && <p aria-live="polite">Preparing your photo…</p>}
        {photo.kind === "new" && <p className={styles.muted}>New photo chosen. It is saved when you press Save.</p>}
        {photo.kind === "remove" && <p className={styles.muted}>Your photo will be removed when you press Save.</p>}
      </fieldset>

      <div className={styles.fields}>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-country`}>
            Country
          </label>
          <select
            id={`${id}-country`}
            className="w360-input"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            aria-invalid={invalid("country")}
            aria-describedby={describedBy("country")}
          >
            <option value="">Not set</option>
            {countries.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-language`}>
            Language
          </label>
          <select
            id={`${id}-language`}
            className="w360-input"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            aria-invalid={invalid("language")}
            aria-describedby={describedBy("language")}
          >
            <option value="">Not set</option>
            {languages.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={`${id}-zone`}>
            Time zone
          </label>
          <select
            id={`${id}-zone`}
            className="w360-input"
            value={timeZone}
            onChange={(e) => setTimeZone(e.target.value)}
            aria-invalid={invalid("time_zone")}
            aria-describedby={describedBy("time_zone") ?? `${id}-zone-hint`}
          >
            <option value="">Not set</option>
            {options.time_zones.map((zone) => (
              <option key={zone} value={zone}>
                {timeZoneLabel(zone)}
              </option>
            ))}
          </select>
          <p id={`${id}-zone-hint`} className="w360-hint">
            For how times are shown to you. Your organizations&apos; reports and billing keep their own.
          </p>
          {device && device !== timeZone && (
            <div>
              <Button size="sm" onClick={() => setTimeZone(device)}>
                Use this device&apos;s time zone ({timeZoneLabel(device)})
              </Button>
            </div>
          )}
        </div>
      </div>

      {problem && (
        <p id={errorId} role="alert" className="w360-error-text">
          {problem.text}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" variant="primary" loading={busy} disabled={preparing}>
          Save
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
