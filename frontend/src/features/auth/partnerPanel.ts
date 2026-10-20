/**
 * The separate Partner Panel (the reseller/agency admin app). When
 * `W360_PARTNER_PANEL_URL` is set (runtime, server-side), the sign-in
 * chooser's "Partner" option opens that app instead of this platform's own
 * partner sign-in. Unset, nothing changes.
 *
 * Only an absolute http(s) URL without credentials or a fragment is accepted;
 * anything else is ignored rather than becoming a link.
 */
export function readPartnerPanelUrl(env: Record<string, string | undefined>): string | undefined {
  const raw = (env.W360_PARTNER_PANEL_URL ?? "").trim();
  if (!raw) return undefined;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return undefined;
  if (url.username || url.password || url.hash) return undefined;
  return url.toString();
}
