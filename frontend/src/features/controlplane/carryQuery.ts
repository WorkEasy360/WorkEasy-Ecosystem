/** A page's search parameters as a query string to carry across a redirect
 * (an old address kept working). Only the keys and values are copied - the
 * destination is a fixed path, never taken from the request. */
export function carryQuery(params: Record<string, string | string[] | undefined>): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    for (const v of Array.isArray(value) ? value : [value]) out.append(key, v);
  }
  const text = out.toString();
  return text ? `?${text}` : "";
}
