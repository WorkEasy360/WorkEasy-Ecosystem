/**
 * Where a partner's custom domain sends a visitor to sign in (Phase 12, D-035
 * decision B, ADR-039): the canonical sign-in page, naming this host only so
 * that page can present the partner's brand.
 *
 * Built from the deployment's canonical origin and the request's own host -
 * never from anything in the URL - so it cannot become an open redirect. A
 * plain module (not a client component) because the login page computes it on
 * the server.
 */
export function signInHandoffUrl(canonicalOrigin: string, currentHost: string): string {
  const host = currentHost.split(":")[0].toLowerCase();
  return `${canonicalOrigin.replace(/\/+$/, "")}/login?brand=${encodeURIComponent(host)}`;
}
