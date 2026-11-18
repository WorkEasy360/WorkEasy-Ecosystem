/**
 * The Product Manager's shared vocabulary (ADR-051): how a server state reads,
 * and the image checks a picker runs before uploading.
 *
 * Nothing here decides anything. The server owns every rule - these checks
 * only spare an admin a round trip, and the server repeats each one on the
 * bytes it receives.
 */

import { ApiError } from "@/lib/api/client";
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
/** A YouTube or Vimeo link, as the server accepts it (the server decides). */
export function looksLikeVideo(value: string): boolean {
  return /^https:\/\/(?:(?:www\.|m\.)?youtube\.com\/(?:watch\?|shorts\/|embed\/|live\/)|youtu\.be\/|www\.youtube-nocookie\.com\/embed\/|(?:www\.)?vimeo\.com\/\d|player\.vimeo\.com\/video\/\d)/.test(value);
}

export function looksLikeHttps(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !!url.hostname && !url.username && !url.password;
  } catch {
    return false;
  }
}

// --- single sign-on (ADR-069, ADR-070) -----------------------------------------------

const originOf = (value: string): string | null => {
  try {
    return new URL(value.trim()).origin;
  } catch {
    return null;
  }
};

/** Why a back-channel logout URL would be refused, or `undefined` ("" is no
 * URL). The registry's rules, repeated to spare a round trip: https, no query,
 * fragment, wildcard or credentials, and the same origin as one of the
 * product's sign-in callback (redirect) URIs. The server checks it again. */
export function backchannelUriProblem(value: string, callbacks: string[]): string | undefined {
  const raw = value.trim();
  if (!raw) return undefined;
  if (raw.includes("*")) return "Wildcards are not accepted.";
  if (raw.includes("\\")) return "A backslash is not accepted in the address.";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "Enter a full https:// address.";
  }
  if (url.protocol !== "https:" || !url.hostname) return "Use an https:// address.";
  if (url.username || url.password) return "Remove the user name and password from the address.";
  if (raw.includes("?")) return "Remove the query string (from ?).";
  if (raw.includes("#")) return "Remove the fragment (from #).";
  const origins = callbacks.map(originOf).filter((o): o is string => !!o);
  if (origins.length === 0) return "Add the sign-in callback URL first; the back-channel logout URL must share its origin.";
  if (!origins.includes(url.origin)) return `Use the same origin (scheme, host and port) as a sign-in callback URL (${origins.join(" or ")}).`;
  return undefined;
}

/** Why a typed hostname cannot be asked about, or `undefined`. Only the
 * shape: a bare name such as app.example.com (the server canonicalizes it and
 * decides the rest). */
export function hostnameProblem(value: string): string | undefined {
  const raw = value.trim();
  if (!raw) return "Enter a hostname.";
  if (/[\s/:?#@*\\]/.test(raw)) return "Enter the hostname only, such as app.example.com - no https://, port, path or wildcard.";
  if (!raw.includes(".") && raw.toLowerCase() !== "localhost") return "Enter a full hostname, such as app.example.com.";
  return undefined;
}

/** A refusal because a hostname's control is not proven yet (HTTP 409
 * `product_domain_unverified`, ADR-070): the server's message and every
 * hostname it names. */
export interface DomainBlock {
  detail: string;
  hostnames: string[];
}

/** The {@link DomainBlock} in an API error, or `null` for any other error. */
export function domainBlockOf(err: unknown): DomainBlock | null {
  if (!(err instanceof ApiError) || err.status !== 409 || err.code !== "product_domain_unverified") return null;
  const body = (err.body ?? {}) as { detail?: unknown; hostnames?: unknown };
  const hostnames = Array.isArray(body.hostnames) ? body.hostnames.filter((h): h is string => typeof h === "string" && h !== "") : [];
  const detail = typeof body.detail === "string" && body.detail ? body.detail : "Prove control of the product's domains first.";
  return { detail, hostnames };
}

// --- images -----------------------------------------------------------------------

/** 2026-11-16: `video_poster` is the product demo's thumbnail. */
export type MediaKind = "logo" | "banner" | "screenshot" | "video_poster";

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
  video_poster: { maxBytes: 2 * 1024 * 1024, minWidth: 320, minHeight: 180, maxWidth: 4096, maxHeight: 4096 },
};

export const MAX_SCREENSHOTS = 10;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg"];

export const MEDIA_HINT: Record<MediaKind, string> = {
  logo: "PNG or JPEG, up to 512 KB, at least 64×64 px (square works best).",
  banner: "PNG or JPEG, up to 2 MB, at least 600×150 px.",
  screenshot: "PNG or JPEG, up to 2 MB each, at least 320×200 px.",
  video_poster: "PNG or JPEG, up to 2 MB, at least 320×180 px (16:9 works best).",
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

/** A form's own refusal (before any request), thrown from a `FormDialog`'s
 * `onSubmit`: the popup shows its text the way it shows the server's own
 * refusal (`describeError` reads `detail`) and stays open with the values kept. */
export function refusal(detail: string): ApiError {
  return new ApiError(400, { detail });
}
