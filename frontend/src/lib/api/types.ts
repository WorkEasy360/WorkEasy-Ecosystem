/** Wire types, matching the Django JSON shapes exactly (interfaces/api/v1/*).
 * One file so a backend field rename is one place to fix on this side. */

export interface SessionState {
  authenticated: boolean;
  user_id?: string;
  email?: string;
  name?: string;
}

export interface OrganizationSummary {
  organization_id: string;
  slug: string;
  display_name: string;
  status: string;
  membership_id: string;
  is_owner: boolean;
}

export interface OrganizationDetail {
  organization_id: string;
  slug: string;
  display_name: string;
  status: string;
  write_allowed: boolean;
}

/** The five closed states from platform.launcher.access.AppAccessState. */
export type AppAccessState =
  | "active"
  | "no_seat"
  | "no_entitlement"
  | "integration_suspended"
  | "product_unavailable";

export interface AppDirectoryEntry {
  product_code: string;
  display_name: string;
  description: string;
  licensing_mode: "per_seat" | "per_organization" | "usage";
  state: AppAccessState;
  can_launch: boolean;
}

export interface SeatMember {
  membership_id: string;
  email: string;
  held: boolean;
}

export interface SeatOverview {
  product_code: string;
  display_name: string;
  licensing_mode: string;
  seats_total: number;
  seats_assigned: number;
  members: SeatMember[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface MembershipSummary {
  membership_id: string;
  user_id: string;
  email: string;
  status: string;
  member_type: string;
  is_owner: boolean;
  joined_at: string | null;
}
