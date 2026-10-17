"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { partnerApi, newKey, type Brand, type Diagnosis } from "@/lib/api/controlPlane";
import { panelApi } from "@/lib/api/partnerPanel";
import { portalOwnerInvitationApi } from "@/lib/api/partnerConsole";
import { usePartner } from "./ControlShell";
import { ActivationBadge, OwnerInvitationActions } from "./PartnerCustomers";
import { DiagnosticResult } from "./AdminScreens";
import { applyAccent, safeColor } from "./useBranding";
import { LoadGate, MoreButton, Status, Table, money, useAction, useLoad, usePaged, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

function usePartnerId(): { id: string; has: (c: string) => boolean; active: boolean; isAdmin: boolean } {
  const { partner } = usePartner();
  return {
    id: partner?.partner_id ?? "",
    has: (c) => partner?.capabilities.includes(c) ?? false,
    active: partner?.status === "active",
    isAdmin: partner?.is_admin ?? false,
  };
}

// --- portal chooser --------------------------------------------------------------------

export function PortalChooser() {
  const [load] = useLoad(() => partnerApi.portal(), []);
  return (
    <div className={styles.stack}>
      <h1>Partner accounts</h1>
      <LoadGate load={load} what="your partner accounts">
        {({ partners }) =>
          partners.length === 0 ? (
            <p>You aren&apos;t a member of any partner account.</p>
          ) : (
            <ul className={styles.chain}>
              {partners.map((p) => (
                <li key={p.partner_id} className={styles.chainItem}>
                  <Link href={`/partner/${p.partner_id}`}>{p.display_name}</Link>
                  <Status value={p.status} />
                </li>
              ))}
            </ul>
          )
        }
      </LoadGate>
    </div>
  );
}

// --- customers ---------------------------------------------------------------------------------

export function PartnerCustomers() {
  const { id, has, active } = usePartnerId();
  // Searching is the server's: it matches the name of a live customer and
  // never reaches a former one (whose name is not shown to the partner).
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const searchId = useId();
  const paged = usePaged((cursor) => partnerApi.customers(id, cursor, applied), [id, applied]);
  const { act, busy, feedback } = useAction(paged.reload);
  const nameId = useId();
  const emailId = useId();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const firstId = useId();
  const lastId = useId();
  // One command id per *attempt*: a double click or a retry after a network
  // failure replays the same provisioning instead of creating a second customer.
  const key = useRef(newKey());
  const [diagnosing, setDiagnosing] = useState<string | null>(null);

  return (
    <div className={styles.stack}>
      <h1>Users</h1>
      <p className={styles.lead}>
        The customer organizations you serve and who has access to what. Each customer stays its own organization with its own people: you manage the relationship and the subscriptions, never their data.
      </p>
      <form
        className={styles.row}
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(search.trim());
        }}
      >
        <label className="w360-label" htmlFor={searchId}>
          Search customers
        </label>
        <input id={searchId} className="w360-input" type="search" maxLength={80} value={search} onChange={(e) => setSearch(e.target.value)} />
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
        {applied && (
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => {
              setSearch("");
              setApplied("");
            }}
          >
            Clear
          </button>
        )}
      </form>
      {has("customer.provision") || has("customer.manage") ? (
        <div className={styles.row}>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const { blob, truncated } = await panelApi.exportCustomers(id);
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = "customers.csv";
                link.click();
                URL.revokeObjectURL(url);
                if (truncated) throw new Error("The export was cut at 5,000 customers.");
              }, "Your customer list was downloaded.")
            }
          >
            Export customers (CSV)
          </button>
        </div>
      ) : null}
      {feedback}
      {paged.state === "loading" && <p aria-live="polite">Loading customers…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to customers.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load customers.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Your customers" head={["Customer", "Relationship", "Owner", "Subscription", "Products", "Joined", ""]} isEmpty={paged.rows.length === 0} empty={applied ? "No customer matches that search." : "No customers yet."}>
            {paged.rows.map((c) => {
              // An ended relationship is history: the server returns none of the
              // customer's current details for it, so there is nothing to show or act on.
              const ended = c.link_status === "ended";
              return (
                <tr key={c.organization_id}>
                  <th scope="row">{ended ? <span className={styles.muted}>Former customer</span> : c.name}</th>
                  <td>
                    <Status value={c.link_status} />
                    {c.ended_at && <span className={styles.muted}> ended {when(c.ended_at)}</span>}
                  </td>
                  <td>
                    {ended ? (
                      "—"
                    ) : (
                      <div className={styles.stack}>
                        <ActivationBadge owner={c.owner_activation} />
                        {c.owner_activation.activation === "pending" && c.owner_activation.expires_at && (
                          <span className={styles.muted}>Expires {when(c.owner_activation.expires_at)}</span>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {ended ? (
                      "—"
                    ) : (
                      <>
                        <Status value={c.subscription_status} />
                        {c.access_class === "trial" && <span className={styles.muted}> trial</span>}
                      </>
                    )}
                  </td>
                  <td>{c.products.join(", ") || "—"}</td>
                  <td>{when(c.started_at)}</td>
                  <td>
                    <div className={styles.row}>
                      {has("subscription.sell") && active && c.link_status === "active" && (
                        <SellForm partnerId={id} organizationId={c.organization_id} onDone={paged.reload} />
                      )}
                      {has("customer.provision") && active && !ended && (
                        <OwnerInvitationActions
                          activation={c.owner_activation.activation}
                          live={!ended}
                          subject={c.name}
                          resend={() => portalOwnerInvitationApi.resend(id, c.organization_id)}
                          revoke={() => portalOwnerInvitationApi.revoke(id, c.organization_id)}
                          onDone={paged.reload}
                        />
                      )}
                      {has("support.access") && !ended && (
                        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setDiagnosing(c.organization_id)}>
                          Diagnose launch
                        </button>
                      )}
                    </div>
                    {diagnosing === c.organization_id && <PartnerDiagnose partnerId={id} organizationId={c.organization_id} />}
                  </td>
                </tr>
              );
            })}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
      {has("customer.provision") && active && (
        <form
          id="add-customer"
          className={`w360-card ${styles.card} ${styles.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await partnerApi.provision(id, name.trim(), email.trim(), key.current, { first: first.trim(), last: last.trim() });
              key.current = newKey();
              setName("");
              setEmail("");
              setFirst("");
              setLast("");
            }, "Customer created. Their owner has been emailed a one-time link to activate the account; until they do, the customer shows as awaiting activation.");
          }}
        >
          <h2 className={styles.cardTitle}>Add a customer</h2>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={nameId}>
              Organization name
            </label>
            <input id={nameId} className="w360-input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={firstId}>
              Owner&apos;s first name
            </label>
            <input id={firstId} className="w360-input" maxLength={100} value={first} onChange={(e) => setFirst(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={lastId}>
              Owner&apos;s last name
            </label>
            <input id={lastId} className="w360-input" maxLength={100} value={last} onChange={(e) => setLast(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={emailId}>
              Owner&apos;s email
            </label>
            <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !name.trim() || !email.trim()}>
              Create customer
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function SellForm({ partnerId, organizationId, onDone }: { partnerId: string; organizationId: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [catalog] = useLoad(() => (open ? partnerApi.catalog(partnerId) : Promise.resolve({ results: [] })), [open, partnerId]);
  const { act, busy, feedback } = useAction(onDone);
  const planId = useId();
  const qtyId = useId();
  const [plan, setPlan] = useState("");
  const [quantity, setQuantity] = useState("1");
  const key = useRef(newKey());

  if (!open) {
    return (
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(true)}>
        Sell a plan
      </button>
    );
  }
  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          await partnerApi.sell(partnerId, organizationId, plan, Number(quantity), key.current);
          key.current = newKey();
          setOpen(false);
        }, "Plan sold. The price was set by the server from your approved price book.");
      }}
    >
      {feedback}
      <LoadGate load={catalog} what="your approved plans">
        {(d) => (
          <>
            <div className={styles.field}>
              <label className="w360-label" htmlFor={planId}>
                Plan
              </label>
              <select id={planId} className="w360-input" required value={plan} onChange={(e) => setPlan(e.target.value)}>
                <option value="">Choose…</option>
                {d.results.map((p) => (
                  <option key={p.plan_version_id} value={p.plan_version_id}>
                    {p.product_name} — {p.plan_name}
                    {p.price ? ` (${money(p.price.total_amount_minor, p.price.currency)} / ${p.price.interval})` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className={styles.field}>
              <label className="w360-label" htmlFor={qtyId}>
                Seats
              </label>
              <input id={qtyId} className="w360-input" inputMode="numeric" pattern="[0-9]{1,5}" required value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </div>
            <div className={styles.row}>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !plan}>
                Confirm sale
              </button>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </>
        )}
      </LoadGate>
    </form>
  );
}

function PartnerDiagnose({ partnerId, organizationId }: { partnerId: string; organizationId: string }) {
  const productId = useId();
  const [product, setProduct] = useState("");
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        partnerApi
          .diagnose(partnerId, organizationId, product.trim())
          .then(setResult)
          .catch(() => setError("Couldn't run the check."));
      }}
    >
      <div className={styles.field}>
        <label className="w360-label" htmlFor={productId}>
          Product code
        </label>
        <input id={productId} className="w360-input" required value={product} onChange={(e) => setProduct(e.target.value)} />
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-secondary" disabled={!product.trim()}>
          Check
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {result && <DiagnosticResult result={result} />}
    </form>
  );
}

// --- commissions & payouts ----------------------------------------------------------------------------

export function PartnerCommissions() {
  const { id } = usePartnerId();
  const [balances, setBalances] = useState<
    { currency: string; earned_minor: number; adjustments_minor: number; balance_minor: number }[]
  >([]);
  const paged = usePaged(async (cursor) => {
    const page = await partnerApi.commissions(id, cursor);
    if (!cursor) setBalances(page.balances);
    return page;
  }, [id]);
  return (
    <div className={styles.stack}>
      <h1>Commission</h1>
      <p className={styles.lead}>Every figure is calculated by Workeasy360 from the sales your customers make. Reversals and adjustments appear as their own entries.</p>
      {balances.length > 0 && (
        <Table caption="Balance" head={["Currency", "Earned", "Adjustments", "Balance"]}>
          {balances.map((b) => (
            <tr key={b.currency}>
              <th scope="row">{b.currency}</th>
              <td className={styles.num}>{money(b.earned_minor, b.currency)}</td>
              <td className={styles.num}>{money(b.adjustments_minor, b.currency)}</td>
              <td className={styles.num}>{money(b.balance_minor, b.currency)}</td>
            </tr>
          ))}
        </Table>
      )}
      {paged.state === "loading" && <p aria-live="polite">Loading…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to commission.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load commission.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Ledger entries" head={["When", "Kind", "Amount", "Clears"]} isEmpty={paged.rows.length === 0} empty="No commission yet.">
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.event_at)}</td>
                <td>{a.kind}</td>
                <td className={styles.num}>{money(a.amount_minor, a.currency)}</td>
                <td>{when(a.clears_at)}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

export function PartnerPayouts() {
  const { id } = usePartnerId();
  const paged = usePaged((cursor) => partnerApi.payouts(id, cursor), [id]);
  return (
    <div className={styles.stack}>
      <h1>Payouts</h1>
      <p className={styles.lead}>“Paid” means Workeasy360 recorded a transfer it made outside this platform.</p>
      {paged.state === "loading" && <p aria-live="polite">Loading…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to payouts.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load payouts.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Your payouts" head={["Period end", "Status", "Gross", "Adjustments", "Payable"]} isEmpty={paged.rows.length === 0} empty="No payouts yet.">
            {paged.rows.map((p) => (
              <tr key={p.id}>
                <th scope="row">{p.period_end}</th>
                <td>
                  <Status value={p.status} />
                </td>
                <td className={styles.num}>{money(p.gross_minor, p.currency)}</td>
                <td className={styles.num}>{money(p.adjustments_minor, p.currency)}</td>
                <td className={styles.num}>{money(p.payable_minor, p.currency)}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

// --- branding --------------------------------------------------------------------------------------------

const LOGO_TYPES = ["image/png", "image/jpeg"];
const MAX_LOGO_BYTES = 512 * 1024;

export function PartnerBranding() {
  const { id, active } = usePartnerId();
  const [load, reload] = useLoad(() => partnerApi.brand(id), [id]);
  return (
    <div className={styles.stack}>
      <h1>Branding</h1>
      <p className={styles.lead}>
        Your name, accent colour and logo are shown to your customers on your domain. The layout, and every security
        prompt, stay Workeasy360&apos;s.
      </p>
      <BrandScope />
      <LoadGate load={load} what="your branding">
        {({ brand }) => <BrandForm partnerId={id} brand={brand} disabled={!active} onSaved={reload} />}
      </LoadGate>
    </div>
  );
}

/** Which brand is which: the WorkEasy360 platform brand (this panel, every
 * security prompt - never changed here) and the partner's own white-label
 * brand (what their customers see - edited below). */
function BrandScope() {
  return (
    <div className={styles.brandScope}>
      <section className={styles.scopeCard} aria-labelledby="scope-platform">
        <p className={styles.scopeTag}>Fixed</p>
        <h2 id="scope-platform" className={styles.cardTitle}>
          WorkEasy360 platform brand
        </h2>
        <p className={styles.muted}>
          This partner panel, the sign-in security prompts and Workeasy360&apos;s own messages always use the WorkEasy360
          look. It is not changed by your branding.
        </p>
        <ul className={styles.platformSwatches} aria-label="WorkEasy360 colours">
          <li>
            <span className={`${styles.platformSwatch} ${styles.swatchGreen}`} aria-hidden="true" />
            WorkEasy360 green
          </li>
          <li>
            <span className={`${styles.platformSwatch} ${styles.swatchInk}`} aria-hidden="true" />
            Deep navy
          </li>
        </ul>
      </section>
      <section className={`${styles.scopeCard} ${styles.scopeCardPartner}`} aria-labelledby="scope-partner">
        <p className={styles.scopeTag}>Yours to edit</p>
        <h2 id="scope-partner" className={styles.cardTitle}>
          Your white-label brand
        </h2>
        <p className={styles.muted}>
          What your customers see on your domain: your brand name, accent colour and logo. Change it below; the live
          preview shows your colours as you type.
        </p>
      </section>
    </div>
  );
}

function BrandForm({ partnerId, brand, disabled, onSaved }: { partnerId: string; brand: Brand | null; disabled: boolean; onSaved: () => void }) {
  const { act, busy, feedback } = useAction(onSaved);
  const nameId = useId();
  const colorId = useId();
  const emailId = useId();
  const urlId = useId();
  const logoId = useId();
  const [name, setName] = useState(brand?.brand_name ?? "");
  const [color, setColor] = useState(brand?.accent_color ?? "");
  const [email, setEmail] = useState(brand?.support_email ?? "");
  const [url, setUrl] = useState(brand?.support_url ?? "");
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const preview = useRef<HTMLElement>(null);
  const validColor = color === "" || safeColor(color) !== null;

  // The preview is isolated to its own element: the accent is applied there
  // through the CSSOM, never as markup, so a bad value cannot leak into the page.
  useEffect(() => {
    if (preview.current) applyAccent(preview.current, color);
  }, [color]);

  function pickLogo(file: File | undefined) {
    setLogoError(null);
    setLogoPreview(null);
    setLogoFile(null);
    if (!file) return;
    if (!LOGO_TYPES.includes(file.type)) {
      setLogoError("Use a PNG or JPEG image. SVG is not accepted.");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError("The logo must be 512 KB or smaller.");
      return;
    }
    setLogoFile(file);
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" && setLogoPreview(reader.result);
    reader.readAsDataURL(file);
  }

  return (
    <div className={styles.stack}>
      {feedback}
      <form
        className={`w360-card ${styles.card} ${styles.form}`}
        onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            // A save *replaces* the brand, so the current logo must be sent back
            // or it is cleared. A newly chosen file is uploaded first and replaces it.
            let logoId = brand?.logo_file_id ?? undefined;
            if (logoFile) logoId = (await partnerApi.uploadLogo(partnerId, logoFile)).logo_file_id;
            await partnerApi.saveBrand(
              partnerId,
              {
                brand_name: name.trim(),
                accent_color: color,
                support_email: email.trim(),
                support_url: url.trim(),
                theme: {},
                logo_file_id: logoId,
              },
              brand?.version,
            );
            setLogoFile(null);
          }, "Branding saved.");
        }}
      >
        <div className={styles.field}>
          <label className="w360-label" htmlFor={nameId}>
            Brand name
          </label>
          <input id={nameId} className="w360-input" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} disabled={disabled} />
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={colorId}>
            Accent colour (hex, e.g. #1a56db)
          </label>
          <input id={colorId} className="w360-input" value={color} onChange={(e) => setColor(e.target.value)} aria-invalid={!validColor} aria-describedby={validColor ? undefined : `${colorId}-e`} disabled={disabled} />
          {!validColor && (
            <span id={`${colorId}-e`} className="w360-error-text" role="alert">
              Enter a six-digit hex colour such as #1a56db.
            </span>
          )}
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={emailId}>
            Support email
          </label>
          <input id={emailId} className="w360-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={disabled} />
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={urlId}>
            Support page (https)
          </label>
          <input id={urlId} className="w360-input" type="url" pattern="https://.*" value={url} onChange={(e) => setUrl(e.target.value)} disabled={disabled} />
        </div>
        <div className={styles.field}>
          <label className="w360-label" htmlFor={logoId}>
            Logo (PNG or JPEG, up to 512 KB)
          </label>
          <input id={logoId} type="file" accept="image/png,image/jpeg" onChange={(e) => pickLogo(e.target.files?.[0])} disabled={disabled} />
          {logoError && (
            <span className="w360-error-text" role="alert">
              {logoError}
            </span>
          )}
          {brand?.logo_file_id && !logoFile && <span className={styles.muted}>A logo is saved. Choose a file to replace it.</span>}
        </div>
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || disabled || !validColor || !name.trim()}>
            Save branding
          </button>
        </div>
      </form>

      {/* The partner's colours apply inside this section only (applyAccent on
          its own element); the panel around it stays WorkEasy360's. */}
      <section aria-labelledby="preview-h" className={styles.preview} ref={preview}>
        <h2 id="preview-h" className={styles.cardTitle}>
          Your white-label brand: live preview
        </h2>
        <p className={styles.muted}>
          Customers see this beside the standard Workeasy360 sign-in.{" "}
          {safeColor(color) ? `Accent ${color}.` : "The default accent until you choose one."}
        </p>
        <div className={local.simulated}>
          <div className={local.simHeader}>
            {logoPreview ? (
              // eslint-disable-next-line @next/next/no-img-element -- a data: URL of a local, validated file
              <img className={styles.logo} src={logoPreview} alt={`${name || "Your"} logo preview`} />
            ) : (
              <span className={styles.previewMark} aria-hidden="true" />
            )}
            <strong>{name || "Your brand"}</strong>
          </div>
          <div className={styles.previewSignIn} aria-hidden="true">
            <p>Sign in to {name || "your brand"}</p>
            <div className={local.simTile}>Email</div>
            <div className={local.simTile}>Password</div>
            <span className={styles.previewButton}>Sign in</span>
          </div>
        </div>
      </section>
    </div>
  );
}

// --- domains ----------------------------------------------------------------------------------------------

export function PartnerDomains() {
  const { id, active } = usePartnerId();
  const [load, reload] = useLoad(() => partnerApi.domains(id), [id]);
  const { act, busy, feedback } = useAction(reload);
  const hostId = useId();
  const [host, setHost] = useState("");
  return (
    <div className={styles.stack}>
      <h1>Domains</h1>
      <p className={styles.lead}>
        A domain serves your branding only after you prove you control it by publishing the DNS record shown, and after
        you activate it.
      </p>
      {feedback}
      <LoadGate load={load} what="your domains">
        {(d) => (
          <Table caption="Custom domains" head={["Hostname", "Status", "DNS record to publish", ""]} isEmpty={d.results.length === 0} empty="No domains yet.">
            {d.results.map((r) => (
              <tr key={r.id}>
                <th scope="row" className={styles.mono}>
                  {r.hostname}
                </th>
                <td>
                  <Status value={r.status} />
                  {r.failure_code && <div className={styles.muted}>{r.failure_code.replace(/_/g, " ")}</div>}
                </td>
                <td className={styles.mono}>
                  {r.verification_record.value
                    ? `${r.verification_record.type} ${r.verification_record.name} = ${r.verification_record.value}`
                    : "—"}
                </td>
                <td>
                  <div className={styles.row}>
                    {active && (r.status === "requested" || r.status === "verification_pending") && (
                      <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => partnerApi.domainAction(id, r.id, "verify"), "Checked the DNS record.")}>
                        Check DNS
                      </button>
                    )}
                    {active && r.status === "verified" && (
                      <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => act(() => partnerApi.domainAction(id, r.id, "activate"), "Domain activated.")}>
                        Activate
                      </button>
                    )}
                    {r.status !== "disabled" && (
                      <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => act(() => partnerApi.domainAction(id, r.id, "disable"), "Domain disabled.")}>
                        Disable
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
      {active && (
        <form
          className={`w360-card ${styles.card} ${styles.form}`}
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await partnerApi.requestDomain(id, host.trim().toLowerCase());
              setHost("");
            }, "Domain requested. Publish the DNS record, then check it.");
          }}
        >
          <h2 className={styles.cardTitle}>Add a domain</h2>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={hostId}>
              Hostname (e.g. app.example.com)
            </label>
            <input id={hostId} className="w360-input" required value={host} onChange={(e) => setHost(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !host.trim()}>
              Request domain
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
