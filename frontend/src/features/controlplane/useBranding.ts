"use client";

import { useEffect, useState } from "react";
import { brandingApi, type OwnBrand, type PublicPortalFields } from "@/lib/api/controlPlane";

/** Only a strict six-digit hex colour ever reaches the DOM. */
const HEX = /^#[0-9a-fA-F]{6}$/;

export function safeColor(value: string | null | undefined): string | null {
  return value && HEX.test(value) ? value : null;
}

/** WCAG relative luminance of a validated `#rrggbb` colour. */
function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** WCAG contrast ratio between two relative luminances. */
function ratio(a: number, b: number): number {
  const [hi, lo] = a >= b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * The text colour (black or white) with the higher contrast on `hex`, so no
 * accent a partner picks - `#ffffff` included - can make the sign-in button's
 * label unreadable. The branded page must always remain usable.
 */
export function contrastOn(hex: string): "#000000" | "#ffffff" {
  const l = luminance(hex);
  return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? "#000000" : "#ffffff";
}

function mix(hex: string, toward: "#000000" | "#ffffff", amount: number): string {
  const target = toward === "#ffffff" ? 255 : 0;
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16);
    return Math.round(c + (target - c) * amount)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(1)}${channel(3)}${channel(5)}`;
}

// The lightest surface a link can sit on in each theme (see globals.css).
const LIGHT_SURFACE = "#f7f8fa";
const DARK_SURFACE = "#1d212a";
const WCAG_AA_TEXT = 4.5;

/**
 * The accent, moved as little as possible toward black (`darker`) or white, until
 * it reads on `surface` at AA for text. An accent that already does is returned
 * unchanged. Used for link text: a partner's accent is fine as a *fill* (the
 * button's label colour is chosen for it) but a mid-tone green or a pale yellow
 * as link text fails on one theme or the other.
 */
function readableOn(hex: string, surface: string, toward: "#000000" | "#ffffff"): string {
  const bg = luminance(surface);
  for (let step = 0; step <= 20; step++) {
    const candidate = mix(hex, toward, step / 20);
    if (ratio(luminance(candidate), bg) >= WCAG_AA_TEXT) return candidate;
  }
  return toward;
}

/** The accent as link text on the light theme. */
export function textOnLight(hex: string): string {
  return readableOn(hex, LIGHT_SURFACE, "#000000");
}

/** The accent as link text on the dark theme. */
export function textOnDark(hex: string): string {
  return readableOn(hex, DARK_SURFACE, "#ffffff");
}

/**
 * The hover fill: the accent moved *away* from its label colour, so the label
 * (chosen for the accent) is at least as readable on hover as at rest. Without
 * this the hover fill stayed the platform's indigo under a label chosen for the
 * partner's accent - black on indigo for a pale accent.
 */
export function hoverOf(hex: string): string {
  return mix(hex, contrastOn(hex) === "#ffffff" ? "#000000" : "#ffffff", 0.15);
}

const ACCENT_VARIABLES = [
  "--w360-brand",
  "--w360-brand-contrast",
  "--w360-brand-hover",
  "--w360-brand-text-on-light",
  "--w360-brand-text-on-dark",
] as const;

/**
 * Applies a partner's accent colour to an element through the CSSOM
 * (`style.setProperty`), which the production CSP permits and which cannot
 * inject markup or a selector. Anything that is not a plain hex colour is
 * ignored, so a malformed or hostile value falls back to the default theme.
 * Every colour that follows from the accent - the label on it, its hover fill,
 * its link text on each theme - is derived here, never taken from the partner,
 * so no accent can make the page unreadable.
 */
export function applyAccent(el: HTMLElement, color: string | null | undefined): void {
  const safe = safeColor(color);
  if (safe) {
    el.style.setProperty("--w360-brand", safe);
    el.style.setProperty("--w360-brand-contrast", contrastOn(safe));
    el.style.setProperty("--w360-brand-hover", hoverOf(safe));
    el.style.setProperty("--w360-brand-text-on-light", textOnLight(safe));
    el.style.setProperty("--w360-brand-text-on-dark", textOnDark(safe));
  } else {
    for (const name of ACCENT_VARIABLES) el.style.removeProperty(name);
  }
}

export interface HostBrand {
  brand_name: string;
  accent_color: string;
  support_email: string;
  support_url: string;
  logo_url: string | null;
  /** The words and support details a customer reads (plain text; see ``brandDisplay``). */
  portal: PublicPortalFields;
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");

/**
 * The portal fields as the page may rely on them: every key present, every text a
 * string, both sign-in choices a boolean (shown unless the server said otherwise).
 * A response from before the portal existed - or any malformed one - reads as "no
 * words set", never as a crash, and never as markup: these are drawn as text only.
 */
export function normalizePortal(raw: Partial<PublicPortalFields> | null | undefined): PublicPortalFields {
  const p = raw ?? {};
  return {
    short_name: text(p.short_name),
    support_name: text(p.support_name),
    support_phone: text(p.support_phone),
    website_url: text(p.website_url),
    welcome_message: text(p.welcome_message),
    footer_text: text(p.footer_text),
    login_show_welcome: typeof p.login_show_welcome === "boolean" ? p.login_show_welcome : true,
    login_show_support: typeof p.login_show_support === "boolean" ? p.login_show_support : true,
  };
}

/**
 * The white-label brand for the host the visitor is on, if a partner's active
 * verified domain matches it. The server resolves the brand from the explicit
 * `host` parameter sent here (it never reads its own request's Host header,
 * which the frontend proxy rewrites anyway), and the answer is presentation
 * only - it carries no authority. Failure is silent: an unbranded page is
 * always a valid page.
 *
 * `forHost` (Phase 12, D-035): the canonical sign-in page presents the brand
 * of the partner domain a visitor came from (`/login?brand=<host>`). Still
 * presentation only, and still resolved by the server among *active, verified*
 * domains - an unknown or revoked host simply renders unbranded.
 */
export function useBranding(forHost?: string): HostBrand | null {
  const [brand, setBrand] = useState<HostBrand | null>(null);
  useEffect(() => {
    let cancelled = false;
    brandingApi
      .forHost(forHost ?? window.location.host)
      .then(({ brand: b }) => {
        if (cancelled || !b) return;
        setBrand({ ...b, portal: normalizePortal(b.portal) });
        applyAccent(document.documentElement, b.accent_color);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [forHost]);
  return brand;
}

/**
 * This organization's own white-label brand (Phase 11, Step 30), for the
 * customer-facing console - resolved through its live partner link, never a
 * Host header, so it works on the canonical Workeasy360 domain. Since Phase 12
 * (D-035, decision B) this *is* the branded portal a partner's customer lands
 * in after signing in: authentication stays on the canonical host.
 *
 * The accent is applied to `<html>` for as long as this hook is mounted and
 * removed the moment it is not - unlike {@link useBranding} (effectively a
 * single, isolated page), this console is one a person can navigate away
 * from without a full reload (to Super Admin or a partner portal), and a
 * previous organization's colour must never bleed into either.
 */
export function useOrganizationBranding(slug: string): OwnBrand | null {
  const [brand, setBrand] = useState<OwnBrand | null>(null);
  useEffect(() => {
    let cancelled = false;
    brandingApi
      .own(slug)
      .then(({ brand: b }) => {
        if (cancelled) return;
        setBrand(b ? { ...b, portal: normalizePortal(b.portal) } : null);
        applyAccent(document.documentElement, b?.accent_color);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      applyAccent(document.documentElement, null);
    };
  }, [slug]);
  return brand;
}
