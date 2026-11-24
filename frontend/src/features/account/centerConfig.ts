import { readPortalOrigins } from "@/lib/portals";

/**
 * ADR-072: the Accounts Control Center exists only while WorkEasy360 Accounts hosts
 * identity (`W360_ACCOUNTS_IDENTITY`, ADR-071). Read on the server, per request; off,
 * `/account` is the ADR-071 account page exactly and the Control Center's own pages
 * are not found.
 */
export interface CenterOrigins {
  /** User Admin, where organizations, members, seats and billing are managed. */
  appOrigin: string;
}

export function centerOrigins(env: Record<string, string | undefined> = process.env): CenterOrigins | null {
  const origins = readPortalOrigins(env);
  if (!origins?.accountsIdentity) return null;
  return { appOrigin: origins.app };
}

/** The Control Center's sections, in the order its navigation shows them - exactly
 * four (Accounts V3, the note's section 04). Organizations, subscriptions and billing
 * are User Admin's and are linked from Settings; products' sign-ins are part of
 * Devices & Sessions. My Profile opens first. */
export const CENTER_SECTIONS = [
  { key: "profile", label: "My Profile", href: "/account" },
  { key: "security", label: "Login & Security", href: "/account/security" },
  { key: "sessions", label: "Devices & Sessions", href: "/account/sessions" },
  { key: "settings", label: "Settings", href: "/account/settings" },
] as const;

export type CenterSection = (typeof CENTER_SECTIONS)[number]["key"];

/** Accounts V3: where each page of the earlier Control Center (ADR-072) lives now. */
export const MOVED_SECTIONS: Record<string, string> = {
  "/account/products": "/account/settings",
  "/account/organizations": "/account/settings",
  "/account/connected-logins": "/account/sessions",
  "/account/history": "/account/sessions",
  "/account/sign-in-preferences": "/account/security",
};

/** The account-security help page, signed in or not (Accounts V3, the note's 10.3). */
export const ACCOUNT_HELP_HREF = "/help/account";
