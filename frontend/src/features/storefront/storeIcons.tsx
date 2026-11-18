/**
 * Small line icons for the Store (2026-11-03 marketplace redesign): one per
 * kind of category, picked from the category's own name, plus the few the
 * product page's checkout box uses. Decorative only - every icon is
 * `aria-hidden`; the text beside it carries the meaning.
 */

type IconName =
  | "megaphone"
  | "chart"
  | "gear"
  | "receipt"
  | "users"
  | "chat"
  | "headset"
  | "cap"
  | "building"
  | "sparkle"
  | "grid"
  | "layers"
  | "tag"
  | "star"
  | "plug"
  | "wallet"
  | "box"
  | "lock"
  | "bolt"
  | "shield"
  | "home"
  | "arrow"
  | "check"
  | "gift"
  | "rocket"
  | "refund"
  | "ticket";

const PATHS: Record<IconName, string> = {
  megaphone: "M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1Zm12-3a5 5 0 0 1 0 8m3-11a9 9 0 0 1 0 14",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.6 7.6 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.6 7.6 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z",
  receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2V3Zm3 5h6m-6 4h6m-6 4h4",
  users: "M16 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1m6.5-9a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM21 20v-1a4 4 0 0 0-3-3.9M15.5 4.1a3.5 3.5 0 0 1 0 6.8",
  chat: "M4 5h16v11H9l-5 4V5Z",
  headset: "M4 15v-3a8 8 0 0 1 16 0v3m-16 0a2 2 0 0 0 2 2h1v-6H6a2 2 0 0 0-2 2v2Zm16 0a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2v2Zm-3 2v1a3 3 0 0 1-3 3h-2",
  cap: "M2 9l10-5 10 5-10 5L2 9Zm4 2v5c0 1.5 2.7 3 6 3s6-1.5 6-3v-5m4-2v6",
  building: "M4 21V5l8-2v18m0-12h8v12M8 8h.01M8 12h.01M8 16h.01M16 13h.01M16 17h.01M2 21h20",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Zm7 12 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z",
  grid: "M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7Z",
  layers: "M12 3 2 8l10 5 10-5-10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5",
  tag: "M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9Zm5-4h.01",
  star: "M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9L12 3Z",
  plug: "M9 2v5m6-5v5M6 7h12v4a6 6 0 0 1-12 0V7Zm6 10v5",
  wallet: "M3 7a2 2 0 0 1 2-2h13v4M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2Zm13 7h.01",
  box: "M3 7l9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4m-9 4v10",
  lock: "M6 11h12v10H6V11Zm2 0V8a4 4 0 0 1 8 0v3",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7l1-8Z",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3Zm-3.5 9 2.5 2.5 4.5-5",
  home: "M3 11l9-8 9 8M5 9.5V21h5v-6h4v6h5V9.5",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  check: "M5 12.5 10 17l9-10",
  gift: "M4 11h16v10H4V11Zm-1-4h18v4H3V7Zm9 0v14M12 7c-1-3-5-4-5-1.5S10 7 12 7Zm0 0c1-3 5-4 5-1.5S14 7 12 7Z",
  rocket: "M5 15c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.1 2.1 0 0 0-2.9-.1ZM12 15l-3-3a22 22 0 0 1 2-4A13 13 0 0 1 22 2c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2Zm-3-3H4s.6-3 2-4c1.6-1.1 5 0 5 0m1 7v5s3-.6 4-2c1.1-1.6 0-5 0-5",
  refund: "M21 12a9 9 0 0 1-15.4 6.4M3 12A9 9 0 0 1 18.4 5.6M18 2v4h-4M6 22v-4h4",
  ticket: "M3 8a2 2 0 0 0 0 4v0a2 2 0 0 1 0 4v3h18v-3a2 2 0 0 1 0-4 2 2 0 0 0 0-4V5H3v3Zm11-3v14",
};

export function StoreIcon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The icon for a category, by the words in its name. */
const CATEGORY_WORDS: [RegExp, IconName][] = [
  [/market|seo|social|campaign|ads?\b/i, "megaphone"],
  [/sales|crm|lead/i, "chart"],
  [/account|financ|invoice|book|tax|billing|payment/i, "receipt"],
  [/\bhr\b|human|workforce|people|payroll|attendance|team/i, "users"],
  [/communicat|whatsapp|chat|message|sms|email|calling|voice/i, "chat"],
  [/help ?desk|support|ticket|service/i, "headset"],
  [/lms|learn|course|community|education|school/i, "cap"],
  [/industr|vertical|solution|retail|health|real estate/i, "building"],
  [/\bai\b|artificial|automation|bot|gpt/i, "sparkle"],
  [/operat|project|task|workflow|inventory|erp/i, "gear"],
];

export function categoryIcon(category: string): IconName {
  for (const [words, icon] of CATEGORY_WORDS) if (words.test(category)) return icon;
  return "grid";
}
