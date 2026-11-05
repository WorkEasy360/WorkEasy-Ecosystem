"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { ApiError } from "@/lib/api/client";
import {
  getSignInPreferences,
  resetSignInPreferences,
  saveSignInPreferences,
  type EffectiveSignIn,
  type SignInPreferenceChanges,
  type SignInPreferences,
  type SignInPreferenceValue,
} from "@/lib/api/signIn";
import { PageHeader, Panel } from "@/features/console/consoleUi";
import { FormPopup } from "@/features/controlplane/Modal";
import { DEFAULT_LABELS, overrideLabel, whatHappens } from "./signInCopy";
import styles from "./SignInPreferences.module.css";

type Load = { status: "loading" } | { status: "error"; signedOut: boolean } | { status: "ready"; data: SignInPreferences };

/**
 * Settings -> Sign-in preferences (ADR-044): how Workeasy360 signs this person
 * in to its products. Their own setting, in every organization - never an
 * organization's, never visible to an administrator.
 *
 * The server decides what each product offers (`choices`) and what therefore
 * happens (`effective`); this screen only offers those choices and shows the
 * saved answer. Changes take effect the next time a product is opened -
 * nothing is signed out. The page shows what is saved; the choices are made in
 * an Edit popup (2026-11-05, every form in a popup).
 */
export function SignInPreferencesScreen() {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [draftDefault, setDraftDefault] = useState<EffectiveSignIn>("auto_sso");
  const [draftOverrides, setDraftOverrides] = useState<Record<string, SignInPreferenceValue>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const legendId = useId();
  const noteId = useId();

  const apply = useCallback((data: SignInPreferences) => {
    setLoad({ status: "ready", data });
    setDraftDefault(data.default);
    setDraftOverrides(Object.fromEntries(data.products.map((p) => [p.product_code, p.override])));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSignInPreferences()
      .then((data) => !cancelled && apply(data))
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ status: "error", signedOut: err instanceof ApiError && err.status === 401 });
      });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  if (load.status === "loading") return <p aria-live="polite">Loading your sign-in preferences…</p>;
  if (load.status === "error") {
    return (
      <p role="alert">
        {load.signedOut ? (
          <>
            Your session has ended. <a href="/login">Sign in</a> to change your sign-in preferences.
          </>
        ) : (
          "Couldn't load your sign-in preferences. Try again in a moment."
        )}
      </p>
    );
  }

  const { data } = load;
  const changes: SignInPreferenceChanges = {};
  if (draftDefault !== data.default) changes.default = draftDefault;
  const productChanges = Object.fromEntries(
    data.products
      .filter((p) => draftOverrides[p.product_code] !== undefined && draftOverrides[p.product_code] !== p.override)
      .map((p) => [p.product_code, draftOverrides[p.product_code]]),
  );
  if (Object.keys(productChanges).length > 0) changes.products = productChanges;
  const dirty = Object.keys(changes).length > 0;

  async function save() {
    if (!dirty) return;
    setBusy(true);
    setMessage(null);
    try {
      apply(await saveSignInPreferences(changes));
    } catch (err) {
      const detail = err instanceof ApiError && typeof err.body === "object" && err.body !== null ? (err.body as { detail?: string }).detail : undefined;
      // The popup's error line reads an ApiError's `detail`: the same words as before.
      throw new ApiError(400, { detail: detail ?? "Couldn't save your sign-in preferences. Try again in a moment." });
    } finally {
      setBusy(false);
    }
    return "Saved. This applies the next time you open a product.";
  }

  async function reset() {
    setBusy(true);
    setMessage(null);
    try {
      apply(await resetSignInPreferences());
      setMessage({ tone: "ok", text: "Reset. Workeasy360 will sign you in to every product automatically." });
    } catch {
      setMessage({ tone: "error", text: "Couldn't reset your sign-in preferences. Try again in a moment." });
    } finally {
      setBusy(false);
    }
  }

  const savedDefault = DEFAULT_LABELS[data.default]?.label ?? data.default;

  return (
    <div className={styles.stack}>
      <PageHeader
        title="Sign-in preferences"
        lead="Choose how Workeasy360 signs you in to its products. This is your own setting, in every organization. You can change it anytime."
      />
      <Panel title="Workeasy360 product sign-in">
        <p>
          <span className={styles.productName}>Your default for every product:</span> {savedDefault}
        </p>
        <p className={styles.muted}>Products added to Workeasy360 later use this setting too, unless you choose otherwise for them.</p>
      </Panel>

      <Panel title="Product overrides">
        {data.products.length === 0 ? (
          <p>No products are connected to Workeasy360 sign-in yet.</p>
        ) : (
          <ul className={styles.products}>
            {data.products.map((product) => (
              <SavedProductRow key={product.product_code} product={product} />
            ))}
          </ul>
        )}
      </Panel>

      <div className={styles.actions}>
        <FormPopup
          label="Edit sign-in preferences"
          title="Edit sign-in preferences"
          size="lg"
          columns={1}
          submitLabel="Save changes"
          canSubmit={dirty && !busy}
          disabled={busy}
          onOpen={() => {
            setMessage(null);
            setDraftDefault(data.default);
            setDraftOverrides(Object.fromEntries(data.products.map((p) => [p.product_code, p.override])));
          }}
          onSubmit={save}
        >
          <fieldset className={styles.fieldset} aria-describedby={noteId}>
            <legend id={legendId} className={styles.legend}>
              Your default for every product
            </legend>
            <div className={styles.options}>
              {data.default_choices.map((value) => (
                <DefaultOption
                  key={value}
                  value={value}
                  checked={draftDefault === value}
                  disabled={busy}
                  onChange={() => setDraftDefault(value)}
                />
              ))}
            </div>
            <p id={noteId} className={styles.muted}>Products added to Workeasy360 later use this setting too, unless you choose otherwise for them below.</p>
          </fieldset>
          {data.products.length > 0 && (
            <fieldset className={styles.fieldset}>
              <legend className={styles.legend}>Product overrides</legend>
              <p className={styles.muted}>
                Choose differently for one product. &ldquo;Use my default&rdquo; follows the setting above. A product that cannot sign you in with
                Workeasy360 has nothing to choose.
              </p>
              <ul className={styles.products}>
                {data.products.map((product) => (
                  <ProductRow
                    key={product.product_code}
                    product={product}
                    value={draftOverrides[product.product_code] ?? product.override}
                    defaultValue={draftDefault}
                    disabled={busy}
                    onChange={(next) => setDraftOverrides((cur) => ({ ...cur, [product.product_code]: next }))}
                  />
                ))}
              </ul>
            </fieldset>
          )}
        </FormPopup>
        <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void reset()}>
          Reset all to default
        </button>
      </div>
      <p role="status" aria-live="polite" className={message?.tone === "error" ? styles.error : styles.ok}>
        {message?.text ?? ""}
      </p>
    </div>
  );
}

/** A product's saved sign-in, as text on the page (it is changed in the popup). */
function SavedProductRow({ product }: { product: SignInPreferences["products"][number] }) {
  const name = product.name || product.display_name;
  return (
    <li className={styles.product}>
      <span className={styles.glyph} aria-hidden="true">
        {productIconKnown(product.icon) ? <ProductIcon icon={product.icon} size={20} /> : name.slice(0, 1)}
      </span>
      <div className={styles.productBody}>
        <span className={styles.productName}>{name}</span>
        <p className={styles.muted}>
          {product.choices.length > 0 ? `Saved: ${whatHappens(product, name).toLowerCase()}.` : "Separate product sign-in required."}
        </p>
      </div>
    </li>
  );
}

function DefaultOption({
  value,
  checked,
  disabled,
  onChange,
}: {
  value: EffectiveSignIn;
  checked: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  const id = useId();
  const hintId = useId();
  const copy = DEFAULT_LABELS[value] ?? { label: value, hint: "" };
  return (
    <div className={styles.option}>
      <input id={id} type="radio" name="default-sign-in" value={value} checked={checked} disabled={disabled} onChange={onChange} aria-describedby={hintId} />
      <label htmlFor={id} className={styles.optionText}>
        <span className={styles.optionLabel}>
          {copy.label}
          {value === "auto_sso" && <span className={styles.recommended}> (recommended)</span>}
        </span>
        <span id={hintId} className={styles.optionHint}>
          {copy.hint}
        </span>
      </label>
    </div>
  );
}

function ProductRow({
  product,
  value,
  defaultValue,
  disabled,
  onChange,
}: {
  product: SignInPreferences["products"][number];
  value: SignInPreferenceValue;
  defaultValue: EffectiveSignIn;
  disabled: boolean;
  onChange: (next: SignInPreferenceValue) => void;
}) {
  const selectId = useId();
  const statusId = useId();
  const name = product.name || product.display_name;
  return (
    <li className={styles.product}>
      <span className={styles.glyph} aria-hidden="true">
        {productIconKnown(product.icon) ? <ProductIcon icon={product.icon} size={20} /> : name.slice(0, 1)}
      </span>
      <div className={styles.productBody}>
        {product.choices.length > 0 ? (
          <>
            <label htmlFor={selectId} className={styles.productName}>
              {name}
            </label>
            <select
              id={selectId}
              className="w360-input"
              value={value}
              disabled={disabled}
              aria-describedby={statusId}
              onChange={(e) => onChange(e.target.value as SignInPreferenceValue)}
            >
              {product.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {overrideLabel(choice, name, defaultValue)}
                </option>
              ))}
            </select>
          </>
        ) : (
          <span className={styles.productName}>{name}</span>
        )}
        <p id={statusId} className={styles.muted}>
          {product.choices.length > 0 ? `Saved: ${whatHappens(product, name).toLowerCase()}.` : "Separate product sign-in required."}
        </p>
      </div>
    </li>
  );
}
