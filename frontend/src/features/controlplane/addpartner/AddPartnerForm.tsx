"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { partnerConsoleApi, type ReviewProblem } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "../ControlShell";
import { countryOptions } from "../countries";
import type { FieldErrors } from "../profileDraft";
import { useAction } from "../ui";
import { ErrorSummary, SelectField, TextField } from "../widgets";
import mine from "./AddPartner.module.css";
import { DialogHead as SharedHead, dialog } from "../Modal";
import { Button } from "../kit";
import { PasswordField, PasswordGuidance } from "./PasswordField";
import {
  DEFAULT_MIN_PASSWORD,
  EXISTING_ACCOUNT,
  EXPERIENCE_KEYS,
  SAME_NAME_PREFIX,
  TYPE_CARDS,
  emptyAddPartner,
  errorsFromProblems,
  experienceFromParam,
  messagesOf,
  toOnboardingBody,
  validate,
  type AddPartnerDraft,
} from "./model";

/**
 * Partners → New partner (ADR-048), shown as a dialog over All partners
 * (2026-10-26, the owner's reference design): the short form that creates a partner's
 * account - its business name, the **Partner Email** (the partner's own login, never
 * a Workeasy360 staff address; 2026-11-10), the partner type, and optionally the
 * country, a mobile number and a separate contact email. By default the partner is
 * invited: the email with a link to set their own password is queued at once, so
 * they can sign in and send their KYC while still a draft. Signing in activates
 * nothing. Setting a password here is the advanced option. Nothing more: business
 * details, products, prices, commission, brand, domain, referral program and
 * team are set afterwards in the partner's **Complete Your Profile**, which this
 * page opens on success.
 *
 * What it will not do:
 *
 * - **Hold a typed password anywhere but React state.** No storage, no address, no
 *   draft. Both inputs are `autocomplete="new-password"`, the password is sent in
 *   the one create request, and the inputs are emptied as soon as it is made.
 * - **Overwrite an existing account.** If the email already has a Workeasy360
 *   account the server says so, and the form offers to *link* that person
 *   (they keep their own password) or to cancel.
 * - **Decide anything.** The type is a preset the server turns into capabilities;
 *   the new partner is a draft until its profile is complete and staff activate it.
 */
export function AddPartnerForm({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const router = useRouter();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [draft, setDraft] = useState<AddPartnerDraft>(() =>
    emptyAddPartner(experienceFromParam(params.get("type") ?? params.get("purpose"))),
  );
  const [errors, setErrors] = useState<FieldErrors>({});
  const [summary, setSummary] = useState<string[]>([]);
  const [existing, setExisting] = useState(false);
  // The server found a live partner with this name: offer to confirm it is another one.
  const [sameName, setSameName] = useState<string | null>(null);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const guidanceId = useId();
  const { act, busy, feedback } = useAction();

  useEffect(() => {
    if (summary.length > 0) summaryRef.current?.focus();
  }, [summary]);

  if (!manage) {
    return (
      <div className={dialog.form}>
        <DialogHead titleId={titleId} onClose={onClose} />
        <div className={dialog.body}>
          <p role="alert">You need the permission to manage partners to create one.</p>
        </div>
      </div>
    );
  }

  const set = <K extends keyof AddPartnerDraft>(key: K, value: AddPartnerDraft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      const fields: Record<string, string[]> = {
        name: ["partner.display_name", "partner"],
        country: ["partner.country"],
        phone: ["partner.phone"],
        contactEmail: ["partner.contact_email"],
        separateContact: ["partner.contact_email"],
        email: ["administrator.email"],
        password: ["administrator.password"],
        confirm: ["administrator.password_confirm"],
        experience: ["experience"],
        access: ["administrator.password", "administrator.password_confirm"],
      };
      for (const f of fields[key as string] ?? []) delete next[f];
      return next;
    });
  };

  function show(found: FieldErrors) {
    setErrors(found);
    setSummary(messagesOf(found));
  }

  function clearSecrets() {
    setDraft((d) => ({ ...d, password: "", confirm: "" }));
  }

  async function submit(current: AddPartnerDraft) {
    const local = validate(current, DEFAULT_MIN_PASSWORD);
    if (Object.keys(local).length > 0) {
      show(local);
      return;
    }
    setSummary([]);
    const body = toOnboardingBody(current);
    await act(async () => {
      try {
        const created = await partnerConsoleApi.createWithLogin(body);
        // All Partners, with the new row marked and what really happened to the
        // invitation (queued, deferred or not sent - never "delivered").
        const invite = created.invitation ? `&invite=${created.invitation.state}&mail=${created.invitation.delivery_mode === "smtp" ? "smtp" : "local"}` : "";
        router.push(`/admin/partners/all?created=${created.id}${invite}`);
      } catch (err) {
        const problems = err instanceof ApiError && err.status === 400 ? (err.body as { problems?: ReviewProblem[] })?.problems : undefined;
        if (Array.isArray(problems)) {
          const found = errorsFromProblems(problems);
          const nameProblem = found["partner.display_name"];
          if (nameProblem?.startsWith(SAME_NAME_PREFIX) && !current.allowSameName) setSameName(nameProblem);
          if (found["administrator.email"] === EXISTING_ACCOUNT && current.mode === "create") {
            // The panel under the email is the message (and the alert); the
            // password problems of a password that will not be used are not shown.
            setExisting(true);
            setErrors({});
            return;
          }
          show(found);
          return;
        }
        throw err;
      } finally {
        clearSecrets();
      }
    }, "Partner created as a draft.");
  }

  function confirmSameName() {
    const next = { ...draft, allowSameName: true };
    setDraft(next);
    setSameName(null);
    void submit(next);
  }

  function linkExisting() {
    const next = { ...draft, mode: "link_existing" as const, password: "", confirm: "" };
    setDraft(next);
    setExisting(false);
    void submit(next);
  }

  function useAnother() {
    setExisting(false);
    setDraft((d) => ({ ...d, mode: "create", email: "" }));
  }

  const typeOptions = [
    { value: "", label: "Select…" },
    ...EXPERIENCE_KEYS.map((key) => ({ value: key, label: TYPE_CARDS[key].title })),
  ];

  return (
    <form method="post"
      className={dialog.form}
      noValidate
      aria-busy={busy}
      onSubmit={(e) => {
        e.preventDefault();
        void submit(draft);
      }}
    >
      <DialogHead titleId={titleId} onClose={onClose} />
      <div className={`${dialog.body} ${mine.scrollBody}`}>
        <ErrorSummary errors={summary} focusRef={summaryRef} />
        {feedback}

        {/* Two aligned columns, as every popup form (2026-11-05). The order follows
            the owner's brief of 2026-11-10: business name, Partner Email, type,
            country, mobile, then how the partner gets in. */}
        <div className={dialog.grid}>
          <TextField
            label="Partner / Business name"
            value={draft.name}
            onChange={(v) => {
              set("name", v);
              set("allowSameName", false);
              setSameName(null);
            }}
            maxLength={120}
            autoComplete="organization"
            placeholder="Enter the partner's business name"
            required
            showRequired
            error={errors["partner.display_name"] ?? errors.partner}
          />
          <TextField
            label="Partner Email"
            type="email"
            value={draft.email}
            onChange={(v) => {
              set("email", v);
              if (draft.mode === "link_existing") set("mode", "create");
              setExisting(false);
            }}
            maxLength={254}
            autoComplete="off"
            placeholder="Enter partner email address"
            hint="The partner's own sign-in email for the partner portal. Not a Workeasy360 staff email."
            required
            showRequired
            error={errors["administrator.email"]}
          />

          {sameName && (
            <div className={`${mine.existing} ${dialog.span}`}>
              <p>
                <strong>A partner with this name already exists.</strong>
              </p>
              <p>Open it from All Partners if it is the same business. If it is a different partner that happens to share the name, confirm it.</p>
              <div className={mine.existingActions}>
                <Button disabled={busy} onClick={confirmSameName}>
                  It is a different partner: create it
                </Button>
              </div>
            </div>
          )}

          {existing && (
            <div className={`${mine.existing} ${dialog.span}`} role="alert">
              <p>
                <strong>{EXISTING_ACCOUNT}</strong>
              </p>
              <p>
                Its password will not be changed. You can link that person as this partner&apos;s account administrator - they keep signing in
                with the password they already have - or cancel and use another email.
              </p>
              <div className={mine.existingActions}>
                <Button variant="primary" disabled={busy} onClick={linkExisting}>
                  Link Existing User as Partner Administrator
                </Button>
                <Button onClick={useAnother}>Use another email</Button>
              </div>
            </div>
          )}

          <SelectField
            label="Partner type"
            value={draft.experience ?? ""}
            onChange={(v) => set("experience", experienceFromParam(v))}
            options={typeOptions}
            required
            showRequired
            hint={draft.experience ? TYPE_CARDS[draft.experience].text : "What the partner applies to become. Nothing is granted until its KYC is approved and it is activated."}
            error={errors.experience}
          />

          {draft.experience && (
            <fieldset className={mine.alsoGroup}>
              <legend>Also allow (optional)</legend>
              <p className={mine.alsoHint}>
                {draft.experience === "white_label"
                  ? "White Label approval gives branding and a custom domain only. Tick what else this partner may do."
                  : "Any other permission this partner is explicitly authorised for."}
              </p>
              {EXPERIENCE_KEYS.filter((k) => k !== draft.experience).map((k) => (
                <label key={k} className={mine.alsoOption}>
                  <input
                    type="checkbox"
                    checked={draft.also.includes(k)}
                    onChange={(e) => set("also", e.target.checked ? [...draft.also, k] : draft.also.filter((x) => x !== k))}
                  />{" "}
                  {TYPE_CARDS[k].title}
                  {k === "reseller" ? " - sell to customers, Platform Wallet" : k === "affiliate" ? " - referral links and commission" : " - branding and domain"}
                </label>
              ))}
              {errors.also && (
                <p className="w360-error-text" role="alert">
                  {errors.also}
                </p>
              )}
            </fieldset>
          )}

          <SelectField
            label="Country"
            value={draft.country}
            onChange={(v) => set("country", v)}
            options={countryOptions("Select…")}
            hint="Optional. Sets the starting currency, which can be changed until the partner is activated."
            error={errors["partner.country"]}
          />

          <TextField
            label="Mobile number"
            type="tel"
            value={draft.phone}
            onChange={(v) => set("phone", v)}
            maxLength={32}
            autoComplete="off"
            inputMode="tel"
            placeholder="+91 98765 43210"
            hint="Optional."
            error={errors["partner.phone"]}
          />

          <div className={mine.contactToggle}>
            <label className={mine.check}>
              <input type="checkbox" checked={draft.separateContact} onChange={(e) => set("separateContact", e.target.checked)} />
              Use a different contact email
            </label>
            <p className={mine.muted}>Optional. By default the Partner Email is also the contact address.</p>
          </div>

          {draft.separateContact && (
            <TextField
              label="Contact email"
              type="email"
              value={draft.contactEmail}
              onChange={(v) => set("contactEmail", v)}
              maxLength={254}
              autoComplete="off"
              placeholder="Enter contact email address"
              hint="Where business emails go. Sign-in stays on the Partner Email."
              required
              showRequired
              error={errors["partner.contact_email"]}
            />
          )}

          {draft.mode === "create" && !existing && (
            <fieldset className={`${mine.access} ${dialog.span}`}>
              <legend>Invitation method: how the partner signs in</legend>
              <label className={mine.choice}>
                <input type="radio" name="access" checked={draft.access === "invite"} onChange={() => set("access", "invite")} />
                <span className={mine.choiceText}>
                  <strong>Send an invitation email (recommended)</strong>
                  <span>
                    The partner gets a link to set their own password, signs in to the partner portal and sends their KYC. Signing in does not
                    activate the partner.
                  </span>
                </span>
              </label>
              <label className={mine.choice}>
                <input type="radio" name="access" checked={draft.access === "password"} onChange={() => set("access", "password")} />
                <span className={mine.choiceText}>
                  <strong>Set a password now (advanced)</strong>
                  <span>You type the password and tell the partner yourself, privately. Nothing is emailed; they can change it after signing in.</span>
                </span>
              </label>
            </fieldset>
          )}

          {draft.mode === "create" && !existing && draft.access === "password" && (
            <>
              <div className={dialog.span}>
                <PasswordField
                  label="Password"
                  value={draft.password}
                  onChange={(v) => set("password", v)}
                  placeholder="Enter password"
                  describedBy={guidanceId}
                  error={errors["administrator.password"]}
                />
              </div>
              <div className={dialog.span}>
                <PasswordField
                  label="Confirm password"
                  value={draft.confirm}
                  onChange={(v) => set("confirm", v)}
                  placeholder="Confirm password"
                  error={errors["administrator.password_confirm"]}
                />
              </div>
              <div className={dialog.span}>
                <PasswordGuidance id={guidanceId} password={draft.password} confirm={draft.confirm} minLength={DEFAULT_MIN_PASSWORD} compact />
              </div>
              <label className={`${mine.check} ${dialog.span}`}>
                <input type="checkbox" checked={draft.forceChange} onChange={(e) => set("forceChange", e.target.checked)} />
                Ask them to choose their own password the first time they sign in (recommended)
              </label>
              <p className={`${mine.note} ${dialog.span}`}>
                Nothing is emailed: tell the partner this password privately, never by email. It is never shown again or stored readable.
              </p>
            </>
          )}

          <p className={`${mine.note} ${dialog.span}`}>
            The partner is created as a draft with no partner rights. It becomes active only after its KYC is approved and an Admin activates it.
          </p>
        </div>
      </div>
      <div className={`${dialog.foot} ${mine.foot}`}>
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" loading={busy}>
          {draft.mode === "create" && !existing && draft.access === "invite" ? "Create partner & send invitation" : "Create partner"}
        </Button>
      </div>
    </form>
  );
}

/** The dialog's title and its close button (Escape closes it too). */
function DialogHead({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  return <SharedHead titleId={titleId} title="New partner" onClose={onClose} />;
}
