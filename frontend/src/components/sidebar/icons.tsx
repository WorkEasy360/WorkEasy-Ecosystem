/**
 * The navigation's icons: small, decorative (`aria-hidden`), stroke-only SVG
 * paths drawn in `currentColor` so they follow the text colour in every theme
 * and in forced-colours mode. Never the only signal - every item has its label.
 */

const PATHS: Record<string, string> = {
  dashboard: "M4 13h6V4H4v9Zm10 7h6V11h-6v9ZM4 20h6v-4H4v4Zm10-13h6V4h-6v3Z",
  building: "M4 21V5l8-3v19M12 21h8V9l-8-3M8 8h.01M8 12h.01M8 16h.01M16 12h.01M16 16h.01",
  apps: "M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z",
  users: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm13 9v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8",
  shield: "M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6l-7-3Zm-3 9 2 2 4-4",
  audit: "M9 4h6l1 2h3v14H5V6h3l1-2Zm0 8h6M9 16h4",
  card: "M3 7h18v10H3V7Zm0 4h18M7 15h3",
  puzzle: "M10 4a2 2 0 1 1 4 0v2h4v4h-2a2 2 0 1 0 0 4h2v4h-4v-2a2 2 0 1 0-4 0v2H6v-4h2a2 2 0 1 0 0-4H6V6h4V4Z",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7l1-8Z",
  sparkles: "M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8L12 3Zm7 11 .9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9L19 14Z",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.3 7.3 0 0 0-2-1.2L14.5 3h-4l-.4 2.6a7.3 7.3 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.3 7.3 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7.3 7.3 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z",
  tag: "M3 12V4h8l10 10-8 8L3 12Zm5-4h.01",
  currency: "M12 3v18M17 7H9.5a3 3 0 0 0 0 6h5a3 3 0 0 1 0 6H6",
  mail: "M3 6h18v12H3V6Zm0 0 9 7 9-7",
  palette: "M12 3a9 9 0 1 0 0 18c1 0 1.5-.7 1.5-1.5 0-.9-.7-1.4-.7-2.3 0-1 .8-1.7 1.7-1.7H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5ZM7.5 11h.01M10 7h.01M15 7h.01",
  key: "M15 7a4 4 0 1 1-3.9 5H9v2H7v2H4v-3l5.1-5.1A4 4 0 0 1 15 7Zm1 1h.01",
  network: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm-9 9h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z",
  receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2V3Zm3 5h6M9 12h6M9 16h3",
  plus: "M12 5v14M5 12h14",
  handshake: "M3 12 7 8l4 2 3-3 7 5-5 5-3-2-2 2-4-3-4-2Zm8-2 3 3",
  gift: "M4 11h16v10H4V11Zm-1-4h18v4H3V7Zm9 0v14M12 7c-1.5-3-5-3-5-1s3 1 5 1Zm0 0c1.5-3 5-3 5-1s-3 1-5 1Z",
  store: "M4 9l1.5-5h13L20 9M4 9h16M4 9v11h16V9M9 20v-6h6v6",
  lock: "M6 11h12v10H6V11Zm2 0V7a4 4 0 1 1 8 0v4",
  device: "M5 4h14v11H5V4Zm-2 15h18M9 19l1-4h4l1 4",
  history: "M3 12a9 9 0 1 0 3-6.7M3 4v4h4M12 7v5l3 3",
  diagnostics: "M3 12h4l3-8 4 16 3-8h4",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2Zm4 4a2 2 0 0 0 4 0",
  help: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 13h.01M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5",
  login: "M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l4-4-4-4M14 12H3",
  copy: "M9 9h11v11H9V9Zm-5 6V4h11",
  chevronDown: "m6 9 6 6 6-6",
  chevronRight: "m9 6 6 6-6 6",
  chevronLeft: "m15 6-6 6 6 6",
  chevronUp: "m6 15 6-6 6 6",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm9 3-4.3-4.3",
  x: "M6 6l12 12M18 6 6 18",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  check: "m5 12 5 5 9-10",
  checkCircle: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm-4 9 3 3 5-6",
  xCircle: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm-3 6 6 6m0-6-6 6",
  circle: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 8v5m0-8h.01",
  alert: "M12 4 2.5 20h19L12 4Zm0 6v4m0 3h.01",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  eyeOff: "M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3 3.9M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2",
  download: "M12 4v11m-5-5 5 5 5-5M4 20h16",
  upload: "M12 20V9m-5 5 5-5 5 5M4 4h16",
  columns: "M4 4h16v16H4V4Zm6 0v16m6-16v16",
  sort: "m8 9 4-4 4 4M8 15l4 4 4-4",
  arrowUp: "M12 19V5m-6 6 6-6 6 6",
  arrowDown: "M12 5v14m-6-6 6 6 6-6",
  calendar: "M4 6h16v14H4V6Zm0 4h16M8 3v4m8-4v4",
  user: "M20 21v-1a5 5 0 0 0-5-5H9a5 5 0 0 0-5 5v1M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3",
  edit: "M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4",
  refresh: "M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4m-4 5a8 8 0 0 0 14.6 4.5L20 16m0 4v-4h-4",
  play: "M7 4v16l13-8L7 4Z",
  send: "m4 12 16-8-6 16-2-7-8-1Z",
  ban: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM5.6 5.6l12.8 12.8",
  pause: "M8 5v14m8-14v14",
  logout: "M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 16l4-4-4-4M18 12H9",
  book: "M4 5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2V5Zm0 14a2 2 0 0 1 2-2h14",
  menu: "M4 6h16M4 12h16M4 18h16",
  sidebar: "M4 4h16v16H4V4Zm5 0v16",
  filter: "M4 5h16l-6 8v6l-4-2v-4L4 5Z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm-9 9h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z",
  ticket: "M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4V8Zm11-2v12",
  wallet: "M4 7h15v12H4a1 1 0 0 1-1-1V6a2 2 0 0 1 2-2h12v3M16 13h.01",
  box: "M12 3 3 7.5v9L12 21l9-4.5v-9L12 3Zm0 0v18M3 7.5l9 4.5 9-4.5",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4v5l3 2",
  cart: "M3 4h2l2.4 11h11L21 7H6.2M9 20h.01M18 20h.01",
};

/** Any icon above at a chosen size (16 inside buttons and inputs, 18 in the
 * navigation, 20 on cards). Decorative: the control carries the name. */
export function Icon({ name, size = 16, className }: { name: string; size?: number; className?: string }) {
  const d = PATHS[name];
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
      className={className}
    >
      <path d={d} />
    </svg>
  );
}

export function NavIcon({ name }: { name?: string }) {
  const d = name ? PATHS[name] : undefined;
  if (!d) return null;
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="18"
      height="18"
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
