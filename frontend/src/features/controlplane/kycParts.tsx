"use client";

import type { KycStatus } from "@/lib/api/adminPanel";
import { Badge, EMPTY, sentence, type Tone } from "./kit";
import type { Load } from "./ui";
import { TextAreaField, TextField } from "./widgets";
import styles from "./AdminKyc.module.css";

/**
 * What the KYC review, the partner's own application and the setup queue share
 * (2026-11-09): the words for a KYC status, the experiences a partner can ask for, the
 * business details an application carries, and the form for a requirement an Admin enters.
 */

const KYC_STATUS: Record<KycStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  submitted: { label: "Submitted", tone: "info" },
  under_review: { label: "Under review", tone: "info" },
  correction_required: { label: "Correction required", tone: "warning" },
  approved: { label: "Approved", tone: "success" },
  rejected: { label: "Rejected", tone: "danger" },
};

export const kycLabel = (status: string | null | undefined): string =>
  status ? (KYC_STATUS[status as KycStatus]?.label ?? sentence(status)) : EMPTY;

/** A KYC status. Never a partner account's status: those are a separate system. */
export function KycBadge({ status, prefix }: { status: string; prefix?: string }) {
  const tone = KYC_STATUS[status as KycStatus]?.tone ?? "neutral";
  return (
    <Badge tone={tone} dot>
      {prefix}
      {kycLabel(status)}
    </Badge>
  );
}

export const EXPERIENCES = [
  { value: "affiliate", label: "Affiliate" },
  { value: "reseller", label: "Reseller" },
  { value: "white_label", label: "White-label" },
] as const;

export type ExperienceChoice = (typeof EXPERIENCES)[number]["value"];

export function experienceLabel(key: string | null | undefined): string {
  if (!key) return EMPTY;
  return EXPERIENCES.find((e) => e.value === key)?.label ?? sentence(key);
}

/** The business details, in the order a person fills them in. The lengths are the server's. */
export const BUSINESS_FIELDS = [
  { key: "legal_name", label: "Legal name", maxLength: 200, autoComplete: "organization" },
  { key: "business_type", label: "Business type", maxLength: 64, autoComplete: "off" },
  { key: "registration_number", label: "Registration number", maxLength: 64, autoComplete: "off" },
  { key: "tax_number", label: "Tax number", maxLength: 64, autoComplete: "off" },
  { key: "address_line", label: "Address", maxLength: 300, autoComplete: "street-address" },
  { key: "city", label: "City", maxLength: 100, autoComplete: "address-level2" },
  { key: "region", label: "State or region", maxLength: 100, autoComplete: "address-level1" },
  { key: "postal_code", label: "Postal code", maxLength: 16, autoComplete: "postal-code" },
  { key: "country", label: "Country", maxLength: 2, autoComplete: "country" },
  { key: "website", label: "Website", maxLength: 300, autoComplete: "url" },
  { key: "phone", label: "Phone", maxLength: 32, autoComplete: "tel" },
] as const;

/** "120 KB", "1.2 MB". */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The table's loading and error props for a list read with `useLoad`. */
export function tableState<T>(load: Load<T>, what: string) {
  return {
    loading: load.status === "loading",
    error: load.status === "forbidden" ? `You don't have access to ${what}.` : load.status === "error" ? load.message : null,
  };
}

// --- a requirement an Admin enters ---------------------------------------------------------

export interface RequirementDraft {
  label: string;
  help_text: string;
  is_required: boolean;
  is_active: boolean;
  display_order: string;
}

export const NEW_REQUIREMENT: RequirementDraft = { label: "", help_text: "", is_required: true, is_active: true, display_order: "0" };

export function requirementDraft(row: { label: string; help_text: string; is_required: boolean; is_active: boolean; display_order: number }): RequirementDraft {
  return {
    label: row.label,
    help_text: row.help_text,
    is_required: row.is_required,
    is_active: row.is_active,
    display_order: String(row.display_order),
  };
}

const orderIsValid = (raw: string) => /^\d{1,3}$/.test(raw.trim());

export const requirementIsValid = (draft: RequirementDraft) => draft.label.trim() !== "" && orderIsValid(draft.display_order);

export function requirementBody(draft: RequirementDraft) {
  return {
    label: draft.label.trim(),
    help_text: draft.help_text.trim(),
    is_required: draft.is_required,
    is_active: draft.is_active,
    display_order: Number(draft.display_order.trim()),
  };
}

export function RequirementFields({ draft, onChange }: { draft: RequirementDraft; onChange: (next: RequirementDraft) => void }) {
  return (
    <>
      <TextField label="Label" required maxLength={120} value={draft.label} onChange={(label) => onChange({ ...draft, label })} />
      <TextAreaField label="Help text (optional)" rows={3} maxLength={300} value={draft.help_text} onChange={(help_text) => onChange({ ...draft, help_text })} />
      <TextField
        label="Order"
        inputMode="numeric"
        maxLength={3}
        value={draft.display_order}
        onChange={(display_order) => onChange({ ...draft, display_order })}
        hint="Lower numbers are listed first."
        error={orderIsValid(draft.display_order) ? undefined : "Enter a whole number from 0 to 999."}
      />
      <label className={styles.check}>
        <input type="checkbox" checked={draft.is_required} onChange={(e) => onChange({ ...draft, is_required: e.target.checked })} />
        Required
      </label>
      <label className={styles.check}>
        <input type="checkbox" checked={draft.is_active} onChange={(e) => onChange({ ...draft, is_active: e.target.checked })} />
        Active
      </label>
    </>
  );
}
