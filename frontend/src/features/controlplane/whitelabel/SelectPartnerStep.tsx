"use client";

import { useId, useState } from "react";
import { partnerConsoleApi, type ResellerRow } from "@/lib/api/partnerConsole";
import { ExperienceLabels } from "../labels";
import { MoreButton, Status, Table, useAction, usePaged } from "../ui";
import { StepFrame, type Nav } from "./shared";
import styles from "../ControlPlane.module.css";
import css from "../WhiteLabelSetup.module.css";

/**
 * Step 1: choose the partner this setup is for.
 *
 * The wizard configures an *existing* partner account. It never creates one and
 * never stores a kind of partner: the labels in each row ("Reseller",
 * "White-label", "Affiliate") are what the server derives from the capabilities
 * the partner holds, so a reseller, an affiliate or a partner with no
 * capabilities yet are all listed (scope "all") and all can be chosen - except a
 * terminated one, which takes nothing new.
 */
export function SelectPartnerStep({
  nav,
  current,
  onChoose,
}: {
  nav: Nav;
  /** The partner already chosen (from the address, or earlier in this setup). */
  current: string | null;
  onChoose: (partnerId: string) => void;
}) {
  const action = useAction();
  const searchId = useId();
  const group = useId();
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [pick, setPick] = useState<string | null>(current);
  const paged = usePaged(
    (cursor) => partnerConsoleApi.resellers({ scope: "all", q: query || undefined, cursor }),
    [query],
  );

  return (
    <StepFrame
      step="partner"
      nav={nav}
      action={action}
      canContinue={pick !== null}
      advance={() => pick && onChoose(pick)}
      intro={
        <p className={css.lead}>
          Choose the partner to set up. This works for an existing reseller, affiliate or any other partner: white-label is something a partner
          can do, not a kind of account, so nothing is created here.
          {current ? " A partner is already chosen: continue to keep it, or choose another." : ""}
        </p>
      }
    >
      <form
        className={css.searchRow}
        role="search"
        aria-label="Search partners"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(typed.trim());
        }}
      >
        <div>
          <label className="w360-label" htmlFor={searchId}>
            Search by partner name or code
          </label>
          <input id={searchId} className="w360-input" value={typed} onChange={(e) => setTyped(e.target.value)} maxLength={80} autoComplete="off" />
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>

      {paged.state === "loading" && <p aria-live="polite">Loading partners…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load partners.</p>}
      {paged.state === "ready" && (
        <fieldset className={css.group}>
          <legend>Partner</legend>
          <Table
            caption="Partners"
            head={["Partner", "Status", "Derived labels", "Products", "Customers"]}
            isEmpty={paged.rows.length === 0}
            empty="No partner matches that search."
          >
            {paged.rows.map((row) => (
              <PartnerRow key={row.id} row={row} name={group} picked={pick === row.id} onPick={() => setPick(row.id)} />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </fieldset>
      )}
    </StepFrame>
  );
}

function PartnerRow({ row, name, picked, onPick }: { row: ResellerRow; name: string; picked: boolean; onPick: () => void }) {
  const terminated = row.status === "terminated";
  const products = Array.from(new Set(row.products.map((p) => p.product_code)));
  return (
    <tr>
      <th scope="row">
        <label className={css.pick}>
          <input type="radio" name={name} value={row.id} checked={picked} disabled={terminated} onChange={onPick} />
          <span>{row.display_name}</span>
        </label>
        <div className={styles.muted}>{row.code}</div>
        {terminated && <div className={css.reason}>A terminated partner cannot be chosen.</div>}
      </th>
      <td>
        <Status value={row.status} />
      </td>
      <td>
        <ExperienceLabels labels={row.experience} />
      </td>
      <td>{products.length > 0 ? products.join(", ") : <span className={styles.muted}>None</span>}</td>
      <td className={styles.num}>{row.customers}</td>
    </tr>
  );
}
