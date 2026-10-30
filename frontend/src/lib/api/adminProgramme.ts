/**
 * Super Admin: the partner programme's commercial settings (ADR-053) - partner
 * margins (a discount off list price) and set-up fees. The browser sends a
 * scope and whole numbers (basis points, minor units); the server validates and
 * versions every change.
 */

import { apiFetch } from "./client";
import type { PromotionRequest, ReferralTrail } from "./partnerPanel";

const admin = "/api/v1/admin";
const json = (body: unknown): string => JSON.stringify(body);

export interface MarginRule {
  id: string;
  code: string;
  version: number;
  experience: "reseller" | "white_label" | null;
  partner_id: string | null;
  product_code: string;
  discount_bp: number;
  recurring: boolean;
  effective_from: string;
  retired_at: string | null;
  reason: string;
}

export interface SetMarginBody {
  discount_bp: number;
  recurring?: boolean;
  product_code?: string;
  experience?: string;
  partner_id?: string;
  reason?: string;
}

export interface ProgramFees {
  fees: { reseller: { amount_minor: number; currency: string } | null; white_label: { amount_minor: number; currency: string } | null };
}

// --- ADR-054: the affiliate programme ------------------------------------------------

export interface AffiliatePolicy {
  attribution_days: number;
  revoked_links: "stop" | "continue";
  updated_at: string | null;
  attribution_rule: string;
  max_attribution_days: number;
}

export interface PromotionGrant {
  id: string;
  product_code: string;
  product_name: string;
  status: "active" | "revoked";
  granted_at: string;
  revoked_at: string | null;
}

// --- ADR-055: affiliate product requests ------------------------------------------------

export type PromotionRequestStatus = PromotionRequest["status"];

export interface AdminPromotionRequest extends PromotionRequest {
  partner_id: string;
  requested_by: string;
  decided_by: string | null;
  revoked_by: string | null;
  grant_id: string | null;
}

export interface PromotionRequestQueue {
  items: (AdminPromotionRequest & { partner_name: string; partner_code: string; partner_status: string })[];
  counts: Record<PromotionRequestStatus, number>;
  total: number;
  limit: number;
  offset: number;
  channels: Record<string, string>;
}

export interface PromotionRequestDetail {
  request: AdminPromotionRequest;
  partner: {
    id: string;
    name: string;
    code: string;
    status: string;
    website: string;
    referral_capability: boolean;
    promotes: string[];
  };
  product: { code: string; name: string; status: string; eligible: boolean };
  history: AdminPromotionRequest[];
  grants: { id: string; status: "active" | "revoked"; granted_at: string; granted_by: string; revoked_at: string | null; revoked_by: string | null }[];
}

export interface AdminProductUpdate {
  id: string;
  kind: "feature" | "release";
  slug: string;
  title: string;
  summary: string;
  body: string;
  banner_id: string | null;
  banner_url: string | null;
  landing_url: string;
  status: "draft" | "published" | "archived";
  page_path: string;
  published_at: string | null;
  archived_at: string | null;
}

export interface ProductUpdateBody {
  kind?: "feature" | "release";
  slug?: string;
  title?: string;
  summary?: string;
  body?: string;
  banner_id?: string | null;
  landing_url?: string;
}

export const adminProgrammeApi = {
  margins: (partnerId?: string) =>
    apiFetch<{ live: MarginRule[]; history: MarginRule[] }>(`${admin}/margins`, { query: { partner_id: partnerId } }),
  setMargin: (body: SetMarginBody) => apiFetch<MarginRule>(`${admin}/margins`, { method: "POST", body: json(body) }),
  endMargin: (code: string) => apiFetch<MarginRule>(`${admin}/margins/end`, { method: "POST", body: json({ code }) }),
  fees: () => apiFetch<ProgramFees>(`${admin}/partner-programme/fees`),
  /** `amount` is the decimal text typed ("25000.00"); the server converts it. */
  setFee: (body: { experience: string; amount: string; currency: string }) =>
    apiFetch<ProgramFees>(`${admin}/partner-programme/fees`, { method: "PUT", body: json(body) }),
  affiliatePolicy: () => apiFetch<AffiliatePolicy>(`${admin}/affiliate-policy`),
  setAffiliatePolicy: (body: { attribution_days: number; revoked_links: string }) =>
    apiFetch<AffiliatePolicy>(`${admin}/affiliate-policy`, { method: "PUT", body: json(body) }),
  promotions: (partnerId: string) => apiFetch<{ grants: PromotionGrant[] }>(`${admin}/partners/${partnerId}/promotions`),
  grantPromotion: (partnerId: string, productCode: string) =>
    apiFetch<{ id: string }>(`${admin}/partners/${partnerId}/promotions`, {
      method: "POST",
      body: json({ product_code: productCode }),
    }),
  revokePromotion: (partnerId: string, grantId: string) =>
    apiFetch<{ id: string }>(`${admin}/partners/${partnerId}/promotions/${grantId}/revoke`, { method: "POST", body: json({}) }),
  promotionRequests: (query: { status?: string; q?: string; partner_id?: string; offset?: number }) =>
    apiFetch<PromotionRequestQueue>(`${admin}/partner-product-requests`, { query }),
  promotionRequest: (requestId: string) => apiFetch<PromotionRequestDetail>(`${admin}/partner-product-requests/${requestId}`),
  /** Approve, reject (a reason the partner reads is required) or revoke. */
  decidePromotionRequest: (requestId: string, action: "approve" | "reject" | "revoke", reason?: string) =>
    apiFetch<AdminPromotionRequest>(`${admin}/partner-product-requests/${requestId}/${action}`, {
      method: "POST",
      body: json(reason ? { reason } : {}),
    }),
  /** How one of a partner's referrals became earnings; never names the customer. */
  referralTrail: (partnerId: string, referralId: string) =>
    apiFetch<ReferralTrail>(`${admin}/partners/${partnerId}/referrals/${referralId}/trail`),
  productUpdates: (productId: string) =>
    apiFetch<{ updates: AdminProductUpdate[]; approved_hosts: string[]; images: { id: string; kind: string; caption: string }[] }>(
      `${admin}/products/${productId}/updates`,
    ),
  createProductUpdate: (productId: string, body: ProductUpdateBody) =>
    apiFetch<{ id: string }>(`${admin}/products/${productId}/updates`, { method: "POST", body: json(body) }),
  editProductUpdate: (productId: string, updateId: string, body: ProductUpdateBody) =>
    apiFetch<{ id: string }>(`${admin}/products/${productId}/updates/${updateId}`, { method: "PATCH", body: json(body) }),
  productUpdateAction: (productId: string, updateId: string, action: "publish" | "archive") =>
    apiFetch<{ id: string; status: string }>(`${admin}/products/${productId}/updates/${updateId}/${action}`, {
      method: "POST",
      body: json({}),
    }),
};
