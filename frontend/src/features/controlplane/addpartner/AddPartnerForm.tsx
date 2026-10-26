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
import styles from "../ControlPlane.module.css";
import mine from "./AddPartner.module.css";
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
 * Partners → Add Partner (ADR-048), shown as a dialog over All Partners
 * (2026-10-26, the owner's reference design): the short form that creates a partner's
 * account - its name, the administrator's email and the password Super Admin
 * sets, the partner type, and optionally the country. Nothing more: business
 * details, products, prices, commission, brand, domain, referral program and
 * team are set afterwards in the partner's **Complete Your Profile**, which this
 * page opens on success.
 *
 * What it will not do:
 *
 * - **Hold the password anywhere but React state.** No storage, no address, no
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
      <div className={mine.dialogForm}>
        <DialogHead titleId={titleId} onClose={onClose} />
        <div className={mine.dialogBody}>
          <p role="alert">You need the permission to manage partners to add one.</p>
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
    }, "Partner created.");
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
      className={mine.dialogForm}
      noValidate
      aria-busy={busy}
      onSubmit={(e) => {
        e.preventDefault();
        void submit(draft);
      }}
    >
      <DialogHead titleId={titleId} onClose={onClose} />
      <div className={mine.dialogBody}>
        <ErrorSummary errors={summary} focusRef={summaryRef} />
        {feedback}

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
          <div className={mine.existing} role="alert">
            <p>
              <strong>{EXISTING_ACCOUNT}</strong>
            </p>
            <p>
              Its password will not be changed. You can link that person as this partner&apos;s administrator - they keep signing in with
              the password they already have - or cancel and use another email.
            </p>
            <div className={styles.row}>
              <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={linkExisting}>
                Link Existing User as Partner Administrator
              </button>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={useAnother}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {draft.mode === "create" && !existing && (
          <>
            <PasswordField
              label="Password"
              value={draft.password}
              onChange={(v) => set("password", v)}
              placeholder="Enter password"
              describedBy={guidanceId}
              error={errors["administrator.password"]}
            />
            <PasswordField
              label="Confirm password"
              value={draft.confirm}
              onChange={(v) => set("confirm", v)}
              placeholder="Confirm password"
              error={errors["administrator.password_confirm"]}
            />
            <PasswordGuidance id={guidanceId} password={draft.password} confirm={draft.confirm} minLength={DEFAULT_MIN_PASSWORD} compact />
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

        {draft.mode === "create" && !existing && (
          <p className={mine.note}>Nothing is emailed: tell the administrator this password privately. They can change it after signing in.</p>
        )}
      </div>
      <div className={mine.dialogFoot}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
          Close
        </button>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          {busy ? "Adding…" : "Add Partner"}
        </button>
      </div>
    </form>
  );
}

/** The dialog's title and its close button (Escape closes it too). */
function DialogHead({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  return (
    <div className={mine.dialogHead}>
      <h2 id={titleId} className={mine.dialogTitle}>
        Add New Partner
      </h2>
      <button type="button" className={mine.closeButton} aria-label="Close" onClick={onClose}>
        <span aria-hidden="true">×</span>
      </button>
    </div>
  );
}
