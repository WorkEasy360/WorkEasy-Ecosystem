"use client";

import { useState } from "react";
import Link from "next/link";
import { accessApi, type DomainsOverview, type LoginClientRow, type SignInEventRow } from "@/lib/api/adminPanel";
import { adminApi } from "@/lib/api/controlPlane";
import { TableSection } from "./AdminCommerce";
import { DateRange, OffsetPager } from "./AdminListParts";
import { DateText } from "./AdminUsers";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, describeError, useLoad } from "./ui";
import {
  Alert,
  Badge,
  BoolBadge,
  Button,
  ButtonLink,
  DataTable,
  EmptyState,
  FilterBar,
  IdCell,
  KeyText,
  Page,
  PageHeader,
  PersonCell,
  SearchInput,
  Select,
  Stack,
  StatusBadge,
  StatusChips,
  Trunc,
  UrlCell,
  EMPTY,
  formatDateTime,
  orEmpty,
  productName,
  sentence,
  type Column,
} from "./kit";
import local from "./AdminPeople.module.css";
import styles from "./AdminDomains.module.css";

/**
 * Admin → Settings, the read-only views over addresses and sign-in (2026-11-09): Domains
 * & Apps, Login clients and the Sign-in audit. Nothing here is edited: each address is
 * changed on its own page. Domain status is a live check of the address itself.
 */

const CATALOG_VIEW = "platform.staff_catalog.view";
const LIMIT = 25;

const isWebUrl = (url: string) => /^https?:\/\//i.test(url);

/** An address that may have been typed by anyone is a link only when it is a web address. */
function Address({ url }: { url: string | null | undefined }) {
  if (!url) return <>{EMPTY}</>;
  return isWebUrl(url) ? <UrlCell url={url} /> : <Trunc>{url}</Trunc>;
}

function hostOf(origin: string): string {
  try {
    return new URL(origin).hostname;
  } catch {
    return origin;
  }
}

// --- domains & apps ----------------------------------------------------------------------

type App = DomainsOverview["apps"][number];
type Portal = DomainsOverview["portals"][number];
type PartnerDomain = DomainsOverview["partner_domains"][number];
type PartnerBrand = DomainsOverview["brands"][number];

export function DomainsScreen() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => accessApi.domains(), []);
  // The apps carry a product's code; its page is addressed by id, known to the catalog.
  const canCatalog = can(permissions, CATALOG_VIEW);
  const [products] = useLoad(() => (canCatalog ? adminApi.products() : Promise.resolve({ results: [] })), [canCatalog]);
  const idOf = (code: string) => (products.status === "ready" ? products.data.results.find((p) => p.code === code)?.id : undefined);
  const productHref = (code: string): string | null => {
    if (!canCatalog) return null;
    const id = idOf(code);
    return id ? `/admin/products/${id}` : "/admin/products";
  };
  return (
    <Page>
      <PageHeader
        title="Domains & Apps"
        subtitle="Where the platform, its products and its partners answer. Read-only here."
        info="A product's addresses are changed on its own page, and a partner's domain on the partner's page."
      />
      <LoadGate load={load} what="domains and apps">
        {(d) => (
          <div className={local.stack}>
            {!d.dns_provider_connected && (
              <Alert tone="info">No Cloudflare account is connected, so this is a live check of the address itself, not Cloudflare&apos;s own status.</Alert>
            )}
            <Portals portals={d.portals} />
            <Apps apps={d.apps} customerOrigin={d.portals.find((p) => p.key === "app")?.origin ?? ""} productHref={productHref} />
            <PartnerDomains rows={d.partner_domains} />
            <Brands rows={d.brands} />
          </div>
        )}
      </LoadGate>
    </Page>
  );
}

function Portals({ portals }: { portals: Portal[] }) {
  const columns: Column<Portal>[] = [
    { key: "portal", header: "Portal", render: (p) => p.label },
    { key: "address", header: "Address", render: (p) => <Address url={p.origin} /> },
    { key: "check", header: "Live check", wrap: true, render: (p) => <HostCheck label={p.label} host={hostOf(p.origin)} /> },
  ];
  return (
    <TableSection title="Primary domains" description="The platform's own addresses, from its configuration.">
      <DataTable
        caption="Primary domains"
        columns={columns}
        rows={portals}
        rowKey={(p) => p.key}
        pageSize={0}
        maxHeight={false}
        hideEmptyColumns={false}
        empty={<EmptyState icon="globe" title="No portal addresses are configured" compact />}
      />
    </TableSection>
  );
}

/** Asks the server to try the address now, and says exactly what it answered. */
function HostCheck({ label, host }: { label: string; host: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const check = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const answer = await accessApi.checkHost(host);
      setResult({ ok: answer.ok, detail: answer.detail });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className={styles.check}>
      <Button size="sm" loading={busy} aria-label={`Check ${label}`} onClick={() => void check()}>
        Check
      </Button>
      <span role="status" className={styles.checkResult}>
        {result && (
          <>
            <BoolBadge value={result.ok} yes="Answering" no="Not answering" noTone="danger" /> {result.detail}
          </>
        )}
      </span>
      {error && (
        <span role="alert" className="w360-error-text">
          {error}
        </span>
      )}
    </span>
  );
}

const ROUTE_WORDS: [keyof App["purchase_routes"], string][] = [
  ["store", "Store"],
  ["reseller", "Reseller"],
  ["affiliate", "Affiliate"],
  ["white_label", "White-label"],
];

function Apps({ apps, customerOrigin, productHref }: { apps: App[]; customerOrigin: string; productHref: (code: string) => string | null }) {
  const columns: Column<App>[] = [
    {
      key: "product",
      header: "Product",
      render: (a) => {
        const href = productHref(a.code);
        const name = productName(a.name);
        return <Stack main={href ? <Link href={href}>{name}</Link> : name} sub={<KeyText>{a.code}</KeyText>} />;
      },
    },
    { key: "status", header: "Status", render: (a) => <StatusBadge value={a.status} /> },
    { key: "application", header: "Application", render: (a) => <Address url={a.application_url} /> },
    {
      key: "check",
      header: "Live check",
      wrap: true,
      // 2026-11-10: product addresses get the same DNS and TLS check as the portals.
      render: (a) => (isWebUrl(a.application_url) ? <HostCheck label={productName(a.name)} host={hostOf(a.application_url)} /> : EMPTY),
    },
    { key: "marketing", header: "Marketing", render: (a) => <Address url={a.marketing_url} /> },
    {
      key: "links",
      header: "App links",
      wrap: true,
      render: (a) => {
        const links = Object.entries(a.app_links);
        if (links.length === 0) return EMPTY;
        return (
          <ul className={styles.list}>
            {links.map(([name, url]) => (
              <li key={name}>
                {isWebUrl(url) ? (
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    {sentence(name)}
                    <span className="w360-visually-hidden"> (opens in a new tab)</span>
                  </a>
                ) : (
                  `${sentence(name)}: ${url}`
                )}
              </li>
            ))}
          </ul>
        );
      },
    },
    {
      key: "store",
      header: "Store link",
      render: (a) => (a.store_link ? <Address url={customerOrigin ? `${customerOrigin}${a.store_link}` : a.store_link} /> : EMPTY),
    },
    {
      key: "routes",
      header: "Purchase routes",
      wrap: true,
      render: (a) => {
        const open = ROUTE_WORDS.filter(([key]) => a.purchase_routes[key]);
        if (open.length === 0) return <Badge tone="neutral">Not on sale</Badge>;
        return (
          <span className={styles.badges}>
            {open.map(([key, word]) => (
              <Badge key={key} tone="success">
                {word}
              </Badge>
            ))}
          </span>
        );
      },
    },
  ];
  return (
    <TableSection title="Product addresses & apps" description="Each product's addresses and how it can be bought. Open a product to change them.">
      <DataTable
        caption="Product addresses and apps"
        columns={columns}
        rows={apps}
        rowKey={(a) => a.code}
        pageSize={0}
        hideEmptyColumns={false}
        empty={<EmptyState icon="box" title="No products yet" compact />}
      />
    </TableSection>
  );
}

function PartnerDomains({ rows }: { rows: PartnerDomain[] }) {
  const columns: Column<PartnerDomain>[] = [
    { key: "hostname", header: "Hostname", render: (r) => <Trunc>{r.hostname}</Trunc> },
    { key: "partner", header: "Partner", render: (r) => <Link href={`/admin/partners/${r.partner_id}`}>{r.partner_name}</Link> },
    { key: "status", header: "Status", render: (r) => <StatusBadge value={r.status} /> },
    { key: "verified", header: "Verified", render: (r) => <DateText iso={r.verified_at} never="Not verified" /> },
    // 2026-11-10: the same live DNS and TLS check as the primary domains.
    { key: "check", header: "Live check", wrap: true, render: (r) => <HostCheck label={r.hostname} host={r.hostname} /> },
  ];
  return (
    <TableSection title="Partner domains" description="Addresses partners have asked to use. Verified on the partner's page.">
      <DataTable
        caption="Partner domains"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        pageSize={0}
        maxHeight={false}
        hideEmptyColumns={false}
        empty={<EmptyState icon="globe" title="No partner domains yet" compact />}
      />
    </TableSection>
  );
}

function Brands({ rows }: { rows: PartnerBrand[] }) {
  const columns: Column<PartnerBrand>[] = [
    { key: "partner", header: "Partner", render: (r) => <Link href={`/admin/partners/${r.partner_id}`}>{r.partner_name}</Link> },
    { key: "brand", header: "Brand name", render: (r) => <Trunc>{r.brand_name}</Trunc> },
    { key: "logo", header: "Logo", render: (r) => <BoolBadge value={r.has_logo} yes="Has logo" no="No logo" noTone="neutral" /> },
  ];
  return (
    <TableSection
      title="Partner branding"
      description="Partners that have set up their own brand. Whether a partner may use its brand at all comes from the white-label capability, granted or withdrawn on the partner's page."
    >
      <DataTable
        caption="Partner branding"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.partner_id}
        pageSize={0}
        maxHeight={false}
        hideEmptyColumns={false}
        empty={<EmptyState icon="palette" title="No partner has set up a brand yet" compact />}
      />
    </TableSection>
  );
}

// --- login clients ------------------------------------------------------------------------

function AddressList({ urls }: { urls: string[] }) {
  if (urls.length === 0) return <>{EMPTY}</>;
  return (
    <ul className={styles.list}>
      {urls.map((url) => (
        <li key={url}>
          <code className={styles.address}>{url}</code>
        </li>
      ))}
    </ul>
  );
}

const CLIENT_COLUMNS: Column<LoginClientRow>[] = [
  { key: "product", header: "Product", render: (c) => <KeyText>{c.product_code}</KeyText> },
  { key: "client", header: "Client ID", render: (c) => <IdCell value={c.client_id} label="Copy client ID" /> },
  { key: "status", header: "Status", render: (c) => <StatusBadge value={c.status} /> },
  { key: "return", header: "Approved return destinations", wrap: true, render: (c) => <AddressList urls={c.redirect_uris} /> },
  { key: "signout", header: "Sign-out destinations", wrap: true, render: (c) => <AddressList urls={c.post_logout_redirect_uris} /> },
  { key: "launch", header: "Launch URL", render: (c) => <Address url={c.launch_url} /> },
];

export function LoginClientsScreen() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => accessApi.loginClients(), []);
  return (
    <Page>
      <PageHeader
        title="Login clients"
        subtitle="Which products sign in with WorkEasy360, and where each may send people back. Read-only here."
        info="To change them, open the product and go to its integration page."
        actions={
          can(permissions, CATALOG_VIEW) ? (
            <ButtonLink href="/admin/products" iconEnd="chevronRight">
              Open products to change them
            </ButtonLink>
          ) : undefined
        }
      />
      <LoadGate load={load} what="login clients">
        {({ results }) => (
          <DataTable
            caption="Login clients"
            columns={CLIENT_COLUMNS}
            rows={results}
            rowKey={(c) => c.client_id || c.product_code}
            pageSize={0}
            hideEmptyColumns={false}
            empty={<EmptyState icon="key" title="No login clients yet" hint="A product appears here once it signs in with WorkEasy360." />}
          />
        )}
      </LoadGate>
    </Page>
  );
}

// --- sign-in audit ---------------------------------------------------------------------------

const SIGN_IN_KINDS = [
  { value: "", label: "All events" },
  { value: "sign_in", label: "Signed in" },
  { value: "sign_in_failed", label: "Sign-in failed" },
  { value: "password_reset", label: "Password reset" },
  { value: "account_unlocked", label: "Account unlocked" },
];

const WHOSE = [
  { value: "staff", label: "Staff only" },
  { value: "everyone", label: "Everyone" },
];

const SIGN_IN_COLUMNS: Column<SignInEventRow>[] = [
  { key: "when", header: "When", render: (e) => formatDateTime(e.occurred_at) },
  {
    key: "person",
    header: "Person",
    render: (e) => <PersonCell name={e.name} email={e.email} badge={e.is_staff ? <Badge tone="brand">Staff</Badge> : undefined} />,
  },
  {
    key: "event",
    header: "Event",
    render: (e) => <Badge tone={e.kind === "sign_in_failed" ? "danger" : e.kind === "sign_in" ? "success" : "neutral"}>{e.kind_label}</Badge>,
  },
  { key: "reason", header: "Reason", render: (e) => <Trunc max="md">{orEmpty(e.reason)}</Trunc> },
  { key: "network", header: "Network", render: (e) => orEmpty(e.ip_prefix) },
  { key: "device", header: "Device", render: (e) => <Trunc max="md">{orEmpty(e.device_label)}</Trunc> },
];

export function SignInAuditScreen() {
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [whose, setWhose] = useState("staff");
  const [offset, setOffset] = useState(0);
  const [load] = useLoad(
    () =>
      accessApi.signIns({
        kind: kind || undefined,
        q: query || undefined,
        from: from || undefined,
        to: to || undefined,
        everyone: whose === "everyone" ? "1" : undefined,
        limit: LIMIT,
        offset,
      }),
    [kind, query, from, to, whose, offset],
  );
  /** A new filter starts again on the first page. */
  const first = (set: (value: string) => void) => (value: string) => {
    set(value);
    setOffset(0);
  };
  const filtered = Boolean(kind || query || from || to);
  const rows = load.status === "ready" ? load.data.results : [];
  return (
    <Page>
      <PageHeader
        title="Sign-in audit"
        subtitle="Who signed in or failed to, from which network and device, newest first."
        info="Only a coarse network and device are kept, never a password or a token."
      />
      <StatusChips label="Whose sign-ins" value={whose} onChange={first(setWhose)} chips={WHOSE} />
      <FilterBar
        label="Filter sign-ins"
        onClear={
          filtered
            ? () => {
                setKind("");
                setQuery("");
                setFrom("");
                setTo("");
                setOffset(0);
              }
            : undefined
        }
      >
        <SearchInput label="Search by email" value={query} onSearch={first(setQuery)} />
        <Select label="Event" prefix="Event:" value={kind} onChange={first(setKind)} options={SIGN_IN_KINDS} />
        <DateRange from={from} to={to} onFrom={first(setFrom)} onTo={first(setTo)} />
      </FilterBar>
      <DataTable
        caption="Sign-in events"
        columns={SIGN_IN_COLUMNS}
        rows={rows}
        rowKey={(e) => e.id}
        loading={load.status === "loading"}
        error={load.status === "error" ? load.message : load.status === "forbidden" ? "You don't have access to the sign-in audit." : null}
        pageSize={0}
        hideEmptyColumns={false}
        empty={<EmptyState icon="login" title="No sign-in events" hint={filtered || whose === "staff" ? "Try other filters, or look at everyone." : undefined} />}
      />
      {load.status === "ready" && <OffsetPager total={load.data.total} limit={load.data.limit} offset={load.data.offset} shown={rows.length} onOffset={setOffset} />}
    </Page>
  );
}
