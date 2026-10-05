import { ApiError } from "@/lib/api/client";

// A TOTP code is accepted once only (RFC 6238 replay protection): the code
// that just confirmed enrollment or passed a step-up cannot be reused for the
// next one, so the "didn't verify" copy has to say "wait for a new code".
export const ERROR_COPY: Record<string, string> = {
  mfa_code_invalid:
    "That code didn't verify. Each code works only once - wait for your app to show a new code, check the time on your device, and try again.",
  mfa_enrollment_not_found: "That enrollment has expired. Start again.",
  step_up_required: "Verify a code from your authenticator app first.",
  rate_limited: "Too many attempts. Wait a moment and try again.",
};


export function errorMessage(err: unknown): string {
  if (err instanceof ApiError && ERROR_COPY[err.code]) return ERROR_COPY[err.code];
  return "Something went wrong. Please try again.";
}

export function isStepUpRequired(err: unknown): boolean {
  return err instanceof ApiError && err.code === "step_up_required";
}
