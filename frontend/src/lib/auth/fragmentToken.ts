/**
 * Reads a one-time token from the URL fragment (`#token=...`) - which
 * browsers never send to a server, so it cannot reach an access log or a
 * `Referer` - and removes it from the address bar at once, so it is not left
 * in the history or on screen. Returns `null` when there is none.
 *
 * Shared by every emailed link: invitations, email verification and password
 * reset (Phase 9).
 */
export function takeFragmentToken(): string | null {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return null;
  const token = new URLSearchParams(hash).get("token");
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  return token && token.length <= 200 ? token : null;
}
