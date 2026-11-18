"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { partnerConsoleApi, type ContactKind, type PartnerContactView, type PartnerProfile, type ProfileInput } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { countryName } from "./countries";
import { DialogHead, Modal, dialog } from "./Modal";
import { Section } from "./PartnerSection";
import { BusinessDetailsForm, ContactsForm, PayoutPrefsForm, SupportForm } from "./ProfileForms";
import { CONTACT_KINDS, errorsFromProblems, localProblems, profileDraftFrom, toProfileInput, type FieldErrors, type ProfileDraft, type ProfileParts } from "./profileDraft";
import { type TabKey, type TabProps, TAB_LABELS } from "./partnerTabs";
import { LoadGate, describeError, useAction, useLoad } from "./ui";
import { ErrorSummary } from "./widgets";
import styles from "./ControlPlane.module.css";
import ps from "./PartnerProfileTabs.module.css";

/**
 * A partner's Business Details and Contacts tabs.
 *
 * Both edit the one stored profile through the one endpoint
 * (`PUT /admin/partners/<id>/profile`), and each sends **only its own parts**: a
 * save on Business Details carries business, support, payout and notes; a save on
 * Contacts carries contacts. So neither can overwrite the other with a stale
 * form, and neither can touch the partner's currency, code, capabilities,
 * products, prices or commission - none of those are in the request.
 *
 * Nothing here decides anything. The server validates and cleans every value and
 * names a problem by the dotted field it belongs to; a problem it names lands on
 * that field. A save carries the partner's `version`, so two people editing one
 * partner cannot silently overwrite each other (409 -> reload).
 */

const MANAGE = "platform.staff_partner.manage";

const CONFLICT_MESSAGE =
  "Someone else changed this partner while you were editing, so nothing was saved. Reload to see their changes, then make your edit again.";
const REFUSED_MESSAGE = "Nothing was saved. Correct what is marked below and save again.";

// --- naming the fields -----------------------------------------------------------------

const KIND_TITLE: Record<ContactKind, string> = {
  primary: "Primary contact",
  billing: "Billing contact",
  technical: "Technical contact",
  support: "Support contact",
};

const FIELD_LABELS: Record<string, string> = {
  "business.display_name": "Company or partner name",
  "business.legal_name": "Legal business name",
  "business.business_email": "Business email",
  "business.alternative_email": "Alternative email",
  "business.website": "Website",
  "business.phone": "Business phone",
  "business.registration_number": "Registration or company number",
  "business.tax_number": "Tax, GST or VAT number",
  "business.country": "Country",
  "business.address.line1": "Address line 1",
  "business.address.line2": "Address line 2",
  "business.address.city": "City",
  "business.address.region": "State or province",
  "business.address.postal_code": "Postal or ZIP code",
  "support.email": "Support email",
  "support.phone": "Support phone",
  "support.url": "Support page",
  "payout.frequency": "Payout frequency",
  "payout.contact_email": "Payout contact email",
  internal_notes: "Internal notes",
};

const CONTACT_PART: Record<string, string> = { name: "name", job_title: "job title", email: "email", phone: "phone" };
const CONTACT_FIELD = /^contacts\.(primary|billing|technical|support)\.(name|job_title|email|phone)$/;

/** The words for a form field's dotted name, or `null` when the name is not a
 * field this page shows (a problem about anything else is shown as a message). */
function fieldLabel(field: string): string | null {
  if (Object.prototype.hasOwnProperty.call(FIELD_LABELS, field)) return FIELD_LABELS[field];
  const match = CONTACT_FIELD.exec(field);
  return match ? `${KIND_TITLE[match[1] as ContactKind]} ${CONTACT_PART[match[2]]}` : null;
}

/** One line per problem, naming the field as its label says. */
function summaryLines(errors: FieldErrors): string[] {
  return Object.entries(errors).map(([field, message]) => `${fieldLabel(field) ?? field}: ${message}`);
}

// --- saving -----------------------------------------------------------------------------

/** What the server's refusal means here. A version conflict and a problem with
 * one field become a plain message plus state the form reads; anything else
 * passes through unchanged. */
function refusal(err: unknown, setErrors: (e: FieldErrors) => void, setConflict: (c: boolean) => void): unknown {
  if (!(err instanceof ApiError)) return err;
  if (err.status === 409) {
    setConflict(true);
    return new ApiError(409, { code: "concurrent_modification", detail: CONFLICT_MESSAGE });
  }
  const body = (typeof err.body === "object" && err.body !== null ? err.body : {}) as { field?: unknown; detail?: unknown };
  if (err.status === 400 && typeof body.field === "string" && typeof body.detail === "string" && fieldLabel(body.field)) {
    setErrors(errorsFromProblems([{ field: body.field, message: body.detail }]));
    return new ApiError(400, { code: "profile_refused", detail: REFUSED_MESSAGE });
  }
  return err;
}

/** One save path for both tabs: runs through `useAction` (so the busy state
 * and the announced outcome are the page's usual ones). */
function useProfileSaver(bump: () => void) {
  const { act, busy, feedback } = useAction(bump);
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [conflict, setConflict] = useState(false);
  // A popup editor is open (it shows the conflict's Reload itself).
  const [editing, setEditing] = useState(false);

  function save(profile: PartnerProfile, body: ProfileInput, success: string) {
    setServerErrors({});
    setConflict(false);
    return act(async () => {
      try {
        await partnerConsoleApi.saveProfile(profile.partner_id, profile.version, body);
      } catch (err) {
        throw refusal(err, setServerErrors, setConflict);
      }
    }, success);
  }

  /** The popup's save: the refusal is returned (shown inside the popup), not
   * announced on the page. `null` means it was saved. */
  async function attempt(profile: PartnerProfile, body: ProfileInput): Promise<string | null> {
    setServerErrors({});
    setConflict(false);
    try {
      await partnerConsoleApi.saveProfile(profile.partner_id, profile.version, body);
      return null;
    } catch (err) {
      return describeError(refusal(err, setServerErrors, setConflict));
    }
  }

  /** After the popup saved: announce it on the page, then the page's refresh runs. */
  function announce(success: string) {
    setEditing(false);
    return act(async () => {}, success);
  }

  /** A popup opens (a fresh start, with no problem from before) or closes (a
   * conflict stays, so the page offers Reload). */
  function edit(open: boolean) {
    setEditing(open);
    if (!open) return;
    setServerErrors({});
    setConflict(false);
  }

  /** Re-read everything: the page's refresh runs after this action. */
  function reload() {
    setEditing(false);
    return act(async () => {
      setServerErrors({});
      setConflict(false);
    }, "Reloaded. This is what is saved now.");
  }

  return { save, attempt, announce, edit, editing, reload, serverErrors, conflict, busy, feedback };
}

type Saver = ReturnType<typeof useProfileSaver>;

function ConflictReload({ saver }: { saver: Saver }) {
  // While a popup is open it offers Reload itself.
  if (!saver.conflict || saver.editing) return null;
  return (
    <div className={ps.conflict}>
      <button type="button" className="w360-btn w360-btn-primary" disabled={saver.busy} onClick={() => void saver.reload()}>
        Reload
      </button>
      <span className={styles.muted}>Your unsaved edits will be replaced by what is saved now.</span>
    </div>
  );
}

// --- what is required --------------------------------------------------------------------

function requiredBusiness(d: ProfileDraft): FieldErrors {
  const out: FieldErrors = {};
  if (!d.displayName.trim()) out["business.display_name"] = "Enter the company or partner name.";
  if (!d.businessEmail.trim()) out["business.business_email"] = "Enter the business email.";
  if (!d.country) out["business.country"] = "Choose a country.";
  return out;
}

function requiredPrimary(d: ProfileDraft): FieldErrors {
  const out: FieldErrors = {};
  if (!d.contacts.primary.name.trim()) out["contacts.primary.name"] = "Enter the primary contact's name.";
  if (!d.contacts.primary.email.trim()) out["contacts.primary.email"] = "Enter the primary contact's email.";
  return out;
}

const BUSINESS_PARTS: ProfileParts = ["business", "support", "payout", "notes"];
const CONTACT_PARTS: ProfileParts = ["contacts"];

/** Whether the form differs from what is saved, in exactly the parts it would send. */
function differs(draft: ProfileDraft, baseline: ProfileDraft, parts: ProfileParts): boolean {
  return JSON.stringify(toProfileInput(draft, parts)) !== JSON.stringify(toProfileInput(baseline, parts));
}

// --- read-only views ----------------------------------------------------------------------

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children || "-"}</dd>
    </div>
  );
}

const FREQUENCY_TEXT: Record<string, string> = {
  manual: "Manual - when staff prepare one",
  monthly: "Monthly",
  quarterly: "Quarterly",
};

function ReadOnlyNote() {
  return (
    <p className={styles.muted}>
      You can see these details but not change them: changing a partner needs the platform.staff_partner.manage permission.
    </p>
  );
}

/** What is saved, as text. `edit` is the button that opens the popup editor;
 * without it the page says why nothing can be changed. */
function BusinessReadOnly({ profile: p, edit }: { profile: PartnerProfile; edit?: React.ReactNode }) {
  const b = p.business;
  const address = [b.address.line1, b.address.line2, b.address.city, b.address.region, b.address.postal_code].filter(Boolean).join(", ");
  return (
    <div className={styles.stack}>
      {edit ? <div className={ps.actionsRow}>{edit}</div> : <ReadOnlyNote />}
      <Section title="Business details">
        <dl className={ps.readOnly}>
          <Fact label="Company or partner name">{b.display_name}</Fact>
          <Fact label="Legal business name">{b.legal_name}</Fact>
          <Fact label="Business email">{b.business_email}</Fact>
          <Fact label="Alternative email">{b.alternative_email}</Fact>
          <Fact label="Website">{b.website}</Fact>
          <Fact label="Business phone">{b.phone}</Fact>
          <Fact label="Registration or company number">{b.registration_number}</Fact>
          <Fact label="Tax, GST or VAT number">{b.tax_number}</Fact>
          <Fact label="Address">{address}</Fact>
          <Fact label="Country">{b.country ? countryName(b.country) : ""}</Fact>
          <Fact label="Time zone">{(b.timezone ?? "").replace(/_/g, " ")}</Fact>
        </dl>
      </Section>
      <Section title="Support and notes">
        <dl className={ps.readOnly}>
          <Fact label="Support email">{p.support.email}</Fact>
          <Fact label="Support phone">{p.support.phone}</Fact>
          <Fact label="Support page">{p.support.url}</Fact>
          <Fact label="Internal notes (staff only)">{p.internal_notes}</Fact>
        </dl>
      </Section>
      <Section title="Payout preferences">
        <dl className={ps.readOnly}>
          <Fact label="Paid in">{p.payout.currency}</Fact>
          <Fact label="Payout frequency">{FREQUENCY_TEXT[p.payout.frequency] ?? p.payout.frequency}</Fact>
          <Fact label="Payout contact email">{p.payout.contact_email}</Fact>
        </dl>
      </Section>
    </div>
  );
}

function ContactReadOnly({ kind, contact }: { kind: ContactKind; contact: PartnerContactView }) {
  return (
    <div className={ps.contactBlock}>
      <h4>{KIND_TITLE[kind]}</h4>
      {contact.same_as_primary ? (
        <p>Same as the primary contact.</p>
      ) : (
        <dl className={ps.readOnly}>
          <Fact label="Name">{contact.name}</Fact>
          {kind === "primary" && <Fact label="Job title">{contact.job_title}</Fact>}
          <Fact label="Email">{contact.email}</Fact>
          <Fact label="Phone">{contact.phone}</Fact>
        </dl>
      )}
    </div>
  );
}

// --- one editor's state ---------------------------------------------------------------------

/**
 * The draft, its problems and its save, for one editor. On a page (the
 * Complete-profile steps) the form is the page and saves through `useAction`.
 * In a popup (the partner's Profile tab, 2026-11-05: every form in a popup) the
 * page shows what is saved and an Edit button; the popup starts from what is
 * saved each time, keeps a refusal inside itself with what was typed, and
 * closes on a save, which is then announced on the page.
 */
function useEditor(profile: PartnerProfile, saver: Saver, parts: ProfileParts, check: (d: ProfileDraft) => FieldErrors, success: string) {
  const baseline = useMemo(() => profileDraftFrom(profile), [profile]);
  const [draft, setDraft] = useState<ProfileDraft>(baseline);
  const [local, setLocal] = useState<FieldErrors>({});
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);
  const errors = { ...local, ...saver.serverErrors };
  const dirty = differs(draft, baseline, parts);

  useEffect(() => {
    if (Object.keys(local).length > 0 || Object.keys(saver.serverErrors).length > 0) summary.current?.focus();
  }, [local, saver.serverErrors]);

  const patch = (p: Partial<ProfileDraft>) => setDraft((d) => ({ ...d, ...p }));

  function checked(): boolean {
    const problems = check(draft);
    setLocal(problems);
    return Object.keys(problems).length === 0;
  }

  /** The form on a page. */
  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!checked()) return;
    void saver.save(profile, toProfileInput(draft, parts), success);
  }

  function start() {
    setDraft(baseline);
    setLocal({});
    setError(null);
    saver.edit(true);
    setOpen(true);
  }

  function close() {
    setOpen(false);
    saver.edit(false);
  }

  /** The form in a popup. */
  async function submitPopup(e: React.FormEvent) {
    e.preventDefault();
    if (saving || !checked()) return;
    setSaving(true);
    setError(null);
    const refused = await saver.attempt(profile, toProfileInput(draft, parts));
    setSaving(false);
    if (refused) {
      setError(refused);
      return;
    }
    setOpen(false);
    void saver.announce(success);
  }

  return { draft, patch, errors, dirty, summary, submit, open, start, close, submitPopup, saving, error };
}

type Editor = ReturnType<typeof useEditor>;

/** The button on the page that opens an editor's popup. */
function EditButton({ editor, label, busy }: { editor: Editor; label: string; busy: boolean }) {
  return (
    <button type="button" className="w360-btn w360-btn-primary" aria-haspopup="dialog" disabled={busy} onClick={editor.start}>
      {label}
    </button>
  );
}

/** One editor in a popup: the title and close, the refusal (and Reload after a
 * conflict), the problems, the fields, then Cancel and Save at the bottom right. */
function ProfileDialog({
  title,
  formLabel,
  submitLabel,
  editor,
  saver,
  children,
}: {
  title: string;
  formLabel: string;
  submitLabel: string;
  editor: Editor;
  saver: Saver;
  children: React.ReactNode;
}) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={editor.close} size="lg">
      <form className={dialog.form} noValidate aria-label={formLabel} onSubmit={(e) => void editor.submitPopup(e)}>
        <DialogHead titleId={titleId} title={title} onClose={editor.close} />
        <div className={dialog.body}>
          {editor.error && (
            <p className="w360-error-text" role="alert">
              {editor.error}
            </p>
          )}
          {saver.conflict && (
            <div className={ps.conflict}>
              <button type="button" className="w360-btn w360-btn-primary" disabled={saver.busy} onClick={() => void saver.reload()}>
                Reload
              </button>
              <span className={styles.muted}>Your unsaved edits will be replaced by what is saved now.</span>
            </div>
          )}
          <ErrorSummary errors={summaryLines(editor.errors)} focusRef={editor.summary} />
          {children}
        </div>
        <div className={dialog.foot}>
          {!editor.dirty && <span className={styles.muted}>No changes to save.</span>}
          <button type="button" className="w360-btn w360-btn-secondary" onClick={editor.close}>
            Cancel
          </button>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={editor.saving || saver.busy || !editor.dirty} aria-busy={editor.saving || undefined}>
            {submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** A titled part of a popup editor (a popup has no cards inside it). */
function DialogPart({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section className={ps.sectionFields} aria-labelledby={id}>
      <h3 id={id} className={styles.cardTitle}>
        {title}
      </h3>
      {children}
    </section>
  );
}

// --- Business Details ------------------------------------------------------------------------

const businessCheck = (d: ProfileDraft): FieldErrors => ({ ...localProblems(d, ["business", "support", "payout"]), ...requiredBusiness(d) });

/** `inline`: the form is the page (a Complete-profile step); otherwise what is
 * saved is shown with an Edit button that opens the form in a popup. */
export function BusinessDetailsTab({ partnerId, partner, rev, bump, inline = false }: TabProps & { inline?: boolean }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, MANAGE);
  const [load] = useLoad(() => partnerConsoleApi.profile(partnerId), [partnerId, rev]);
  const saver = useProfileSaver(bump);
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        How to reach and invoice this company. Changing these details never rewrites sales, subscriptions, commissions or payouts: each
        keeps what it was worked out under.
      </p>
      {saver.feedback}
      <ConflictReload saver={saver} />
      <LoadGate load={load} what="the business details">
        {(profile) =>
          !manage ? (
            <BusinessReadOnly profile={profile} />
          ) : inline ? (
            <BusinessEditor profile={profile} saver={saver} currencyEditable={partner.status === "draft"} />
          ) : (
            <BusinessPopup profile={profile} saver={saver} currencyEditable={partner.status === "draft"} />
          )
        }
      </LoadGate>
    </div>
  );
}

function BusinessFields({ editor, profile, currencyEditable, Part }: { editor: Editor; profile: PartnerProfile; currencyEditable: boolean; Part: typeof Section }) {
  return (
    <>
      <Part title="Business details">
        <div className={ps.sectionFields}>
          <BusinessDetailsForm value={editor.draft} onChange={editor.patch} errors={editor.errors} />
        </div>
      </Part>
      <Part title="Support and notes">
        <div className={ps.sectionFields}>
          <SupportForm value={editor.draft} onChange={editor.patch} errors={editor.errors} />
        </div>
      </Part>
      <Part title="Payout preferences">
        <div className={ps.sectionFields}>
          {!currencyEditable && (
            <p className={styles.muted}>The preferred currency is fixed once the partner is activated, so it is shown here and not offered for editing.</p>
          )}
          <PayoutPrefsForm
            value={editor.draft}
            onChange={editor.patch}
            errors={editor.errors}
            currency={profile.payout.currency}
            currencyEditable={currencyEditable}
          />
        </div>
      </Part>
    </>
  );
}

function BusinessEditor({ profile, saver, currencyEditable }: { profile: PartnerProfile; saver: Saver; currencyEditable: boolean }) {
  const editor = useEditor(profile, saver, BUSINESS_PARTS, businessCheck, "Business details saved.");
  return (
    <form className={ps.formStack} noValidate aria-label="Business details" onSubmit={editor.submit}>
      <ErrorSummary errors={summaryLines(editor.errors)} focusRef={editor.summary} />
      <BusinessFields editor={editor} profile={profile} currencyEditable={currencyEditable} Part={Section} />
      <div className={ps.actionsRow}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={saver.busy || !editor.dirty}>
          Save business details
        </button>
        {!editor.dirty && <span className={styles.muted}>No changes to save.</span>}
      </div>
    </form>
  );
}

function BusinessPopup({ profile, saver, currencyEditable }: { profile: PartnerProfile; saver: Saver; currencyEditable: boolean }) {
  const editor = useEditor(profile, saver, BUSINESS_PARTS, businessCheck, "Business details saved.");
  return (
    <>
      <BusinessReadOnly profile={profile} edit={<EditButton editor={editor} label="Edit business details" busy={saver.busy} />} />
      {editor.open && (
        <ProfileDialog title="Edit business details" formLabel="Business details" submitLabel="Save business details" editor={editor} saver={saver}>
          <BusinessFields editor={editor} profile={profile} currencyEditable={currencyEditable} Part={DialogPart} />
        </ProfileDialog>
      )}
    </>
  );
}

// --- Contacts ----------------------------------------------------------------------------------

const contactsCheck = (d: ProfileDraft): FieldErrors => ({ ...localProblems(d, CONTACT_PARTS), ...requiredPrimary(d) });

export function ContactsTab({ partnerId, rev, bump, onOpen, inline = false }: TabProps & { onOpen?: (tab: TabKey) => void; inline?: boolean }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, MANAGE);
  const [load] = useLoad(() => partnerConsoleApi.profile(partnerId), [partnerId, rev]);
  const saver = useProfileSaver(bump);
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        The people to reach about this partner: primary, billing, technical and support. A role left as &ldquo;same as primary&rdquo; stores
        nothing of its own, so it always follows the primary contact. This is staff-only information: it is never shown on a partner
        portal or to another partner.
      </p>
      <p className={styles.muted}>
        A contact is a person to reach, not an account, and cannot sign in.{" "}
        {onOpen ? (
          <>
            To let someone sign in, add them under{" "}
            <button type="button" className={`w360-btn w360-btn-secondary ${ps.linkButton}`} onClick={() => onOpen("users")}>
              {TAB_LABELS.users}
            </button>
            .
          </>
        ) : (
          <>To let someone sign in, add them under {TAB_LABELS.users}.</>
        )}
      </p>
      {saver.feedback}
      <ConflictReload saver={saver} />
      <LoadGate load={load} what="the contacts">
        {(profile) =>
          !manage ? (
            <ContactsReadOnly profile={profile} />
          ) : inline ? (
            <ContactsEditor profile={profile} saver={saver} />
          ) : (
            <ContactsPopup profile={profile} saver={saver} />
          )
        }
      </LoadGate>
    </div>
  );
}

function ContactsReadOnly({ profile, edit }: { profile: PartnerProfile; edit?: React.ReactNode }) {
  return (
    <div className={styles.stack}>
      {edit ? <div className={ps.actionsRow}>{edit}</div> : <ReadOnlyNote />}
      <Section title="Contacts">
        <div className={ps.sectionFields}>
          {CONTACT_KINDS.map((kind) => (
            <ContactReadOnly key={kind} kind={kind} contact={profile.contacts[kind]} />
          ))}
        </div>
      </Section>
    </div>
  );
}

function ContactsEditor({ profile, saver }: { profile: PartnerProfile; saver: Saver }) {
  const editor = useEditor(profile, saver, CONTACT_PARTS, contactsCheck, "Contacts saved.");
  return (
    <form className={ps.formStack} noValidate aria-label="Contacts" onSubmit={editor.submit}>
      <ErrorSummary errors={summaryLines(editor.errors)} focusRef={editor.summary} />
      <Section title="Contacts">
        <div className={ps.sectionFields}>
          <ContactsForm value={editor.draft} onChange={editor.patch} errors={editor.errors} />
        </div>
      </Section>
      <div className={ps.actionsRow}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={saver.busy || !editor.dirty}>
          Save contacts
        </button>
        {!editor.dirty && <span className={styles.muted}>No changes to save.</span>}
      </div>
    </form>
  );
}

function ContactsPopup({ profile, saver }: { profile: PartnerProfile; saver: Saver }) {
  const editor = useEditor(profile, saver, CONTACT_PARTS, contactsCheck, "Contacts saved.");
  return (
    <>
      <ContactsReadOnly profile={profile} edit={<EditButton editor={editor} label="Edit contacts" busy={saver.busy} />} />
      {editor.open && (
        <ProfileDialog title="Edit contacts" formLabel="Contacts" submitLabel="Save contacts" editor={editor} saver={saver}>
          <div className={ps.sectionFields}>
            <ContactsForm value={editor.draft} onChange={editor.patch} errors={editor.errors} />
          </div>
        </ProfileDialog>
      )}
    </>
  );
}

// --- Payout preferences (the affiliate's Payout Settings step) --------------------------------

const PAYOUT_PARTS: ProfileParts = ["payout"];
const payoutCheck = (d: ProfileDraft): FieldErrors => localProblems(d, PAYOUT_PARTS);

/** Where commission goes and how often - preferences for whoever prepares a
 * payout - and, while the partner is a draft, its preferred currency. No bank or
 * card details: Workeasy360 moves no money and collects none. A Complete-profile
 * step, so the form stays on the page. */
export function PayoutPreferencesTab({ partnerId, partner, rev, bump }: TabProps) {
  const [load] = useLoad(() => partnerConsoleApi.profile(partnerId), [partnerId, rev]);
  const saver = useProfileSaver(bump);
  return (
    <div className={styles.stack}>
      {saver.feedback}
      <ConflictReload saver={saver} />
      <LoadGate load={load} what="the payout preferences">
        {(profile) => <PayoutEditor profile={profile} saver={saver} currencyEditable={partner.status === "draft"} />}
      </LoadGate>
    </div>
  );
}

function PayoutEditor({ profile, saver, currencyEditable }: { profile: PartnerProfile; saver: Saver; currencyEditable: boolean }) {
  const editor = useEditor(profile, saver, PAYOUT_PARTS, payoutCheck, "Payout preferences saved.");
  return (
    <form className={ps.formStack} noValidate aria-label="Payout preferences" onSubmit={editor.submit}>
      <ErrorSummary errors={summaryLines(editor.errors)} focusRef={editor.summary} />
      <Section title="Payout preferences">
        <div className={ps.sectionFields}>
          <PayoutPrefsForm
            value={editor.draft}
            onChange={editor.patch}
            errors={editor.errors}
            currency={profile.payout.currency}
            currencyEditable={currencyEditable}
          />
          <p className={styles.muted}>A minimum payout threshold is not part of the payout model: staff decide when to prepare a payout.</p>
        </div>
      </Section>
      <div className={ps.actionsRow}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={saver.busy || !editor.dirty}>
          Save payout preferences
        </button>
        {!editor.dirty && <span className={styles.muted}>No changes to save.</span>}
      </div>
    </form>
  );
}
