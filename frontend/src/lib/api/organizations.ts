import { apiFetch } from "./client";
import type { CreateOrganizationResult } from "./types";

/** Task 6: an idempotency key survives across a retry only if the caller
 * keeps it - generated once per mount/attempt series, not per request, so a
 * network-failure retry of the *same* submission reuses it deliberately. */
export function createOrganization(
  name: string,
  idempotencyKey: string,
): Promise<CreateOrganizationResult> {
  return apiFetch("/api/v1/organizations", {
    method: "POST",
    body: JSON.stringify({ name, idempotency_key: idempotencyKey }),
  });
}
