"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { adminApi } from "@/lib/api/controlPlane";
import {
  usagePricingApi,
  walletsApi,
  type UsageEventRow,
  type WalletEntryRow,
  type WalletKind,
  type WalletRow,
} from "@/lib/api/adminPanel";
import { can, useAdmin } from "./ControlShell";
import { DialogHead, FormDialog, Modal, dialog } from "./Modal";
import { LoadGate, useAction, useLoad, usePaged } from "./ui";
import { SUPPORTED_CURRENCIES, toMajor, toMinor } from "./amounts";
import { TextField } from "./widgets";
import {
  Alert,
  Badge,
  Button,
  ButtonLink,
  Card,
  ConfirmDialog,
  DataTable,
  DescriptionList,
  EMPTY,
  EmptyState,
  FilterBar,
  IdCell,
  Page,
  PageHeader,
  SearchInput,
  Select,
  Stack,
  Tabs,
  Trunc,
  formatDateTime,
  formatMoney,
  formatNumber,
  sentence,
  useDebounced,
  useToast,
  type Column,
  type Tone,
} from "./kit";
import styles from "./AdminWallets.module.css";

/**
 * Admin → Wallets & Credits (2026-11-09). Exactly three spendable balances: the Platform
 * Wallet (customers and partners, money), AI Credits (customers, whole credits, never
 * money) and the Consumption Wallet (customers, money). What a partner earns is a record
 * elsewhere and never a balance here. Every movement names a command id made when its
 * dialog opens, so a double-click or a retry is one movement. The server decides; a
 * hidden button is only a courtesy.
 */

const MANAGE = "platform.staff_wallet.manage";

const KIND_LABEL: Record<WalletKind, string> = {
  platform: "Platform Wallet",
  ai_credits: "AI Credits",
  consumption: "Consumption Wallet",
};

const CURRENCY_OPTIONS = SUPPORTED_CURRENCIES.map((c) => ({ value: c, label: c }));
const DEFAULT_CURRENCY = "INR";
const REASON_MIN = 10;
const REASON_MAX = 255;

// --- values -------------------------------------------------------------------------

/** Whole credits, never money. */
export function creditsText(value: number): string {
  return `${formatNumber(value)} ${Math.abs(value) === 1 ? "credit" : "credits"}`;
}

const isCredits = (kind: WalletKind | undefined) => kind === "ai_credits";

function balanceText(value: number, kind: WalletKind | undefined, currency: string | undefined): string {
  return isCredits(kind) ? creditsText(value) : formatMoney(value, currency);
}

/** A typed whole number, or null. */
export function wholeNumber(text: string): number | null {
  const value = text.trim();
  return /^\d{1,12}$/.test(value) ? Number(value) : null;
}

/** What was typed as the integer the server stores: minor units, or whole credits. */
function parseAmount(text: string, kind: WalletKind | undefined, currency: string | undefined): number | null {
  return isCredits(kind) ? wholeNumber(text) : toMinor(text, currency ?? "");
}

/** The text for an input holding a stored amount. */
const amountInput = (value: number, kind: WalletKind, currency: string) => (isCredits(kind) ? String(value) : toMajor(value, currency));

const amountLabel = (word: string, kind: WalletKind | undefined, currency: string | undefined) =>
  isCredits(kind) ? `${word} (credits)` : `${word} (${currency})`;

const shortId = (id: string) => id.slice(0, 8);

const ownerHref = (ownerType: string | undefined, ownerId: string) =>
  ownerType === "partner" ? `/admin/partners/${ownerId}` : `/admin/customers/${ownerId}`;

export const reasonIsValid = (text: string) => text.trim().length >= REASON_MIN && text.trim().length <= REASON_MAX;

/** One command id per dialog, made when it opens. */
function useCommandId(): string {
  const [id] = useState(() => crypto.randomUUID());
  return id;
}

/** The table props for a server-paged list. */
function pagedProps<T>(paged: ReturnType<typeof usePaged<T>>, what: string) {
  return {
    loading: paged.state === "loading",
    error:
      paged.state === "forbidden" ? `You don't have access to ${what}.` : paged.state === "error" ? `Couldn't load ${what}. Try again in a moment.` : null,
    onLoadMore: () => void paged.more(),
    hasMore: paged.hasMore,
  };
}

// --- small pieces -------------------------------------------------------------------

/** A yes/no filter in a filter bar. */
export function ToggleFilter({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className={styles.toggle}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function OwnerLink({ type, id, name }: { type: string | undefined; id: string | undefined; name: string | undefined }) {
  if (!id) return <>{EMPTY}</>;
  return (
    <Link href={ownerHref(type, id)}>
      <Trunc>{name || id}</Trunc>
    </Link>
  );
}

function Balance({ wallet }: { wallet: WalletRow }) {
  return (
    <span className={styles.balance}>
      {balanceText(wallet.balance, wallet.kind, wallet.currency)}
      {wallet.is_low && <Badge tone="warning">Low</Badge>}
    </span>
  );
}

const thresholdText = (wallet: WalletRow) =>
  wallet.low_balance_threshold === null ? EMPTY : balanceText(wallet.low_balance_threshold, wallet.kind, wallet.currency);

/** A signed movement: the sign is written, the colour only reinforces it. */
function SignedAmount({ value, kind, currency }: { value: number; kind: WalletKind | undefined; currency: string | undefined }) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const text = `${sign}${balanceText(Math.abs(value), kind, currency)}`;
  return <span className={value < 0 ? styles.moneyOut : value > 0 ? styles.moneyIn : undefined}>{text}</span>;
}

export function ReasonField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const tooShort = value.trim() !== "" && value.trim().length < REASON_MIN;
  return (
    <div className={dialog.span}>
      <TextField
        label="Reason"
        required
        value={value}
        onChange={onChange}
        maxLength={REASON_MAX}
        autoComplete="off"
        hint="At least 10 characters. Kept in the audit log."
        error={tooShort ? "Say why, in at least 10 characters." : undefined}
      />
    </div>
  );
}

// --- choosing an owner --------------------------------------------------------------

type OwnerType = "organization" | "partner";

interface OwnerOption {
  id: string;
  name: string;
  hint: string;
}

const MAX_OWNER_OPTIONS = 8;

async function searchOwners(ownerType: OwnerType, term: string): Promise<OwnerOption[]> {
  if (!term) return [];
  if (ownerType === "partner") {
    const page = await adminApi.partners({ q: term, limit: MAX_OWNER_OPTIONS });
    return page.results.map((p) => ({ id: p.id, name: p.display_name, hint: p.contact_email || p.code }));
  }
  const page = await adminApi.customers({ q: term });
  return page.results.slice(0, MAX_OWNER_OPTIONS).map((c) => ({ id: c.organization_id, name: c.display_name, hint: c.owner_email }));
}

/** Find a customer or a partner by name on the server, then pick one. */
function OwnerPicker({ ownerType, value, onChange }: { ownerType: OwnerType; value: OwnerOption | null; onChange: (owner: OwnerOption | null) => void }) {
  const [term, setTerm] = useState("");
  const settled = useDebounced(term.trim());
  const [load] = useLoad(() => searchOwners(ownerType, settled), [ownerType, settled]);
  const noun = ownerType === "partner" ? "partner" : "customer";

  if (value) {
    return (
      <div className={`${dialog.span} ${styles.chosenOwner}`}>
        <span>
          {sentence(noun)}: <strong>{value.name}</strong>
        </span>
        <Button size="sm" variant="link" onClick={() => onChange(null)}>
          Change {noun}
        </Button>
      </div>
    );
  }
  return (
    <div className={dialog.span}>
      <TextField label={`Search ${noun}s by name`} value={term} onChange={setTerm} maxLength={80} autoComplete="off" />
      {settled && (
        <LoadGate load={load} what={`${noun}s`}>
          {(options) =>
            options.length === 0 ? (
              <p className={styles.note} role="status">
                No {noun} matches that name.
              </p>
            ) : (
              <ul className={styles.ownerList} aria-label={`Matching ${noun}s`}>
                {options.map((o) => (
                  <li key={o.id}>
                    <button type="button" className={styles.ownerOption} onClick={() => onChange(o)}>
                      <span>{o.name}</span>
                      {o.hint && <span className={styles.ownerHint}>{o.hint}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      )}
    </div>
  );
}

// --- commands on one wallet ---------------------------------------------------------

interface WalletActionProps {
  wallet: WalletRow;
  onClose: () => void;
  onDone: () => void;
}

/** Money that already arrived outside the platform. Platform and Consumption only:
 * AI Credits come from credit packs. */
function TopUpDialog({ wallet, onClose, onDone }: WalletActionProps) {
  const commandId = useCommandId();
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const minor = parseAmount(amount, wallet.kind, wallet.currency);
  const amountBad = amount.trim() !== "" && (minor === null || minor <= 0);
  const ready = minor !== null && minor > 0 && reference.trim() !== "" && reasonIsValid(reason);
  return (
    <FormDialog
      title="Record offline top-up"
      intro={`Money already received outside the platform, such as a bank transfer. It reaches this ${wallet.kind_label || KIND_LABEL[wallet.kind]} at once.`}
      submitLabel="Record top-up"
      canSubmit={ready}
      onSubmit={async () => {
        await walletsApi.recordTopUp(wallet.id, { amount: minor as number, reference: reference.trim(), reason: reason.trim() }, commandId);
        return `Top-up of ${formatMoney(minor, wallet.currency)} recorded.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <TextField
        label={amountLabel("Amount received", wallet.kind, wallet.currency)}
        required
        inputMode="decimal"
        autoComplete="off"
        value={amount}
        onChange={setAmount}
        error={amountBad ? "Enter an amount above zero, like 1250.50." : undefined}
      />
      <TextField label="Payment reference" required maxLength={64} autoComplete="off" value={reference} onChange={setReference} hint="The bank transfer id." />
      <ReasonField value={reason} onChange={setReason} />
    </FormDialog>
  );
}

/** "-250.50" or "+250.50" as signed minor units (or whole credits); null when it is not
 * an amount other than zero. */
function parseSigned(text: string, wallet: WalletRow): number | null {
  const value = text.trim();
  const negative = value.startsWith("-") || value.startsWith("−");
  const magnitude = parseAmount(value.replace(/^[-+−]\s*/, ""), wallet.kind, wallet.currency);
  if (magnitude === null || magnitude === 0) return null;
  return negative ? -magnitude : magnitude;
}

function AdjustDialog({ wallet, onClose, onDone }: WalletActionProps) {
  const commandId = useCommandId();
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  // FormDialog closes itself when its submit resolves. A decrease resolves by asking
  // first, so that close must not end the dialog; Cancel on the question goes back to
  // the form with what was typed.
  const asking = useRef(false);
  const finished = useRef(false);
  const value = parseSigned(amount, wallet);
  const credits = isCredits(wallet.kind);
  const shown = value === null ? "" : balanceText(Math.abs(value), wallet.kind, wallet.currency);
  const label = wallet.kind_label || KIND_LABEL[wallet.kind];
  const send = () => walletsApi.adjust(wallet.id, { amount: value as number, reason: reason.trim() }, commandId);

  if (confirming && value !== null) {
    return (
      <ConfirmDialog
        options={{
          title: `Decrease this balance by ${shown}?`,
          message: `${shown} comes out of ${wallet.owner_name || "this owner"}'s ${label} at once. The server refuses it if the wallet holds less.`,
          confirmLabel: "Decrease balance",
          danger: true,
          onConfirm: async () => {
            await send();
            finished.current = true;
            onDone();
            return `Balance decreased by ${shown}.`;
          },
        }}
        onClose={() => {
          if (finished.current) onClose();
          else setConfirming(false);
        }}
      />
    );
  }

  return (
    <FormDialog
      title="Manual adjustment"
      intro="An audited correction, up or down. Use a minus sign to decrease the balance."
      size="sm"
      submitLabel="Apply adjustment"
      canSubmit={value !== null && reasonIsValid(reason)}
      onSubmit={async () => {
        if (value !== null && value < 0) {
          asking.current = true;
          setConfirming(true);
          return;
        }
        await send();
        return `Balance increased by ${shown}.`;
      }}
      onDone={(message) => {
        if (!message) return;
        toast.success(message);
        onDone();
      }}
      onClose={() => {
        if (asking.current) asking.current = false;
        else onClose();
      }}
    >
      <TextField
        label={amountLabel("Amount", wallet.kind, wallet.currency)}
        required
        autoComplete="off"
        value={amount}
        onChange={setAmount}
        hint={credits ? "A whole number of credits, like 50 or -50." : "Like 250.50 or -250.50."}
        error={amount.trim() !== "" && value === null ? "Enter an amount other than zero, with a minus sign to decrease." : undefined}
      />
      <ReasonField value={reason} onChange={setReason} />
    </FormDialog>
  );
}

function ThresholdDialog({ wallet, onClose, onDone }: WalletActionProps) {
  const toast = useToast();
  const [amount, setAmount] = useState(() =>
    wallet.low_balance_threshold === null ? "" : amountInput(wallet.low_balance_threshold, wallet.kind, wallet.currency),
  );
  const blank = amount.trim() === "";
  const parsed = blank ? null : parseAmount(amount, wallet.kind, wallet.currency);
  const invalid = !blank && parsed === null;
  return (
    <FormDialog
      title="Set low-balance threshold"
      intro="The wallet is marked Low when its balance is under this. Leave it blank for no threshold."
      size="sm"
      submitLabel="Save threshold"
      canSubmit={!invalid}
      onSubmit={async () => {
        await walletsApi.setThreshold(wallet.id, parsed);
        return parsed === null ? "Low-balance threshold removed." : `Low-balance threshold set to ${balanceText(parsed, wallet.kind, wallet.currency)}.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <TextField
        label={amountLabel("Low-balance threshold", wallet.kind, wallet.currency)}
        autoComplete="off"
        value={amount}
        onChange={setAmount}
        error={invalid ? (isCredits(wallet.kind) ? "Enter a whole number of credits." : "Enter an amount, like 500.00.") : undefined}
      />
    </FormDialog>
  );
}

// --- one wallet's history and actions ---------------------------------------------

type WalletAction = "top_up" | "adjust" | "threshold";

/** The wallet's ledger and its actions. A command opens in place of this dialog and
 * comes back to it, so two dialogs are never stacked. */
function WalletDetails({ wallet, manage, onClose, onChanged }: { wallet: WalletRow; manage: boolean; onClose: () => void; onChanged: () => void }) {
  const [action, setAction] = useState<WalletAction | null>(null);
  const [fresh, refresh] = useLoad(() => walletsApi.get(wallet.id), [wallet.id]);
  const entries = usePaged((cursor) => walletsApi.entries({ wallet: wallet.id, cursor }), [wallet.id]);
  const titleId = useId();
  const body = useRef<HTMLDivElement | null>(null);
  const current = fresh.status === "ready" ? fresh.data : wallet;
  const credits = isCredits(current.kind);
  const label = current.kind_label || KIND_LABEL[current.kind];

  // The dialog opens with no field to focus, so focus goes to its body.
  useEffect(() => {
    if (!action) body.current?.focus();
  }, [action]);

  const back = () => setAction(null);
  const changed = () => {
    refresh();
    entries.reload();
    onChanged();
  };

  if (action === "top_up") return <TopUpDialog wallet={current} onClose={back} onDone={changed} />;
  if (action === "adjust") return <AdjustDialog wallet={current} onClose={back} onDone={changed} />;
  if (action === "threshold") return <ThresholdDialog wallet={current} onClose={back} onDone={changed} />;

  return (
    <Modal labelledBy={titleId} onClose={onClose} size="lg">
      <DialogHead titleId={titleId} title={`${current.owner_name || "Wallet"}: ${label}`} onClose={onClose} />
      <div ref={body} tabIndex={-1} className={`${dialog.body} ${styles.panel}`}>
        <DescriptionList
          columns={2}
          items={[
            { label: "Owner", value: <OwnerLink type={current.owner_type} id={current.owner_id} name={current.owner_name} /> },
            { label: "Balance", value: <Balance wallet={current} /> },
            { label: "Currency", value: current.currency, when: !credits },
            { label: "Low-balance threshold", value: thresholdText(current) },
          ]}
        />
        {manage && (
          <div className={styles.actions}>
            {!credits && (
              <Button aria-haspopup="dialog" onClick={() => setAction("top_up")}>
                Record offline top-up
              </Button>
            )}
            <Button aria-haspopup="dialog" onClick={() => setAction("adjust")}>
              Manual adjustment
            </Button>
            <Button aria-haspopup="dialog" onClick={() => setAction("threshold")}>
              Set low-balance threshold
            </Button>
          </div>
        )}
        {credits && <p className={styles.note}>AI Credits come from credit packs, so they have no offline top-up.</p>}
        <DataTable
          caption="This wallet's transactions"
          columns={entryColumns({ fallback: current })}
          rows={entries.rows}
          rowKey={(e) => e.id}
          stickyFirst={false}
          {...pagedProps(entries, "this wallet's transactions")}
          empty={<EmptyState icon="history" title="No transactions yet" hint="Every movement of this balance appears here." compact />}
        />
      </div>
      <div className={dialog.foot}>
        <Button onClick={onClose}>Done</Button>
      </div>
    </Modal>
  );
}

// --- entries ------------------------------------------------------------------------

/** `reverses` names the original entry. A link when that entry is in the list. */
function ReversalCell({ entry, loaded }: { entry: WalletEntryRow; loaded?: Set<string> }) {
  if (!entry.reverses) return <>{EMPTY}</>;
  const name = `Reverses entry ${shortId(entry.reverses)}`;
  return loaded?.has(entry.reverses) ? (
    <a href={`#entry-${entry.reverses}`} title={entry.reverses}>
      {name}
    </a>
  ) : (
    <span title={entry.reverses}>{name} (not in this list)</span>
  );
}

function entryColumns({
  fallback,
  extra = [],
  loaded,
}: {
  /** The wallet the entries belong to, for rows that do not name their own kind. */
  fallback?: WalletRow;
  /** Columns after the first. */
  extra?: Column<WalletEntryRow>[];
  loaded?: Set<string>;
}): Column<WalletEntryRow>[] {
  const kindOf = (e: WalletEntryRow) => e.wallet_kind ?? fallback?.kind;
  const currencyOf = (e: WalletEntryRow) => e.currency ?? fallback?.currency;
  return [
    {
      key: "when",
      header: "When",
      value: (e) => e.created_at,
      render: (e) => <Stack main={<span id={`entry-${e.id}`}>{formatDateTime(e.created_at)}</span>} sub={`Entry ${shortId(e.id)}`} />,
    },
    ...extra,
    { key: "type", header: "Type", value: (e) => e.type_label, render: (e) => e.type_label },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (e) => e.amount,
      render: (e) => <SignedAmount value={e.amount} kind={kindOf(e)} currency={currencyOf(e)} />,
    },
    {
      key: "after",
      header: "Balance after",
      align: "right",
      value: (e) => e.balance_after,
      render: (e) => balanceText(e.balance_after, kindOf(e), currencyOf(e)),
    },
    { key: "reason", header: "Reason", value: (e) => e.reason, render: (e) => <Trunc max="md">{e.reason}</Trunc> },
    {
      key: "source",
      header: "Source",
      value: (e) => e.source_type,
      render: (e) => <Stack main={sentence(e.source_type)} sub={e.source_id ? <Trunc max="sm">{e.source_id}</Trunc> : undefined} />,
    },
    { key: "reversal", header: "Reversal", value: (e) => e.reverses, render: (e) => <ReversalCell entry={e} loaded={loaded} /> },
  ];
}

// --- the shared list ----------------------------------------------------------------

const OWNER_FILTER = [
  { value: "", label: "All owners" },
  { value: "organization", label: "Customers" },
  { value: "partner", label: "Partners" },
];

interface PlacedOrder {
  number: string;
  customerId: string;
  customerName: string;
  amountMinor: number;
  currency: string;
}

/** One wallet kind's list, its filters, and the page's actions. */
export function WalletsList({
  kind,
  title,
  subtitle,
  info,
  ownerFilter = false,
  initialLow = false,
  initialOwnerType = "",
  initialQuery = "",
  links,
}: {
  kind: WalletKind;
  title: string;
  subtitle: string;
  info: React.ReactNode;
  /** Only the Platform Wallet is held by partners too. */
  ownerFilter?: boolean;
  initialLow?: boolean;
  /** ADR-063: "View Partner Wallet" from the partner list opens one partner's wallet. */
  initialOwnerType?: string;
  initialQuery?: string;
  links?: React.ReactNode;
}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, MANAGE);
  const [ownerType, setOwnerType] = useState(initialOwnerType);
  const [query, setQuery] = useState(initialQuery);
  const [low, setLow] = useState(initialLow);
  const [chosen, setChosen] = useState<WalletRow | null>(null);
  const [form, setForm] = useState<"open" | "order" | null>(null);
  const [placed, setPlaced] = useState<PlacedOrder | null>(null);
  const paged = usePaged(
    (cursor) => walletsApi.list({ kind, owner_type: ownerType || undefined, q: query || undefined, low: low ? "1" : undefined, cursor }),
    [kind, ownerType, query, low],
  );
  const credits = isCredits(kind);
  const filtered = Boolean(ownerType || query || low);

  const columns: Column<WalletRow>[] = [
    {
      key: "owner",
      header: "Owner",
      value: (w) => w.owner_name,
      render: (w) => <OwnerLink type={w.owner_type} id={w.owner_id} name={w.owner_name} />,
    },
    {
      key: "type",
      header: "Type",
      value: (w) => w.owner_type,
      render: (w) => <Badge tone={w.owner_type === "partner" ? "brand" : "neutral"}>{w.owner_type === "partner" ? "Partner" : "Customer"}</Badge>,
    },
    ...(credits ? [] : ([{ key: "currency", header: "Currency", value: (w) => w.currency, render: (w) => w.currency }] as Column<WalletRow>[])),
    { key: "balance", header: "Balance", align: "right", value: (w) => w.balance, render: (w) => <Balance wallet={w} /> },
    {
      key: "threshold",
      header: "Low-balance threshold",
      align: "right",
      value: (w) => w.low_balance_threshold,
      render: thresholdText,
    },
    { key: "updated", header: "Updated", value: (w) => w.updated_at, render: (w) => formatDateTime(w.updated_at) },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      hideable: false,
      render: (w) => (
        <Button size="sm" aria-haspopup="dialog" aria-label={`${manage ? "History and actions" : "History"} for ${w.owner_name || "this owner"}'s wallet`} onClick={() => setChosen(w)}>
          {manage ? "History and actions" : "History"}
        </Button>
      ),
    },
  ];

  const clearFilters = () => {
    setOwnerType("");
    setQuery("");
    setLow(false);
  };

  return (
    <Page>
      <PageHeader
        title={title}
        subtitle={subtitle}
        info={info}
        actions={
          manage ? (
            <Button icon="wallet" aria-haspopup="dialog" onClick={() => setForm("open")}>
              Open wallet
            </Button>
          ) : undefined
        }
        primary={
          manage ? (
            <Button variant="primary" icon={credits ? "cart" : "receipt"} aria-haspopup="dialog" onClick={() => setForm("order")}>
              {credits ? "Sell credit pack" : "New top-up order"}
            </Button>
          ) : undefined
        }
      />
      {links}
      {placed && (
        <Alert
          tone="success"
          title={`Order ${placed.number} placed for ${placed.customerName}`}
          actions={<ButtonLink href={`/admin/customers/${placed.customerId}`}>Open billing page</ButtonLink>}
        >
          {formatMoney(placed.amountMinor, placed.currency)} is due. Its invoice is now open and is paid from the customer&apos;s billing page. The wallet is credited only when it is paid.
        </Alert>
      )}
      <FilterBar onClear={filtered ? clearFilters : undefined}>
        <SearchInput label="Search by owner name" value={query} onSearch={setQuery} maxLength={80} />
        {ownerFilter && <Select label="Owner type" prefix="Owner:" value={ownerType} onChange={setOwnerType} options={OWNER_FILTER} />}
        <ToggleFilter label="Low balance only" checked={low} onChange={setLow} />
      </FilterBar>
      <DataTable
        caption={`${title} wallets`}
        columns={columns}
        rows={paged.rows}
        rowKey={(w) => w.id}
        hideEmptyColumns={false}
        {...pagedProps(paged, "these wallets")}
        empty={
          <EmptyState
            icon="wallet"
            title={filtered ? "No wallets match" : `No ${title} wallets yet`}
            hint={filtered ? "Try another name or clear the filters." : "A wallet appears here once it is opened or first paid into."}
            action={
              manage && !filtered ? (
                <Button variant="primary" icon="wallet" aria-haspopup="dialog" onClick={() => setForm("open")}>
                  Open the first wallet
                </Button>
              ) : undefined
            }
          />
        }
      />
      {chosen && <WalletDetails wallet={chosen} manage={manage} onClose={() => setChosen(null)} onChanged={paged.reload} />}
      {form === "open" && <OpenWalletDialog defaultKind={kind} onClose={() => setForm(null)} onDone={paged.reload} />}
      {form === "order" && !credits && <TopUpOrderDialog defaultKind={kind === "consumption" ? "consumption" : "platform"} onClose={() => setForm(null)} onPlaced={setPlaced} />}
      {form === "order" && credits && <PackOrderDialog onClose={() => setForm(null)} onPlaced={setPlaced} />}
    </Page>
  );
}

// --- page-level commands -------------------------------------------------------------

const KIND_OPTIONS: { value: WalletKind; label: string }[] = [
  { value: "platform", label: KIND_LABEL.platform },
  { value: "ai_credits", label: KIND_LABEL.ai_credits },
  { value: "consumption", label: KIND_LABEL.consumption },
];

const OWNER_TYPE_OPTIONS: { value: OwnerType; label: string }[] = [
  { value: "organization", label: "Customer" },
  { value: "partner", label: "Partner" },
];

function OpenWalletDialog({ defaultKind, onClose, onDone }: { defaultKind: WalletKind; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [ownerType, setOwnerType] = useState<OwnerType>("organization");
  const [owner, setOwner] = useState<OwnerOption | null>(null);
  const [chosenKind, setChosenKind] = useState<WalletKind>(defaultKind);
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);
  const isPartner = ownerType === "partner";
  const kind: WalletKind = isPartner ? "platform" : chosenKind;
  return (
    <FormDialog
      title="Open wallet"
      intro="A new wallet starts empty. Opening one the owner already holds changes nothing."
      submitLabel="Open wallet"
      canSubmit={owner !== null}
      onSubmit={async () => {
        if (!owner) return;
        await walletsApi.open({ owner_type: ownerType, owner_id: owner.id, kind, currency: isCredits(kind) ? undefined : currency });
        return `${KIND_LABEL[kind]} opened for ${owner.name}.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <div className={dialog.span}>
        <Select
          label="Owner type"
          showLabel
          fullWidth
          value={ownerType}
          onChange={(next) => {
            setOwnerType(next);
            setOwner(null);
          }}
          options={OWNER_TYPE_OPTIONS}
        />
      </div>
      <OwnerPicker ownerType={ownerType} value={owner} onChange={setOwner} />
      <Select label="Wallet" showLabel fullWidth disabled={isPartner} value={kind} onChange={setChosenKind} options={KIND_OPTIONS} />
      {!isCredits(kind) && <Select label="Currency" showLabel fullWidth value={currency} onChange={setCurrency} options={CURRENCY_OPTIONS} />}
      {isPartner && (
        <p className={`${dialog.span} ${styles.note}`}>A partner holds a Platform Wallet only, because commissions are earnings, not a balance.</p>
      )}
    </FormDialog>
  );
}

type TopUpKind = "platform" | "consumption";

const TOP_UP_OPTIONS: { value: TopUpKind; label: string }[] = [
  { value: "platform", label: KIND_LABEL.platform },
  { value: "consumption", label: KIND_LABEL.consumption },
];

function TopUpOrderDialog({ defaultKind, onClose, onPlaced }: { defaultKind: TopUpKind; onClose: () => void; onPlaced: (order: PlacedOrder) => void }) {
  const commandId = useCommandId();
  const [customer, setCustomer] = useState<OwnerOption | null>(null);
  const [kind, setKind] = useState<TopUpKind>(defaultKind);
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);
  const minor = toMinor(amount, currency);
  const amountBad = amount.trim() !== "" && (minor === null || minor <= 0);
  return (
    <FormDialog
      title="New top-up order"
      intro="This places an order and opens its invoice. Nothing reaches the wallet until that invoice is paid, from the customer's billing page."
      submitLabel="Place order"
      canSubmit={customer !== null && minor !== null && minor > 0}
      onSubmit={async () => {
        if (!customer || minor === null) return;
        const order = await walletsApi.placeOrder(customer.id, { wallet_kind: kind, amount_minor: minor, currency }, commandId);
        onPlaced({ number: order.number, customerId: customer.id, customerName: customer.name, amountMinor: order.amount_due_minor, currency: order.currency });
      }}
      onClose={onClose}
    >
      <OwnerPicker ownerType="organization" value={customer} onChange={setCustomer} />
      <div className={dialog.span}>
        <Select label="Wallet" showLabel fullWidth value={kind} onChange={setKind} options={TOP_UP_OPTIONS} />
      </div>
      <TextField
        label={`Amount (${currency})`}
        required
        inputMode="decimal"
        autoComplete="off"
        value={amount}
        onChange={setAmount}
        error={amountBad ? "Enter an amount above zero, like 1250.50." : undefined}
      />
      <Select label="Currency" showLabel fullWidth value={currency} onChange={setCurrency} options={CURRENCY_OPTIONS} />
    </FormDialog>
  );
}

function PackOrderDialog({ onClose, onPlaced }: { onClose: () => void; onPlaced: (order: PlacedOrder) => void }) {
  const commandId = useCommandId();
  const [customer, setCustomer] = useState<OwnerOption | null>(null);
  const [packCode, setPackCode] = useState("");
  const [load] = useLoad(() => usagePricingApi.packs(), []);
  return (
    <FormDialog
      title="Sell credit pack"
      intro="This places an order and opens its invoice. The credits arrive when that invoice is paid, from the customer's billing page."
      submitLabel="Place order"
      canSubmit={customer !== null && packCode !== ""}
      onSubmit={async () => {
        if (!customer) return;
        const order = await walletsApi.placeOrder(customer.id, { pack_code: packCode }, commandId);
        onPlaced({ number: order.number, customerId: customer.id, customerName: customer.name, amountMinor: order.amount_due_minor, currency: order.currency });
      }}
      onClose={onClose}
    >
      <OwnerPicker ownerType="organization" value={customer} onChange={setCustomer} />
      <div className={dialog.span}>
        <LoadGate load={load} what="credit packs">
          {({ results }) => {
            const active = results.filter((p) => p.status === "active");
            if (active.length === 0) {
              return (
                <p className={styles.note}>
                  No credit pack is on sale. <Link href="/admin/usage-pricing/credit-packs">Add one under Credit packs</Link>.
                </p>
              );
            }
            return (
              <Select
                label="Credit pack"
                showLabel
                fullWidth
                value={packCode}
                onChange={setPackCode}
                options={active.map((p) => ({ value: p.code, label: `${p.name}: ${creditsText(p.credits)} for ${formatMoney(p.price_minor, p.currency)}` }))}
              />
            );
          }}
        </LoadGate>
      </div>
    </FormDialog>
  );
}

// --- the three balances --------------------------------------------------------------

export function PlatformWalletScreen({
  initialLow = false,
  initialOwnerType = "",
  initialQuery = "",
}: { initialLow?: boolean; initialOwnerType?: string; initialQuery?: string } = {}) {
  return (
    <WalletsList
      kind="platform"
      title="Platform Wallet"
      subtitle="Money customers and partners hold on the platform."
      info="A customer's top-up arrives by paid order or offline payment. A partner's arrives online from its partner panel (Add Money) or by an offline payment recorded here, and pays for its customers' plans at its purchase price. What a partner earns is recorded separately and never adds to this balance."
      ownerFilter
      initialLow={initialLow}
      initialOwnerType={initialOwnerType}
      initialQuery={initialQuery}
    />
  );
}

export function AiCreditsScreen() {
  return (
    <WalletsList
      kind="ai_credits"
      title="AI Credits"
      subtitle="Whole credits customers hold for AI usage. Credits are never money."
      info="Credits arrive when a credit pack's invoice is paid, and AI usage takes them out."
      links={
        <div className={styles.links}>
          <ButtonLink size="sm" icon="receipt" href="/admin/wallets/transactions?kind=ai_credits&type=credit_purchase">
            Pack purchases
          </ButtonLink>
          <ButtonLink size="sm" icon="bolt" href="/admin/wallets/transactions?tab=events&meter=ai_tokens">
            Usage events
          </ButtonLink>
        </div>
      }
    />
  );
}

export function ConsumptionWalletScreen() {
  return (
    <WalletsList
      kind="consumption"
      title="Consumption Wallet"
      subtitle="Money customers hold to pay for metered usage."
      info="Messages, API triggers and webhooks are charged here at the rates set under Usage rates. A meter with no rate is not charged."
    />
  );
}

// --- transaction controls ----------------------------------------------------------

const KIND_FILTER = [{ value: "", label: "All wallets" }, ...KIND_OPTIONS];

const ENTRY_TYPES = [
  { value: "", label: "All types" },
  { value: "top_up", label: "Top-up" },
  { value: "credit_purchase", label: "Credit pack purchase" },
  { value: "subscription", label: "Subscription deduction" },
  { value: "usage", label: "Usage charge" },
  { value: "refund", label: "Refund" },
  { value: "adjustment", label: "Manual adjustment" },
  { value: "reversal", label: "Reversal" },
];

const OUTCOME: Record<UsageEventRow["outcome"], { label: string; tone: Tone }> = {
  charged: { label: "Charged", tone: "success" },
  included: { label: "Within the free allowance", tone: "info" },
  not_rated: { label: "No rate set", tone: "neutral" },
  failed: { label: "Failed", tone: "danger" },
};

const OUTCOME_FILTER = [
  { value: "", label: "All outcomes" },
  { value: "charged", label: OUTCOME.charged.label },
  { value: "included", label: OUTCOME.included.label },
  { value: "not_rated", label: OUTCOME.not_rated.label },
  { value: "failed", label: OUTCOME.failed.label },
];

const METER_FILTER = [
  { value: "", label: "All meters" },
  { value: "ai_tokens", label: "AI tokens" },
  { value: "whatsapp", label: "WhatsApp messages" },
  { value: "sms", label: "SMS messages" },
  { value: "email", label: "Emails" },
  { value: "api_trigger", label: "Billable API triggers" },
  { value: "webhook", label: "Webhook deliveries" },
];

/** A value from the address, only if the filter offers it. */
const offered = (options: { value: string }[], value: string | undefined) => (options.some((o) => o.value === value) ? (value as string) : "");

type ControlTab = "transactions" | "events";

export function TransactionControlsScreen({
  initialTab = "transactions",
  initialKind,
  initialType,
  initialMeter,
}: {
  initialTab?: ControlTab;
  initialKind?: string;
  initialType?: string;
  initialMeter?: string;
} = {}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, MANAGE);
  const [tab, setTab] = useState<ControlTab>(initialTab);
  return (
    <Page>
      <PageHeader
        title="Transaction Controls"
        subtitle="Every wallet movement and every usage event, with the means to correct them."
        info="A reversal adds an opposite entry that names the original; nothing is ever edited or deleted."
      />
      <ReconciliationCard />
      <Tabs
        label="Transaction controls"
        tabs={[
          { key: "transactions", label: "Transactions" },
          { key: "events", label: "Usage events" },
        ]}
        selected={tab}
        onSelect={setTab}
      >
        {tab === "transactions" ? (
          <TransactionsTab manage={manage} initialKind={offered(KIND_FILTER, initialKind)} initialType={offered(ENTRY_TYPES, initialType)} />
        ) : (
          <UsageEventsTab manage={manage} initialMeter={offered(METER_FILTER, initialMeter)} />
        )}
      </Tabs>
    </Page>
  );
}

function ReconciliationCard() {
  const [load, reload] = useLoad(() => walletsApi.reconcile(), []);
  return (
    <Card
      title="Reconciliation"
      description="Each wallet's balance compared with the sum of its ledger entries."
      actions={
        <Button size="sm" icon="refresh" onClick={reload}>
          Check again
        </Button>
      }
    >
      <LoadGate load={load} what="the reconciliation check">
        {(result) =>
          result.balanced ? (
            <Alert tone="success">Every balance equals its ledger.</Alert>
          ) : (
            <div className={styles.stack}>
              <Alert tone="danger" title={`${formatNumber(result.mismatches.length)} ${result.mismatches.length === 1 ? "wallet does" : "wallets do"} not match the ledger`}>
                The figures below are as stored: minor units of the wallet&apos;s currency, or whole credits. Nothing has been corrected automatically.
              </Alert>
              <DataTable
                caption="Wallets whose balance differs from their ledger"
                columns={[
                  { key: "wallet", header: "Wallet", render: (m) => <IdCell value={m.wallet_id} label="Copy wallet ID" /> },
                  { key: "balance", header: "Balance", align: "right", value: (m) => m.balance, render: (m) => formatNumber(m.balance) },
                  { key: "ledger", header: "Ledger total", align: "right", value: (m) => m.ledger_total, render: (m) => formatNumber(m.ledger_total) },
                ]}
                rows={result.mismatches}
                rowKey={(m) => m.wallet_id}
                pageSize={0}
                maxHeight={false}
                stickyFirst={false}
              />
            </div>
          )
        }
      </LoadGate>
    </Card>
  );
}

function TransactionsTab({ manage, initialKind, initialType }: { manage: boolean; initialKind: string; initialType: string }) {
  const [kind, setKind] = useState(initialKind);
  const [type, setType] = useState(initialType);
  const [reversing, setReversing] = useState<WalletEntryRow | null>(null);
  const paged = usePaged((cursor) => walletsApi.entries({ kind: (kind || undefined) as WalletKind | undefined, type: type || undefined, cursor }), [kind, type]);
  const loaded = useMemo(() => new Set(paged.rows.map((e) => e.id)), [paged.rows]);
  const filtered = Boolean(kind || type);

  const columns = [
    ...entryColumns({
      loaded,
      extra: [
        {
          key: "wallet",
          header: "Wallet",
          value: (e) => e.wallet_kind,
          render: (e) => (e.wallet_kind ? KIND_LABEL[e.wallet_kind] : EMPTY),
        },
        {
          key: "owner",
          header: "Owner",
          value: (e) => e.owner_name,
          render: (e) => <OwnerLink type={e.owner_type} id={e.owner_id} name={e.owner_name} />,
        },
      ],
    }),
    ...(manage
      ? ([
          {
            key: "actions",
            header: "Actions",
            align: "right",
            hideable: false,
            render: (e) =>
              e.reverses ? (
                EMPTY
              ) : (
                <Button size="sm" aria-haspopup="dialog" aria-label={`Reverse entry ${shortId(e.id)}`} onClick={() => setReversing(e)}>
                  Reverse
                </Button>
              ),
          },
        ] as Column<WalletEntryRow>[])
      : []),
  ];

  return (
    <div className={styles.stack}>
      <FilterBar
        label="Transaction filters"
        onClear={
          filtered
            ? () => {
                setKind("");
                setType("");
              }
            : undefined
        }
      >
        <Select label="Wallet" prefix="Wallet:" value={kind} onChange={setKind} options={KIND_FILTER} />
        <Select label="Type" prefix="Type:" value={type} onChange={setType} options={ENTRY_TYPES} />
      </FilterBar>
      <DataTable
        caption="Wallet transactions"
        columns={columns}
        rows={paged.rows}
        rowKey={(e) => e.id}
        {...pagedProps(paged, "transactions")}
        empty={
          <EmptyState
            icon="history"
            title={filtered ? "No transactions match" : "No transactions yet"}
            hint={filtered ? "Try another wallet or type, or clear the filters." : "Every wallet movement appears here."}
          />
        }
      />
      {reversing && <ReverseDialog entry={reversing} onClose={() => setReversing(null)} onDone={paged.reload} />}
    </div>
  );
}

function ReverseDialog({ entry, onClose, onDone }: { entry: WalletEntryRow; onClose: () => void; onDone: () => void }) {
  const commandId = useCommandId();
  const toast = useToast();
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const blank = amount.trim() === "";
  const parsed = blank ? null : parseAmount(amount, entry.wallet_kind, entry.currency);
  const invalid = !blank && (parsed === null || parsed <= 0);
  const moved = balanceText(Math.abs(entry.amount), entry.wallet_kind, entry.currency);
  return (
    <FormDialog
      title={`Reverse this ${moved} entry`}
      intro="This adds an opposite entry that names this one; the original stays in the ledger. The server refuses more than is left to reverse."
      submitLabel="Reverse"
      danger
      canSubmit={!invalid && reasonIsValid(reason)}
      onSubmit={async () => {
        await walletsApi.reverse(entry.id, parsed === null ? { reason: reason.trim() } : { amount: parsed, reason: reason.trim() }, commandId);
        return parsed === null ? "Entry reversed." : `${balanceText(parsed, entry.wallet_kind, entry.currency)} of this entry reversed.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <div className={dialog.span}>
        <TextField
          label={amountLabel("Amount to reverse", entry.wallet_kind, entry.currency)}
          autoComplete="off"
          value={amount}
          onChange={setAmount}
          hint="For a partial reversal. Leave it blank to reverse all that is left."
          error={invalid ? "Enter an amount above zero, or leave it blank." : undefined}
        />
      </div>
      <ReasonField value={reason} onChange={setReason} />
    </FormDialog>
  );
}

function UsageEventsTab({ manage, initialMeter }: { manage: boolean; initialMeter: string }) {
  const [outcome, setOutcome] = useState("");
  const [meter, setMeter] = useState(initialMeter);
  const [duplicates, setDuplicates] = useState(false);
  const [unreviewed, setUnreviewed] = useState(false);
  const [reviewing, setReviewing] = useState<UsageEventRow | null>(null);
  const paged = usePaged(
    (cursor) =>
      walletsApi.events({
        outcome: outcome || undefined,
        meter: meter || undefined,
        duplicates: duplicates ? "1" : undefined,
        unreviewed: unreviewed ? "1" : undefined,
        cursor,
      }),
    [outcome, meter, duplicates, unreviewed],
  );
  const { act, busy, feedback } = useAction(paged.reload);
  const filtered = Boolean(outcome || meter || duplicates || unreviewed);

  const columns: Column<UsageEventRow>[] = [
    { key: "received", header: "Received", value: (e) => e.received_at, render: (e) => formatDateTime(e.received_at) },
    {
      key: "customer",
      header: "Customer",
      value: (e) => e.organization_name,
      render: (e) => <OwnerLink type="organization" id={e.organization_id} name={e.organization_name} />,
    },
    { key: "product", header: "Product", value: (e) => e.product_code, render: (e) => sentence(e.product_code) },
    { key: "meter", header: "Meter", value: (e) => e.meter_label, render: (e) => e.meter_label },
    { key: "units", header: "Units", align: "right", value: (e) => e.quantity, render: (e) => formatNumber(e.quantity) },
    {
      key: "outcome",
      header: "Outcome",
      value: (e) => e.outcome,
      render: (e) => <Badge tone={OUTCOME[e.outcome].tone}>{OUTCOME[e.outcome].label}</Badge>,
    },
    { key: "failure", header: "Failure", value: (e) => e.failure_code, render: (e) => (e.failure_code ? sentence(e.failure_code) : EMPTY) },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (e) => e.amount,
      render: (e) => (e.amount ? balanceText(e.amount, e.meter === "ai_tokens" ? "ai_credits" : undefined, e.currency) : EMPTY),
    },
    { key: "duplicates", header: "Duplicates", align: "right", value: (e) => e.duplicate_count, render: (e) => formatNumber(e.duplicate_count) },
    {
      key: "review",
      header: "Review",
      hideable: false,
      render: (e) =>
        e.reviewed_at ? <Stack main={`Reviewed ${formatDateTime(e.reviewed_at)}`} sub={<Trunc max="md">{e.review_note}</Trunc>} /> : "Not reviewed",
    },
    ...(manage
      ? ([
          {
            key: "actions",
            header: "Actions",
            align: "right",
            hideable: false,
            render: (e) => (
              <span className={styles.cellActions}>
                {e.outcome === "failed" && (
                  <Button size="sm" disabled={busy} aria-label={`Retry the event received ${formatDateTime(e.received_at)}`} onClick={() => void act(() => walletsApi.retryEvent(e.id), "Event retried. Its new outcome is in the list.")}>
                    Retry
                  </Button>
                )}
                {!e.reviewed_at && (
                  <Button size="sm" aria-haspopup="dialog" aria-label={`Mark reviewed the event received ${formatDateTime(e.received_at)}`} onClick={() => setReviewing(e)}>
                    Mark reviewed
                  </Button>
                )}
              </span>
            ),
          },
        ] as Column<UsageEventRow>[])
      : []),
  ];

  return (
    <div className={styles.stack}>
      {feedback}
      <FilterBar
        label="Usage event filters"
        onClear={
          filtered
            ? () => {
                setOutcome("");
                setMeter("");
                setDuplicates(false);
                setUnreviewed(false);
              }
            : undefined
        }
      >
        <Select label="Outcome" prefix="Outcome:" value={outcome} onChange={setOutcome} options={OUTCOME_FILTER} />
        <Select label="Meter" prefix="Meter:" value={meter} onChange={setMeter} options={METER_FILTER} />
        <ToggleFilter label="Duplicates" checked={duplicates} onChange={setDuplicates} />
        <ToggleFilter label="Not yet reviewed" checked={unreviewed} onChange={setUnreviewed} />
      </FilterBar>
      <DataTable
        caption="Usage events"
        columns={columns}
        rows={paged.rows}
        rowKey={(e) => e.id}
        {...pagedProps(paged, "usage events")}
        empty={
          <EmptyState
            icon="bolt"
            title={filtered ? "No usage events match" : "No usage events yet"}
            hint={filtered ? "Try another outcome or meter, or clear the filters." : "Usage appears here as products report it."}
          />
        }
      />
      {reviewing && <ReviewDialog event={reviewing} onClose={() => setReviewing(null)} onDone={paged.reload} />}
    </div>
  );
}

function ReviewDialog({ event, onClose, onDone }: { event: UsageEventRow; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState("");
  return (
    <FormDialog
      title="Mark reviewed"
      intro={`${event.meter_label} for ${event.organization_name || "this customer"}. Say what you found. It stays with the event and in the audit log.`}
      size="sm"
      submitLabel="Mark reviewed"
      canSubmit={note.trim().length >= 3}
      onSubmit={async () => {
        await walletsApi.reviewEvent(event.id, note.trim());
        return "Event marked reviewed.";
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <TextField label="Note" required maxLength={255} autoComplete="off" value={note} onChange={setNote} hint="At least 3 characters." />
    </FormDialog>
  );
}
