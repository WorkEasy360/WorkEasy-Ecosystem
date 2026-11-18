"use client";

import { useState } from "react";
import { accountCenterApi, type ConnectedProduct } from "@/lib/api/console";
import { LoadGate, Table, useLoad, when } from "@/features/controlplane/ui";
import { Badge, Button, CopyButton, formatDateTime, useConfirm } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { CenterShell } from "./CenterShell";
import {
  assignmentLabel,
  connectionChips,
  holdingLabel,
  lastSeenLabel,
  lastSignOutLabel,
  productSignOutMessage,
  sessionLabel,
} from "./centerCopy";
import styles from "./Account.module.css";

function ProductCard({ product, onChanged }: { product: ConnectedProduct; onChanged: () => void }) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const session = sessionLabel(product);
  const signedOut = lastSignOutLabel(product);
  const signOut = () => {
    setBusy(true);
    setOutcome(null);
    accountCenterApi
      .signOutOfProduct(product.product_code)
      .then((r) => setOutcome(productSignOutMessage(product.name, r.status)))
      .catch(() => setOutcome(`Could not ask ${product.name} to sign you out. Nothing was changed; try again.`))
      .finally(() => {
        setBusy(false);
        onChanged();
      });
  };
  return (
    <Panel
      title={product.name}
      description={
        product.domain.host ? (
          <>
            {product.domain.host}{" "}
            {product.domain.verified ? <Badge tone="success">Verified domain</Badge> : <Badge tone="warning">Domain not verified</Badge>}
          </>
        ) : undefined
      }
    >
      {confirm.dialog}
      <ul className={styles.chips} aria-label={`What is known about ${product.name}`}>
        {connectionChips(product).map((c) => (
          <li key={c.label}>
            <Badge tone={c.tone}>{c.label}</Badge>
          </li>
        ))}
      </ul>
      <dl className={styles.facts}>
        <dt>Signs you in as</dt>
        <dd className={styles.mono}>{product.subject}</dd>
        <dt>Last signed in</dt>
        <dd>{product.last_sign_in_at ? formatDateTime(product.last_sign_in_at) : "Never with WorkEasy360"}</dd>
        <dt>Last activity</dt>
        <dd>{lastSeenLabel(product.last_activity_at)}</dd>
        <dt>Session</dt>
        <dd>
          {session.label}. <span className={styles.muted}>{session.detail}</span>
        </dd>
        {signedOut && (
          <>
            <dt>Last sign-out</dt>
            <dd>
              {signedOut.label} ({formatDateTime(signedOut.at)})
            </dd>
          </>
        )}
      </dl>
      <Table
        caption={`${product.name} in each of your organizations`}
        head={["Organization", "Access", "Connection", "Last signed in", "Last activity"]}
        isEmpty={product.organizations.length === 0}
        empty="No organization gives you this product."
      >
        {product.organizations.map((o) => {
          const holding = holdingLabel(o.access?.entitlement_status);
          const assigned = assignmentLabel(o.access);
          return (
            <tr key={o.organization_id}>
              <th scope="row">{o.member ? o.name : <span className={styles.muted}>An organization you have left</span>}</th>
              <td>
                <span className={styles.chips}>
                  {holding && <Badge tone={holding.tone}>{holding.label}</Badge>}
                  <Badge tone={assigned.tone}>{assigned.label}</Badge>
                </span>
              </td>
              <td>{o.connection ? `SSO connection established ${when(o.connection.first_sign_in_at)}` : "Not signed in yet"}</td>
              <td>{o.connection ? when(o.connection.last_sign_in_at) : "—"}</td>
              <td>{o.connection ? lastSeenLabel(o.connection.last_activity_at) : "—"}</td>
            </tr>
          );
        })}
      </Table>
      {product.can_sign_out && (
        <div>
          <Button
            icon="logout"
            loading={busy}
            onClick={() =>
              confirm.ask({
                title: `Sign out of ${product.name}?`,
                message: `${product.name} is told to end every session you have in it, on every device. You stay signed in to WorkEasy360 and its other products.`,
                confirmLabel: `Sign out of ${product.name}`,
                danger: true,
                onConfirm: signOut,
              })
            }
          >
            Sign out of this product
          </Button>
        </div>
      )}
      {outcome && <p role="status">{outcome}</p>}
    </Panel>
  );
}

/** Connected Logins: every product you can use or have signed in to with WorkEasy360. */
export function ConnectedLogins() {
  const [load, reload] = useLoad(() => accountCenterApi.connections(), []);
  return (
    <CenterShell
      current="connected-logins"
      title="Connected Logins"
      lead="Products you can use or have signed in to with this account. A product's own sign-out does not reach WorkEasy360, so a product session is never shown as active here: you see when you last signed in, when the product last checked your access, and whether the WorkEasy360 sign-in you opened it with is still open."
    >
      <LoadGate load={load} what="your connected logins">
        {(d) => (
          <>
            <p className={styles.subject}>
              Products know you by your WorkEasy360 ID <span className={styles.mono}>{d.subject}</span>{" "}
              <CopyButton value={d.subject} label="Copy WorkEasy360 ID" />
            </p>
            {d.products.length === 0 ? (
              <p>No product yet. Products you open from My Products appear here.</p>
            ) : (
              d.products.map((p) => <ProductCard key={p.product_code} product={p} onChanged={reload} />)
            )}
          </>
        )}
      </LoadGate>
    </CenterShell>
  );
}
