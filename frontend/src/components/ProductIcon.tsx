/**
 * A product's visual (Phase 11): small, decorative (`aria-hidden`),
 * stroke-only SVG paths in `currentColor`, drawn from a server-chosen key -
 * the same convention as `sidebar/icons.tsx`'s `NavIcon`, kept separate
 * because these are content (what a product *is*), not navigation.
 *
 * Original line art, not a copy of any product's own artwork. The key set
 * matches the backend's allow-list (`catalog/domain.py PRODUCT_ICON_KEYS`);
 * an unknown or empty key falls back to `null`, and the caller draws a
 * monogram instead - a product can never make this render a URL.
 */

const PATHS: Record<string, string> = {
  customers: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm11 9v-1a3 3 0 0 0-2.2-2.9M15 4.1a3 3 0 0 1 0 5.8",
  book: "M4 5a2 2 0 0 1 2-2h6v16H6a2 2 0 0 0-2 2V5Zm16 0a2 2 0 0 0-2-2h-6v16h6a2 2 0 0 1 2 2V5Z",
  board: "M4 4h4v16H4V4Zm6 0h4v11h-4V4Zm6 0h4v14h-4V4Z",
  headset: "M4 13v3a2 2 0 0 0 2 2h1v-6H5a1 1 0 0 0-1 1Zm16 0v3a2 2 0 0 1-2 2h-1v-6h1a1 1 0 0 1 1 1ZM6 13a6 6 0 1 1 12 0M9 18v1a1 1 0 0 0 1 1h1",
  box: "M3 8l9-4 9 4-9 4-9-4Zm0 0v9l9 4 9-4V8M12 12v9M3 8l9 4 9-4",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  megaphone: "M3 10v4h3l6 4V6l-6 4H3Zm14-2a4 4 0 0 1 0 8m2-11a7 7 0 0 1 0 14",
  wallet: "M3 7h15a3 3 0 0 1 3 3v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm0 0a2 2 0 0 1 2-2h11M15 13h3",
  users: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm13 9v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8",
  cart: "M3 4h2l2.4 12.4a2 2 0 0 0 2 1.6h7.2a2 2 0 0 0 2-1.6L20 8H6M9 20a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm8 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
  cog: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.3 7.3 0 0 0-2-1.2L14.5 3h-4l-.4 2.6a7.3 7.3 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.3 7.3 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7.3 7.3 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z",
  calendar: "M4 5h16v16H4V5Zm0 5h16M8 3v4M16 3v4",
  shield: "M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6l-7-3Zm-3 9 2 2 4-4",
  generic: "M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z",
};

export function productIconKnown(key: string | null | undefined): boolean {
  return !!key && key in PATHS;
}

export function ProductIcon({ icon, size = 20 }: { icon?: string | null; size?: number }) {
  const d = icon ? PATHS[icon] : undefined;
  if (!d) return null;
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  );
}
