import { apiFetch } from "./client";
import type { MfaFactor, TotpEnrollmentStart } from "./types";

export function beginTotpEnrollment(): Promise<TotpEnrollmentStart> {
  return apiFetch("/api/v1/auth/mfa/totp/enroll", { method: "POST" });
}

export function confirmTotpEnrollment(
  factorId: string,
  code: string,
): Promise<{ recovery_codes: string[] }> {
  return apiFetch("/api/v1/auth/mfa/totp/confirm", {
    method: "POST",
    body: JSON.stringify({ factor_id: factorId, code }),
  });
}

export function getMfaFactors(): Promise<{ factors: MfaFactor[] }> {
  return apiFetch("/api/v1/auth/mfa/factors");
}

export function removeMfaFactor(factorId: string): Promise<{ ok: boolean }> {
  return apiFetch(`/api/v1/auth/mfa/factors/${factorId}`, { method: "DELETE" });
}

/** Raises the session to AAL2 (Task 37-39). A 400 `mfa_code_invalid` means
 * the code did not verify; the session's AAL is unchanged either way. */
export function stepUp(code: string): Promise<{ aal: number }> {
  return apiFetch("/api/v1/auth/mfa/step-up", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}
