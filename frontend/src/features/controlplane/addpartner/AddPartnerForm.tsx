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
 * account - its name, the administrator's email, how they get in, the partner
 * type, and optionally the country. By default (2026-11-07) the administrator is
 * invited: when the partner is activated they are emailed a link to set their
 * own password, so nobody has to share one. Setting a password here is the
 * advanced option. Nothing more: business
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
        // All Partners, with the new row marked: Complete setup and Guest login are in it.
        router.push(`/admin/partners/all?created=${created.id}`);
      } catch (err) {
        const problems = err instanceof ApiError && err.status === 400 ? (err.body as { problems?: ReviewProblem[] })?.problems : undefined;
        if (Array.isArray(problems)) {
          const found = errorsFromProblems(problems);
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
    }, current.access === "invite" && current.mode === "create" ? "Partner created. Its administrator is emailed a link to set a password when you activate it." : "Partner created.");
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
    <form
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

        {/* Two aligned columns, as every popup form (2026-11-05); the password,
            its guidance and the notes run across both. */}
        <div className={dialog.grid}>
          <TextField
            label="Partner name"
            value={draft.name}
            onChange={(v) => set("name", v)}
            maxLength={120}
            autoComplete="organization"
            placeholder="Enter partner name"
            required
            showRequired
            error={errors["partner.display_name"] ?? errors.partner}
          />
          <TextField
            label="Admin email"
            type="email"
            value={draft.email}
            onChange={(v) => {
              set("email", v);
              if (draft.mode === "link_existing") set("mode", "create");
              setExisting(false);
            }}
            maxLength={254}
            autoComplete="off"
            placeholder="Enter admin email"
            required
            showRequired
            error={errors["administrator.email"]}
          />

          {existing && (
            <div className={`${mine.existing} ${dialog.span}`} role="alert">
              <p>
                <strong>{EXISTING_ACCOUNT}</strong>
              </p>
              <p>
                Its password will not be changed. You can link that person as this partner&apos;s administrator - they keep signing in with
                the password they already have - or cancel and use another email.
              </p>
              <div className={mine.existingActions}>
                <Button variant="primary" disabled={busy} onClick={linkExisting}>
                  Link Existing User as Partner Administrator
                </Button>
                <Button onClick={useAnother}>Use another email</Button>
              </div>
            </div>
          )}

          {draft.mode === "create" && !existing && (
            <fieldset className={`${mine.access} ${dialog.span}`}>
              <legend>How the administrator signs in</legend>
              <label className={mine.choice}>
                <input type="radio" name="access" checked={draft.access === "invite"} onChange={() => set("access", "invite")} />
                <span className={mine.choiceText}>
                  <strong>Send an invitation email</strong>
                  <span>When you activate the partner, they get a link to set their own password.</span>
                </span>
              </label>
              <label className={mine.choice}>
                <input type="radio" name="access" checked={draft.access === "password"} onChange={() => set("access", "password")} />
                <span className={mine.choiceText}>
                  <strong>Set a password now (advanced)</strong>
                  <span>You type the password and tell the administrator yourself. Nothing is emailed.</span>
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
            </>
          )}

          <SelectField
            label="Partner type"
            value={draft.experience ?? ""}
            onChange={(v) => set("experience", experienceFromParam(v))}
            options={typeOptions}
            required
            showRequired
            hint={draft.experience ? TYPE_CARDS[draft.experience].text : "Sets the partner's first capabilities and the workspace it opens."}
            error={errors.experience}
          />

          <SelectField
            label="Country"
            value={draft.country}
            onChange={(v) => set("country", v)}
            options={countryOptions("Select…")}
            hint="Optional. Sets the starting currency, which can be changed until the partner is activated."
            error={errors["partner.country"]}
          />

          {draft.mode === "create" && !existing && draft.access === "password" && (
            <p className={`${mine.note} ${dialog.span}`}>Nothing is emailed: tell the administrator this password privately. They can change it after signing in.</p>
          )}
        </div>
      </div>
      <div className={`${dialog.foot} ${mine.foot}`}>
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" loading={busy}>
          Create partner
        </Button>
      </div>
    </form>
  );
}

/** The dialog's title and its close button (Escape closes it too). */
function DialogHead({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  return <SharedHead titleId={titleId} title="New partner" onClose={onClose} />;
}
