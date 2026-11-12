import type { Metadata } from "next";
import { parseOrigin } from "./portals";

/**
 * The Digital Store's search metadata (ADR-065): its canonical address is
 * https://store.workeasy360.com (`W360_STORE_ORIGIN`, read on the server at request
 * time); until that host exists, the customer portal's. The marketplace itself is
 * the host's root. Pages that belong to one buyer (checkout, confirmation) are
 * never indexed.
 */
export function storeOrigin(env: Record<string, string | undefined> = process.env): string {
  return parseOrigin(env.W360_STORE_ORIGIN) ?? parseOrigin(env.W360_APP_ORIGIN) ?? "";
}

export function storeCanonical(path: string, env: Record<string, string | undefined> = process.env): string | undefined {
  const own = parseOrigin(env.W360_STORE_ORIGIN);
  const origin = storeOrigin(env);
  if (!origin) return undefined;
  if (path === "/store") return own ? `${own}/` : `${origin}/store`;
  return `${origin}${path}`;
}

export function storeMetadata({
  path,
  title,
  description,
  index = true,
}: {
  path: string;
  title: string;
  description: string;
  index?: boolean;
}): Metadata {
  const canonical = storeCanonical(path);
  return {
    title,
    description,
    alternates: canonical ? { canonical } : undefined,
    robots: index ? { index: true, follow: true } : { index: false, follow: false },
    openGraph: {
      title,
      description,
      siteName: "WorkEasy360 Store",
      type: "website",
      ...(canonical ? { url: canonical } : {}),
    },
  };
}
