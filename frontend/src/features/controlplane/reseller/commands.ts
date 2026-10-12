import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi, type CapabilityCatalog, type ProfileInput, type ResellerDraftBody } from "@/lib/api/partnerConsole";
import { formatBasisPoints } from "../amounts";
import { money } from "../ui";
import type { StepDef } from "../widgets";
import { WHITE_LABEL_KEYS } from "./draft";

/** What the first commands hand to the later ones: the new partner's id and the
 * version the next command that needs one must quote. */
export interface Created {
  id?: string;
  version?: number;
}

export interface CommandPlan {
  /** A snapshot of what was reviewed: a retry after a verification prompt runs exactly this. */
  body: ResellerDraftBody;
  catalog: CapabilityCatalog | null;
  /** Names for the progress labels only: ``{product code: name}``. */
  productNames: Record<string, string>;
  /** ``{plan version id: "Product - Plan"}``, for the progress labels only. */
  planNames: Record<string, string>;
  created: { current: Created };
  reason: string;
  /** ``true`` for Activate reseller; Save draft stops short of it. */
  activate: boolean;
}

/** The capability keys to grant: the ones chosen, plus the white-label pair when
 * white-label was asked for. The same partner gains both - never a second account. */
export function grantedCapabilities(body: ResellerDraftBody): string[] {
  const keys = [...body.capabilities];
  if (body.white_label) {
    for (const key of WHITE_LABEL_KEYS) if (!keys.includes(key)) keys.push(key);
  }
  return keys;
}

/**
 * The resumable command list, in the order the design fixes: create the partner,
 * save its profile, grant capabilities, grant products, set partner prices,
 * assign commission, name the administrator, and (only for Activate) activate.
 * Whatever is empty is skipped. Each key is stable, so the step runner never
 * repeats a command that finished - after a second-factor prompt or a failure.
 *
 * Every command sends a selection or a typed integer; none sends a computed
 * figure, an actor or a status stamp.
 */
export function buildCommands(plan: CommandPlan): StepDef[] {
  const { body, catalog, created } = plan;
  const id = () => created.current.id as string;
  const currency = body.partner.payout_currency;
  const profile: ProfileInput = body.profile ?? {};

  const defs: StepDef[] = [
    {
      key: "partner",
      label: `Create the partner “${body.partner.display_name}” (as a draft)`,
      run: async () => {
        const row = await adminApi.createPartner({
          code: body.partner.code,
          display_name: body.partner.display_name,
          payout_currency: currency,
          legal_name: body.partner.legal_name || undefined,
          contact_name: body.partner.contact_name || undefined,
          contact_email: body.partner.contact_email || undefined,
        });
        created.current = { id: row.id, version: row.version };
      },
    },
    {
      key: "profile",
      label: "Save the business details, contacts, support and payout preferences",
      run: async () => {
        const saved = await partnerConsoleApi.saveProfile(id(), created.current.version ?? 0, profile);
        // Saving the profile changes the partner, so every later command that
        // needs a version quotes the one it returned.
        created.current = { ...created.current, version: saved.version };
      },
    },
  ];

  for (const key of grantedCapabilities(body)) {
    const info = catalog?.results.find((c) => c.key === key);
    defs.push({
      key: `capability:${key}`,
      label: `Grant “${info?.short_label || info?.label || key}”`,
      run: async () => {
        await adminApi.grantCapability(id(), key, plan.reason);
      },
    });
  }

  for (const code of body.products) {
    const options = body.product_options?.[code] ?? {};
    const trial = options.trial_allowed !== false;
    defs.push({
      key: `product:${code}`,
      label: `Allow selling ${plan.productNames[code] ?? code} (free trial ${trial ? "allowed" : "not allowed"})`,
      run: async () => {
        await adminApi.grantProduct(id(), code, undefined, {
          trial_allowed: options.trial_allowed ?? true,
          portal_visible: options.portal_visible ?? true,
        });
      },
    });
  }

  for (const price of body.partner_prices) {
    defs.push({
      key: `price:${price.plan_version_id}:${price.interval}`,
      label: `Set a partner price of ${money(price.amount_minor, currency)} for ${
        plan.planNames[price.plan_version_id] ?? "the plan"
      } (${price.interval})`,
      run: async () => {
        await partnerConsoleApi.setPartnerPrice(id(), price);
      },
    });
  }

  const commission = body.commission;
  if (commission.mode === "custom" && commission.rate_bp !== undefined) {
    const rate = commission.rate_bp;
    const scope = commission.product_codes ?? [];
    for (const code of scope.length > 0 ? scope : [null]) {
      defs.push({
        key: `commission:${code ?? "all"}`,
        label: `Assign a commission rate of ${formatBasisPoints(rate)} on ${
          code === null ? "all products" : (plan.productNames[code] ?? code)
        }`,
        run: async () => {
          await partnerConsoleApi.assignCommission(id(), {
            rate_bp: rate,
            reason: plan.reason,
            expected_version: 0,
            ...(code === null ? {} : { product_code: code }),
            ...(commission.effective_from ? { effective_from: commission.effective_from } : {}),
          });
        },
      });
    }
  }

  const admin = body.administrator;
  if (admin && admin.email) {
    defs.push({
      key: "administrator",
      label: `Name ${admin.email} as ${admin.role === "admin" ? "administrator" : "viewer"} (invitation ${
        admin.send_invitation === "now" ? "sent now" : "sent when the partner is activated"
      })`,
      run: async () => {
        await partnerConsoleApi.inviteAdministrator(id(), admin.email, admin.role, {
          first_name: admin.first_name,
          last_name: admin.last_name,
          send_invitation: admin.send_invitation,
        });
      },
    });
  }

  if (plan.activate) {
    defs.push({
      key: "activate",
      label: "Activate the partner",
      run: async () => {
        await adminApi.setPartnerStatus(id(), "active", plan.reason, created.current.version ?? 0);
      },
    });
  }
  return defs;
}
