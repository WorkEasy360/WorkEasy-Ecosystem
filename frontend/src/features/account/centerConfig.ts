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

/** The Control Center's sections, in the order its navigation shows them. */
export const CENTER_SECTIONS = [
  { key: "overview", label: "Overview", href: "/account" },
  { key: "products", label: "My Products", href: "/account/products" },
  { key: "connected-logins", label: "Connected Logins", href: "/account/connected-logins" },
  { key: "sessions", label: "Active Sessions", href: "/account/sessions" },
  { key: "history", label: "Login History", href: "/account/history" },
  { key: "organizations", label: "Organizations", href: "/account/organizations" },
  { key: "security", label: "Security", href: "/account/security" },
  { key: "settings", label: "Account Settings", href: "/account/settings" },
] as const;

export type CenterSection = (typeof CENTER_SECTIONS)[number]["key"];
