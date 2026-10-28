/**
 * Super Admin: the partner programme's commercial settings (ADR-053) - partner
 * margins (a discount off list price) and set-up fees. The browser sends a
 * scope and whole numbers (basis points, minor units); the server validates and
 * versions every change.
 */

import { apiFetch } from "./client";

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

export const adminProgrammeApi = {
  margins: (partnerId?: string) =>
    apiFetch<{ live: MarginRule[]; history: MarginRule[] }>(`${admin}/margins`, { query: { partner_id: partnerId } }),
  setMargin: (body: SetMarginBody) => apiFetch<MarginRule>(`${admin}/margins`, { method: "POST", body: json(body) }),
  endMargin: (code: string) => apiFetch<MarginRule>(`${admin}/margins/end`, { method: "POST", body: json({ code }) }),
  fees: () => apiFetch<ProgramFees>(`${admin}/partner-programme/fees`),
  /** `amount` is the decimal text typed ("25000.00"); the server converts it. */
  setFee: (body: { experience: string; amount: string; currency: string }) =>
    apiFetch<ProgramFees>(`${admin}/partner-programme/fees`, { method: "PUT", body: json(body) }),
};
