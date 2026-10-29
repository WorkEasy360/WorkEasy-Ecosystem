/**
 * The Product Manager's shared vocabulary (ADR-051): how a server state reads,
 * and the image checks a picker runs before uploading.
 *
 * Nothing here decides anything. The server owns every rule - these checks
 * only spare an admin a round trip, and the server repeats each one on the
 * bytes it receives.
 */

import type { ConsoleIntegration, ConsoleProduct, TrialConfig } from "@/lib/api/controlPlane";

export type ManagerStatus = ConsoleProduct["manager_status"];
export type Visibility = "published" | "hidden" | "coming_soon";

export const STATUS_LABEL: Record<ManagerStatus, string> = {
  draft: "Draft",
  active: "Active",
  archived: "Archived",
};

export const STATUS_BADGE: Record<ManagerStatus, string> = {
  draft: "w360-badge w360-badge-neutral",
  active: "w360-badge w360-badge-success",
  archived: "w360-badge w360-badge-warning",
};

export function visibilityOf(listing: ConsoleProduct["listing"]): Visibility {
  if (listing === "listed") return "published";
  return listing === "coming_soon" ? "coming_soon" : "hidden";
}

export const VISIBILITY_LABEL: Record<Visibility, string> = {
  published: "Published",
  hidden: "Hidden",
  coming_soon: "Coming soon",
};

export const VISIBILITY_BADGE: Record<Visibility, string> = {
  published: "w360-badge w360-badge-success",
  hidden: "w360-badge w360-badge-neutral",
  coming_soon: "w360-badge w360-badge-info",
};

export function trialLabel(trial: TrialConfig | null): string {
  if (!trial) return "None";
  return trial.enabled ? `${trial.duration_days} days` : "Off";
}

/** What the Integration tab shows, read from server facts (display only). */
export type IntegrationState = "not_configured" | "configured" | "connected" | "error";

export const INTEGRATION_LABEL: Record<IntegrationState, string> = {
  not_configured: "Not configured",
  configured: "Configured",
  connected: "Connected",
  error: "Error",
};

export const INTEGRATION_BADGE: Record<IntegrationState, string> = {
  not_configured: "w360-badge w360-badge-neutral",
  configured: "w360-badge w360-badge-info",
  connected: "w360-badge w360-badge-success",
  error: "w360-badge w360-badge-danger",
};

export function integrationState(link: ConsoleIntegration | null, launchUrl: string): IntegrationState {
  if (!link) return launchUrl ? "configured" : "not_configured";
  if (link.status === "suspended" || link.health.status === "unreachable" || link.health.status === "degraded") return "error";
  if (link.status === "active" && (link.sso === "connected" || link.sign_in_mode === "local_only")) return "connected";
  return "configured";
}

/** `Acme Notes` -> `acme-notes`: the slug a name suggests (the admin may change it). */
export function slugFrom(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32)
    .replace(/-+$/g, "");
}

export const SLUG_PATTERN = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/;

/** The permanent code a slug becomes on the server (`acme-notes` -> `acme_notes`). */
export function codeFrom(slug: string): string {
  return slug.replace(/-/g, "_");
}

/** An address a store page may link to: https and a host. The server applies
 * the full gate (no internal, loopback or metadata hosts). */
export function looksLikeHttps(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !!url.hostname && !url.username && !url.password;
  } catch {
    return false;
  }
}

// --- images -----------------------------------------------------------------------

export type MediaKind = "logo" | "banner" | "screenshot";

interface MediaRule {
  maxBytes: number;
  minWidth: number;
  minHeight: number;
  maxWidth: number;
  maxHeight: number;
}

/** The server's limits (commercial/catalog/domain.py MEDIA_RULES), repeated so
 * a picker can say "too small" before uploading. */
export const MEDIA_RULES: Record<MediaKind, MediaRule> = {
  logo: { maxBytes: 512 * 1024, minWidth: 64, minHeight: 64, maxWidth: 2048, maxHeight: 2048 },
  banner: { maxBytes: 2 * 1024 * 1024, minWidth: 600, minHeight: 150, maxWidth: 4096, maxHeight: 4096 },
  screenshot: { maxBytes: 2 * 1024 * 1024, minWidth: 320, minHeight: 200, maxWidth: 4096, maxHeight: 4096 },
};

export const MAX_SCREENSHOTS = 8;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg"];

export const MEDIA_HINT: Record<MediaKind, string> = {
  logo: "PNG or JPEG, up to 512 KB, at least 64×64 px (square works best).",
  banner: "PNG or JPEG, up to 2 MB, at least 600×150 px.",
  screenshot: "PNG or JPEG, up to 2 MB each, at least 320×200 px.",
};

function bytesOf(file: Blob, count: number): Promise<Uint8Array> {
  const part = file.slice(0, count);
  if (typeof part.arrayBuffer === "function") return part.arrayBuffer().then((b) => new Uint8Array(b));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(part);
  });
}

/** Width and height read from a PNG's IHDR or a JPEG's start-of-frame - the
 * same parse the server does, so no image is decoded in the browser. */
export async function imageSize(file: Blob): Promise<{ width: number; height: number } | null> {
  const b = await bytesOf(file, 256 * 1024);
  const u32 = (i: number) => ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0;
  const u16 = (i: number) => (b[i] << 8) | b[i + 1];
  if (b.length >= 24 && b[0] === 0x89 && b[1] === 0x50 && b[12] === 0x49 && b[13] === 0x48) {
    return { width: u32(16), height: u32(20) };
  }
  if (b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i + 4 <= b.length) {
    if (b[i] !== 0xff) return null;
    const marker = b[i + 1];
    if (marker === 0xff) {
      i += 1;
      continue;
    }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      i += 2;
      continue;
    }
    const length = u16(i + 2);
    if (length < 2) return null;
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      if (i + 9 > b.length) return null;
      return { width: u16(i + 7), height: u16(i + 5) };
    }
    i += 2 + length;
  }
  return null;
}

/** A problem with this file for this kind of image, or `null` when it may be uploaded. */
export async function checkImage(file: File, kind: MediaKind): Promise<string | null> {
  if (!ACCEPTED_TYPES.includes(file.type)) return "Choose a PNG or JPEG image. SVG and other formats are not accepted.";
  const rule = MEDIA_RULES[kind];
  if (file.size === 0 || file.size > rule.maxBytes) return `The file must be at most ${Math.round(rule.maxBytes / 1024)} KB.`;
  const size = await imageSize(file);
  if (!size) return "This file is not a readable PNG or JPEG image.";
  if (size.width < rule.minWidth || size.height < rule.minHeight || size.width > rule.maxWidth || size.height > rule.maxHeight) {
    return `The image is ${size.width}×${size.height} px; it must be from ${rule.minWidth}×${rule.minHeight} to ${rule.maxWidth}×${rule.maxHeight} px.`;
  }
  return null;
}

/** A local file as a `data:` URL for its preview - the CSP allows images from
 * `'self'` and `data:` only (never `blob:`), and is not loosened for this. */
export function readDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function dateOnly(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
