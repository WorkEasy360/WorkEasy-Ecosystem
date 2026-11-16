/**
 * The one-page Product Publisher's form (ADR-068): what the page holds, how it
 * is checked, and the request it becomes. Pure functions - the page component
 * renders and the server decides; these only shape what was typed.
 *
 * Money is typed as text and converted with `toMinor` (integers only); a
 * percentage with `toBasisPoints`. Nothing here applies a rate to an amount.
 */

import { toBasisPoints, toMajor, toMinor, formatBasisPoints } from "@/features/controlplane/amounts";
import type { PublisherBootstrap, PublisherPlanRow, PublisherPriceBook, PublisherState } from "@/lib/api/controlPlane";
import { looksLikeHttps, looksLikeVideo, slugFrom, SLUG_PATTERN } from "../model";

/** The billing periods a plan can be priced for (the server's `INTERVALS`). */
export const PERIODS = ["monthly", "quarterly", "semi_annual", "annual"] as const;
export type Period = (typeof PERIODS)[number];
export const PERIOD_NAME: Record<Period, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Every 6 months",
  annual: "Yearly",
};

export const LIMITS = {
  name: 128,
  tagline: 160,
  description: 4000,
  feature: 200,
  features: 12,
  planName: 128,
  planCaption: 160,
  planHighlights: 5000,
  videoTitle: 120,
  videoDescription: 500,
  storeTitle: 128,
  storeSummary: 200,
} as const;

export type PriceBook = PublisherPriceBook;
export type { PublisherBootstrap, PublisherPlanRow, PublisherState };

/** One plan card. Amounts are the text typed; `id` is null until saved. */
export interface PlanDraft {
  key: string;
  id: string | null;
  code: string;
  status: string;
  name: string;
  caption: string;
  /** One highlight per line - stored as the plan's description (a bullet list). */
  highlights: string;
  free: boolean;
  bookId: string;
  prices: Record<Period, string>;
  /** Which periods are offered. */
  offered: Record<Period, boolean>;
  perUser: boolean;
  setupFee: string;
  discount: string;
  seats: string;
  maxSeats: string;
  trialDays: string;
  visible: boolean;
  recommended: boolean;
  /** Has a published version (it is, or was, sold). */
  published: boolean;
  advancedOpen: boolean;
}

let planSeq = 0;
export function newPlanKey(): string {
  planSeq += 1;
  return `new-${Date.now().toString(36)}-${planSeq}`;
}

const emptyPrices = (): Record<Period, string> => ({ monthly: "", quarterly: "", semi_annual: "", annual: "" });
const noneOffered = (): Record<Period, boolean> => ({ monthly: false, quarterly: false, semi_annual: false, annual: false });

export function blankPlan(bookId: string, name = ""): PlanDraft {
  return {
    key: newPlanKey(),
    id: null,
    code: "",
    status: "draft",
    name,
    caption: "",
    highlights: "",
    free: false,
    bookId,
    prices: emptyPrices(),
    offered: { ...noneOffered(), monthly: true },
    perUser: false,
    setupFee: "",
    discount: "",
    seats: "1",
    maxSeats: "",
    trialDays: "0",
    visible: true,
    recommended: false,
    published: false,
    advancedOpen: false,
  };
}

/** `- 5 users\n- Automation` -> `5 users\nAutomation` (one per line, as typed). */
export function highlightsFromDescription(description: string): string {
  return description
    .split("\n")
    .map((line) => line.replace(/^\s*[-*]\s+/, "").trim())
    .filter(Boolean)
    .join("\n");
}

/** One highlight per line -> the bullet list the Store renders. */
export function descriptionFromHighlights(text: string): string {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => `- ${line}`)
    .join("\n");
}

export function planFromRow(row: PublisherPlanRow, books: PriceBook[]): PlanDraft {
  const book = books.find((b) => b.id === row.price_book_id) ?? books[0];
  const currency = row.currency ?? book?.currency ?? "INR";
  const prices = emptyPrices();
  const offered = noneOffered();
  for (const period of PERIODS) {
    const amount = row.prices[period];
    if (amount !== undefined && amount !== null) {
      offered[period] = true;
      prices[period] = amount === 0 ? "" : toMajor(amount, currency);
    }
  }
  if (!PERIODS.some((p) => offered[p])) offered.monthly = true;
  return {
    key: `plan-${row.id}`,
    id: row.id,
    code: row.code,
    status: row.status,
    name: row.name,
    caption: row.caption,
    highlights: highlightsFromDescription(row.description),
    free: row.free,
    bookId: book?.id ?? "",
    prices,
    offered,
    perUser: row.per_user,
    setupFee: row.setup_fee_minor ? toMajor(row.setup_fee_minor, currency) : "",
    discount: row.discount_bp ? formatBasisPoints(row.discount_bp).replace(/%$/, "") : "",
    seats: String(row.included_seats),
    maxSeats: row.max_seats === null ? "" : String(row.max_seats),
    trialDays: String(row.trial_days),
    visible: row.visible,
    recommended: row.recommended,
    published: row.published,
    advancedOpen: false,
  };
}

/** A screenshot on the page: stored (`mediaId`) or chosen and not yet uploaded (`file`). */
export interface ShotDraft {
  key: string;
  mediaId: string | null;
  file: File | null;
  /** A `data:` preview of a chosen file, or the stored image's address. */
  src: string | null;
  caption: string;
  /** The caption as stored, to send only a change. */
  savedCaption: string;
  progress: number | null;
}

/** An image the page holds in one slot (logo, cover, video thumbnail). */
export interface ImageSlot {
  mediaId: string | null;
  file: File | null;
  /** The stored one is to be removed on save. */
  removed: boolean;
  progress: number | null;
}

export const emptySlot = (): ImageSlot => ({ mediaId: null, file: null, removed: false, progress: null });

export interface PublisherDraft {
  productId: string | null;
  name: string;
  slug: string;
  slugTouched: boolean;
  /** Fixed once the product has been on sale (the server refuses a change). */
  slugLocked: boolean;
  category: string;
  tagline: string;
  applicationUrl: string;
  description: string;
  features: string[];
  videoMode: "none" | "link" | "upload";
  videoUrl: string;
  videoFile: File | null;
  videoStored: { id: string; content_type: string; size_bytes: number } | null;
  videoRemoved: boolean;
  videoProgress: number | null;
  videoTitle: string;
  videoDescription: string;
  logo: ImageSlot;
  banner: ImageSlot;
  poster: ImageSlot;
  shots: ShotDraft[];
  removedShots: string[];
  marketingUrl: string;
  documentationUrl: string;
  supportUrl: string;
  storeTitle: string;
  storeSummary: string;
  // Advanced settings (collapsed).
  launchUrl: string;
  ssoEnabled: boolean;
  redirectUri: string;
  healthUrl: string;
  /** The product already has an integration: launch and sign-in are edited on its Integration tab. */
  hasIntegration: boolean;
  channels: { customer_store_enabled: boolean; reseller_enabled: boolean; affiliate_enabled: boolean; white_label_enabled: boolean };
  trialEnabled: boolean;
  trialDays: string;
  plans: PlanDraft[];
}

export function blankDraft(bookId: string): PublisherDraft {
  return {
    productId: null,
    name: "",
    slug: "",
    slugTouched: false,
    slugLocked: false,
    category: "",
    tagline: "",
    applicationUrl: "",
    description: "",
    features: [""],
    videoMode: "none",
    videoUrl: "",
    videoFile: null,
    videoStored: null,
    videoRemoved: false,
    videoProgress: null,
    videoTitle: "",
    videoDescription: "",
    logo: emptySlot(),
    banner: emptySlot(),
    poster: emptySlot(),
    shots: [],
    removedShots: [],
    marketingUrl: "",
    documentationUrl: "",
    supportUrl: "",
    storeTitle: "",
    storeSummary: "",
    launchUrl: "",
    ssoEnabled: false,
    redirectUri: "",
    healthUrl: "",
    hasIntegration: false,
    // New products start safe: on the customer Store only once published;
    // partners sell or promote it only when staff switch that on.
    channels: { customer_store_enabled: true, reseller_enabled: false, affiliate_enabled: false, white_label_enabled: false },
    trialEnabled: false,
    trialDays: "14",
    plans: [blankPlan(bookId, "Starter")],
  };
}

/** The slug the page uses: typed in Advanced Settings, or the name's. */
export function effectiveSlug(draft: PublisherDraft): string {
  return draft.slugTouched || draft.slugLocked ? draft.slug : slugFrom(draft.name);
}

export type Problems = Record<string, string>;

function bookOf(books: PriceBook[], id: string): PriceBook | undefined {
  return books.find((b) => b.id === id);
}

/** What the page refuses before asking the server. `publish` adds what
 * publishing needs (the server checks again, with its own checklist). */
export function check(draft: PublisherDraft, books: PriceBook[], { publish, takenSlugs }: { publish: boolean; takenSlugs: Set<string> }): Problems {
  const out: Problems = {};
  const name = draft.name.trim();
  if (!name) out.name = "Enter the product name.";
  else if (name.length > LIMITS.name) out.name = `The name is at most ${LIMITS.name} characters.`;
  const slug = effectiveSlug(draft);
  if (!slug || !SLUG_PATTERN.test(slug)) out.slug = "Use lowercase letters, numbers and single hyphens, starting with a letter.";
  else if (!draft.productId && takenSlugs.has(slug)) out.slug = "Another product already uses this address. Change it in Advanced Settings.";
  if (!draft.tagline.trim()) {
    if (publish) out.tagline = "Enter a short description.";
  } else if (draft.tagline.length > LIMITS.tagline) out.tagline = `At most ${LIMITS.tagline} characters.`;
  if (publish && !draft.category) out.category = "Choose a category.";
  const url = draft.applicationUrl.trim();
  if (url && !looksLikeHttps(url)) out.applicationUrl = "Enter an https:// address.";
  if (publish && !url && !draft.launchUrl.trim() && !draft.hasIntegration) out.applicationUrl = "Enter the application URL customers open.";
  if (publish && !draft.logo.mediaId && !draft.logo.file) out.logo = "Add the product logo.";
  if (draft.logo.removed && !draft.logo.file && publish) out.logo = "Add the product logo.";
  if (draft.description.length > LIMITS.description) out.description = `At most ${LIMITS.description.toLocaleString()} characters.`;
  const features = draft.features.map((f) => f.trim()).filter(Boolean);
  if (features.length > LIMITS.features) out.features = `At most ${LIMITS.features} key features.`;
  draft.features.forEach((f, i) => {
    if (f.length > LIMITS.feature) out[`features.${i}`] = `At most ${LIMITS.feature} characters.`;
  });
  if (draft.videoMode === "link" && draft.videoUrl.trim() && !looksLikeVideo(draft.videoUrl.trim())) out.videoUrl = "Paste a YouTube or Vimeo link.";
  if (draft.videoTitle.length > LIMITS.videoTitle) out.videoTitle = `At most ${LIMITS.videoTitle} characters.`;
  if (draft.videoDescription.length > LIMITS.videoDescription) out.videoDescription = `At most ${LIMITS.videoDescription} characters.`;
  for (const [key, value] of [
    ["marketingUrl", draft.marketingUrl],
    ["documentationUrl", draft.documentationUrl],
    ["supportUrl", draft.supportUrl],
    ["launchUrl", draft.launchUrl],
    ["healthUrl", draft.healthUrl],
    ["redirectUri", draft.redirectUri],
  ] as const) {
    if (value.trim() && !looksLikeHttps(value.trim())) out[key] = "Enter an https:// address.";
  }
  if (draft.ssoEnabled && draft.launchUrl.trim() && !draft.redirectUri.trim() && !draft.hasIntegration) out.redirectUri = "With sign-in through WorkEasy360 on, enter the product's sign-in callback URL.";
  if (draft.trialEnabled) {
    const days = Number(draft.trialDays);
    if (!Number.isInteger(days) || days < 1 || days > 365) out.trialDays = "A trial lasts 1 to 365 days.";
  }
  if (draft.storeTitle.length > LIMITS.storeTitle) out.storeTitle = `At most ${LIMITS.storeTitle} characters.`;
  if (draft.storeSummary.length > LIMITS.storeSummary) out.storeSummary = `At most ${LIMITS.storeSummary} characters.`;

  let sellable = 0;
  draft.plans.forEach((plan, i) => {
    const at = (field: string) => `plans.${i}.${field}`;
    if (!plan.name.trim()) out[at("name")] = "Give the plan a name.";
    else if (plan.name.length > LIMITS.planName) out[at("name")] = `At most ${LIMITS.planName} characters.`;
    if (plan.caption.length > LIMITS.planCaption) out[at("caption")] = `At most ${LIMITS.planCaption} characters.`;
    if (plan.highlights.length > LIMITS.planHighlights) out[at("highlights")] = "Too long.";
    const book = bookOf(books, plan.bookId);
    const periods = PERIODS.filter((p) => plan.offered[p]);
    if (periods.length === 0 && !plan.free) out[at("prices")] = "Choose at least one billing period.";
    let priced = plan.free;
    if (!plan.free) {
      if (!book) out[at("bookId")] = "Choose the currency.";
      for (const period of periods) {
        const text = plan.prices[period].trim();
        if (!text) {
          if (publish) out[at(`prices.${period}`)] = "Enter the price.";
          continue;
        }
        const minor = book ? toMinor(text, book.currency) : null;
        if (minor === null) out[at(`prices.${period}`)] = `Enter an amount like 499${book && book.currency !== "JPY" ? " or 499.00" : ""}.`;
        else if (minor === 0) out[at(`prices.${period}`)] = "A paid plan costs more than zero - mark it Free instead.";
        else priced = true;
      }
      if (plan.setupFee.trim() && (!book || toMinor(plan.setupFee, book.currency) === null)) out[at("setupFee")] = "Enter an amount.";
      if (plan.discount.trim()) {
        const bp = toBasisPoints(plan.discount);
        if (bp === null || bp >= 10000) out[at("discount")] = "Enter a percentage below 100.";
      }
    } else if (!book) out[at("bookId")] = "Choose the currency.";
    const seats = Number(plan.seats);
    if (!Number.isInteger(seats) || seats < 1) out[at("seats")] = "At least 1.";
    if (plan.maxSeats.trim()) {
      const max = Number(plan.maxSeats);
      if (!Number.isInteger(max) || max < seats) out[at("maxSeats")] = "At least the seats included.";
    }
    const trial = Number(plan.trialDays || "0");
    if (!Number.isInteger(trial) || trial < 0 || trial > 365) out[at("trialDays")] = "0 to 365 days.";
    if (priced && plan.visible) sellable += 1;
  });
  if (draft.plans.filter((p) => p.recommended).length > 1) out.plans = "Only one plan can be Recommended.";
  if (publish && sellable === 0) out.plans = "Add at least one visible plan with a price (or a Free plan) to publish.";
  return out;
}

/** The request the page saves (POST/PUT `admin/products[/<id>]/publisher`). */
export function toRequest(draft: PublisherDraft, books: PriceBook[]): Record<string, unknown> {
  const features = draft.features.map((f) => f.trim()).filter(Boolean);
  const product: Record<string, unknown> = {
    name: draft.name.trim(),
    category: draft.category,
    tagline: draft.tagline.trim(),
    description: draft.description.trim(),
    application_url: draft.applicationUrl.trim(),
    marketing_url: draft.marketingUrl.trim(),
    documentation_url: draft.documentationUrl.trim(),
    support_url: draft.supportUrl.trim(),
    store_title: draft.storeTitle.trim(),
    store_summary: draft.storeSummary.trim(),
    video_url: draft.videoMode === "link" ? draft.videoUrl.trim() : "",
    video_title: draft.videoMode === "none" ? "" : draft.videoTitle.trim(),
    video_description: draft.videoMode === "none" ? "" : draft.videoDescription.trim(),
    highlights: features,
    ...draft.channels,
  };
  if (!draft.slugLocked) product.slug = effectiveSlug(draft);
  const body: Record<string, unknown> = { product, plans: draft.plans.map((plan) => planRequest(plan, books)) };
  const launch = draft.launchUrl.trim() || draft.applicationUrl.trim();
  if (!draft.productId && draft.launchUrl.trim()) {
    body.integration = {
      launch_url: launch,
      sso_enabled: draft.ssoEnabled,
      redirect_uri: draft.redirectUri.trim(),
      health_url: draft.healthUrl.trim(),
    };
  }
  body.trial = { enabled: draft.trialEnabled, duration_days: draft.trialEnabled ? Number(draft.trialDays) : 14 };
  return body;
}

function planRequest(plan: PlanDraft, books: PriceBook[]): Record<string, unknown> {
  const book = books.find((b) => b.id === plan.bookId);
  const currency = book?.currency ?? "INR";
  const prices: Record<string, number> = {};
  if (plan.free) {
    const first = PERIODS.find((p) => plan.offered[p]) ?? "monthly";
    prices[first] = 0;
  } else {
    for (const period of PERIODS) {
      if (!plan.offered[period]) continue;
      const minor = toMinor(plan.prices[period], currency);
      if (minor !== null && minor > 0) prices[period] = minor;
    }
  }
  const discount = plan.discount.trim() ? toBasisPoints(plan.discount) : null;
  return {
    id: plan.id,
    name: plan.name.trim(),
    caption: plan.caption.trim(),
    description: descriptionFromHighlights(plan.highlights),
    free: plan.free,
    price_book_id: book?.id ?? null,
    prices,
    per_user: plan.perUser,
    setup_fee_minor: plan.setupFee.trim() ? (toMinor(plan.setupFee, currency) ?? 0) : 0,
    discount_bp: discount ?? 0,
    included_seats: Number(plan.seats) || 1,
    max_seats: plan.maxSeats.trim() ? Number(plan.maxSeats) : null,
    trial_days: Number(plan.trialDays || "0") || 0,
    visible: plan.visible,
    recommended: plan.recommended,
  };
}

/** A server field (`plans.0.prices.monthly`, `video_url`) -> the page's key. */
export function fieldKey(field: string | undefined | null): string | null {
  if (!field) return null;
  const map: Record<string, string> = {
    application_url: "applicationUrl",
    video_url: "videoUrl",
    video_title: "videoTitle",
    video_description: "videoDescription",
    marketing_url: "marketingUrl",
    documentation_url: "documentationUrl",
    support_url: "supportUrl",
    store_title: "storeTitle",
    store_summary: "storeSummary",
    redirect_uri: "redirectUri",
    launch_url: "launchUrl",
    health_url: "healthUrl",
    highlights: "features",
    duration_days: "trialDays",
  };
  const plan = /^plans\.(\d+)\.?(.*)$/.exec(field);
  if (plan) {
    const rest = plan[2]
      .replace(/^price_book_id$/, "bookId")
      .replace(/^included_seats$/, "seats")
      .replace(/^max_seats$/, "maxSeats")
      .replace(/^trial_days$/, "trialDays")
      .replace(/^setup_fee_minor$/, "setupFee")
      .replace(/^discount_bp$/, "discount")
      .replace(/^description$/, "highlights");
    return rest ? `plans.${plan[1]}.${rest}` : "plans";
  }
  return map[field] ?? field;
}

/** A saved product as the page edits it (`GET admin/products/<id>/publisher`).
 * `mediaUrl` turns a stored image id into its staff address. */
export function fromState(state: PublisherState, mediaUrl: (mediaId: string) => string): PublisherDraft {
  const p = state.product;
  const books = state.books;
  const media = p.media ?? [];
  const one = (kind: string) => media.find((m) => m.kind === kind);
  const slot = (kind: string): ImageSlot => ({ ...emptySlot(), mediaId: one(kind)?.id ?? null });
  const listing = state.listing;
  const video = state.video;
  const plans = state.plans.map((row) => planFromRow(row, books));
  return {
    productId: p.id,
    name: p.name,
    slug: p.slug,
    slugTouched: true,
    slugLocked: Boolean(p.activated_at),
    category: p.category ?? "",
    tagline: p.tagline ?? "",
    applicationUrl: listing.application_url || p.application_url || "",
    description: listing.description ?? p.description ?? "",
    features: listing.highlights.length ? [...listing.highlights] : [""],
    videoMode: video ? "upload" : listing.video_url ? "link" : "none",
    videoUrl: listing.video_url ?? "",
    videoFile: null,
    videoStored: video,
    videoRemoved: false,
    videoProgress: null,
    videoTitle: listing.video_title ?? "",
    videoDescription: listing.video_description ?? "",
    logo: slot("logo"),
    banner: slot("banner"),
    poster: slot("video_poster"),
    shots: media
      .filter((m) => m.kind === "screenshot")
      .sort((a, b) => a.sort_order - b.sort_order)
      .map((m) => ({ key: `shot-${m.id}`, mediaId: m.id, file: null, src: mediaUrl(m.id), caption: m.caption, savedCaption: m.caption, progress: null })),
    removedShots: [],
    marketingUrl: listing.marketing_url ?? "",
    documentationUrl: listing.documentation_url ?? "",
    supportUrl: listing.support_url ?? "",
    storeTitle: listing.store_title ?? "",
    storeSummary: listing.store_summary ?? "",
    launchUrl: p.launch_url ?? "",
    ssoEnabled: p.integration?.sign_in_mode === "sso_only",
    redirectUri: "",
    healthUrl: "",
    hasIntegration: Boolean(p.integration),
    channels: {
      customer_store_enabled: p.customer_store_enabled,
      reseller_enabled: p.reseller_enabled,
      affiliate_enabled: p.affiliate_enabled,
      white_label_enabled: p.white_label_enabled,
    },
    trialEnabled: Boolean(p.channels?.trial?.enabled),
    trialDays: String(p.channels?.trial?.duration_days ?? 14),
    plans: plans.length ? plans : [blankPlan(books[0]?.id ?? "", "Starter")],
  };
}
