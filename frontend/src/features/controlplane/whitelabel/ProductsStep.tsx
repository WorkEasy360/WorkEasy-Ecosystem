"use client";

import { useState } from "react";
import Link from "next/link";
import { partnerConsoleApi, type WhiteLabelProductRow } from "@/lib/api/partnerConsole";
import { Status, Table, useAction } from "../ui";
import { StepFrame, yesNo, type Ctx } from "./shared";
import css from "../WhiteLabelSetup.module.css";

/**
 * Step 4: which products the partner's portal shows, and the facts around that.
 *
 * Every catalog product is a row - the list is the server's, never a list in this
 * file. "Visible in portal" is the one thing edited here, and it saves on its own
 * (``PATCH product-grant``). It only decides what customers are *shown* when they
 * browse products: it never grants or removes an entitlement. Granting a partner a
 * product is done on the partner's Products tab, not from here.
 *
 * "Sell allowed" and "Trial available" are worked out by the server from the
 * partner's capabilities, its product access and the platform's trial settings;
 * this table only displays them.
 */
export function ProductsStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const toggles = useAction();
  const { setup, partnerId } = ctx;
  // What a checkbox shows while its save is in flight; the server's value afterwards.
  const [pending, setPending] = useState<Record<string, boolean>>({});

  function setVisible(row: WhiteLabelProductRow, visible: boolean) {
    if (!row.grant_id) return;
    const grantId = row.grant_id;
    void toggles.act(async () => {
      setPending((p) => ({ ...p, [row.product_code]: visible }));
      try {
        await partnerConsoleApi.updateProductGrant(partnerId, grantId, { portal_visible: visible });
        await ctx.refresh();
      } finally {
        setPending((p) => {
          const rest = { ...p };
          delete rest[row.product_code];
          return rest;
        });
      }
    }, `${row.product_name} is now ${visible ? "shown in" : "hidden from"} the portal.`);
  }

  const busy = action.busy || toggles.busy;

  return (
    <StepFrame
      step="products"
      nav={ctx.nav}
      action={action}
      intro={
        <div className={css.callout}>
          <p>
            <strong>Visibility never grants access.</strong> Showing a product in the portal only decides what customers see when they browse
            products. A customer still needs a subscription or a trial to launch a product, and a product a customer already holds still
            launches even if it is hidden here.
          </p>
        </div>
      }
    >
      {toggles.feedback}
      {setup.products.length === 0 ? (
        <p>There are no products in the catalog yet.</p>
      ) : (
        <Table
          caption="Products in the catalog"
          head={["Product", "Available to partner", "Visible in portal", "Sell allowed", "Trial available"]}
        >
          {setup.products.map((row) => (
            <ProductRow
              key={row.product_code}
              row={row}
              partnerId={partnerId}
              checked={pending[row.product_code] ?? row.visible_in_portal}
              busy={busy}
              onToggle={(visible) => setVisible(row, visible)}
            />
          ))}
        </Table>
      )}
      <p className={css.reason}>
        Sell allowed and Trial available are worked out by Workeasy360 from the partner&apos;s capabilities, its product access and the
        platform&apos;s trial settings. Change them on the partner&apos;s page.
      </p>
    </StepFrame>
  );
}

/** Why a trial is, or is not, available - the server's three facts, in words. */
function trialWords(row: WhiteLabelProductRow): string {
  if (row.trial_available) return "Yes";
  if (!row.available_to_partner) return "No";
  if (!row.platform_trial_enabled) return "No: no platform trial is set up for this product";
  return "No: the partner has turned trials off";
}

function ProductRow({
  row,
  partnerId,
  checked,
  busy,
  onToggle,
}: {
  row: WhiteLabelProductRow;
  partnerId: string;
  checked: boolean;
  busy: boolean;
  onToggle: (visible: boolean) => void;
}) {
  return (
    <tr>
      <th scope="row">
        <div className={css.productCell}>
          <span>{row.product_name}</span>
          {row.status !== "available" && <Status value={row.status} />}
        </div>
      </th>
      <td>
        <div className={css.productCell}>
          <span>{yesNo(row.available_to_partner)}</span>
          {!row.available_to_partner && (
            <>
              <span className={css.reason}>Not allowed for this partner</span>
              <Link href={`/admin/partners/${partnerId}?tab=products`}>Open the partner&apos;s Products tab</Link>
            </>
          )}
        </div>
      </td>
      <td>
        {row.available_to_partner && row.grant_id ? (
          <label className={css.check}>
            <input type="checkbox" checked={checked} disabled={busy} onChange={(e) => onToggle(e.target.checked)} />
            <span>
              Visible <span className="w360-visually-hidden">in portal: {row.product_name}</span>
            </span>
          </label>
        ) : (
          "No"
        )}
      </td>
      <td>{yesNo(row.sell_allowed)}</td>
      <td>{trialWords(row)}</td>
    </tr>
  );
}
