import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/admin" }));

import {
  Badge,
  BoolBadge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  Menu,
  PageHeader,
  SearchInput,
  Select,
  StatCard,
  StatGrid,
  StatusBadge,
  StatusChips,
  Tabs,
  ToastProvider,
  eventLabel,
  formatDate,
  formatDateTime,
  formatMoney,
  formatRelative,
  orEmpty,
  productName,
  sentence,
  statusLabel,
  type Column,
} from ".";
import { expectNoViolations } from "../testSupport";

/**
 * The Super Admin design system (2026-11-07): the shared components every
 * console page is built from, and the one way to write dates, amounts and
 * system keys.
 */

describe("formatters", () => {
  it("write dates as 24 Oct 2026 and times without seconds", () => {
    expect(formatDate("2026-10-24T06:45:58Z")).toMatch(/^\d{1,2} Oct 2026$/);
    expect(formatDateTime("2026-10-24T06:45:58")).toBe("24 Oct 2026, 6:45 AM");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });

  it("say how long ago, then fall back to the date", () => {
    const now = Date.parse("2026-11-07T12:00:00Z");
    expect(formatRelative("2026-11-07T11:59:50Z", now)).toBe("Just now");
    expect(formatRelative("2026-11-07T11:55:00Z", now)).toBe("5 min ago");
    expect(formatRelative("2026-11-07T09:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative("2026-11-06T10:00:00Z", now)).toBe("Yesterday");
    expect(formatRelative("2026-11-03T12:00:00Z", now)).toBe("4 days ago");
    expect(formatRelative("2026-08-01T12:00:00Z", now)).toMatch(/Aug 2026$/);
  });

  it("write money with Intl and one empty mark", () => {
    expect(formatMoney(125000, "INR")).toBe("₹1,250.00");
    expect(formatMoney(500, "JPY")).toMatch(/500/);
    expect(formatMoney(null, "INR")).toBe("—");
    for (const blank of ["", "None", "None recorded", "Not configured", null]) expect(orEmpty(blank)).toBe("—");
  });

  it("turn system keys into words", () => {
    expect(eventLabel("commercial.invoice.issued")).toBe("Invoice issued");
    expect(eventLabel("staff.signed_in")).toBe("Staff signed in");
    expect(eventLabel("identity.user")).toBe("User");
    expect(sentence("long_context")).toBe("Long context");
    expect(productName("Easy CRM (easy_crm)")).toBe("Easy CRM");
    expect(statusLabel("AFFILIATE")).toBe("Affiliate");
    expect(statusLabel("past_due")).toBe("Past due");
    expect(statusLabel("trialing")).toBe("Trial");
  });
});

describe("badges and cards", () => {
  it("draw a status in Title Case with its colour, and booleans with an icon", () => {
    render(
      <>
        <StatusBadge value="active" />
        <StatusBadge value="AFFILIATE" />
        <BoolBadge value={false} yes="Verified" no="Unverified" />
        <Badge tone="info">Trial</Badge>
      </>,
    );
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Affiliate")).toBeInTheDocument();
    expect(screen.getByText("Unverified")).toBeInTheDocument();
  });

  it("draw no change as neutral, never as growth", () => {
    const { container } = render(
      <StatGrid>
        <StatCard label="Revenue" value="₹0.00" delta={0} />
        <StatCard label="Users" value="12" delta={12.5} />
        <StatCard label="Partners" value="3" delta={-4} />
      </StatGrid>,
    );
    expect(screen.getByText(/No change/).parentElement).toHaveAttribute("data-dir", "flat");
    expect(screen.getByText(/Up/).parentElement).toHaveAttribute("data-dir", "up");
    expect(screen.getByText(/Down/).parentElement).toHaveAttribute("data-dir", "down");
    expect(container.firstElementChild).toHaveAttribute("data-count", "3");
  });

  it("set the browser title from the page header", async () => {
    const { container } = render(<PageHeader title="Users" subtitle="Every account on the platform." />);
    expect(screen.getByRole("heading", { level: 1, name: "Users" })).toBeInTheDocument();
    expect(document.title).toBe("Users · WorkEasy360 Admin");
    await expectNoViolations(container);
  });

  it("render one empty state with a call to action", () => {
    render(<EmptyState title="No plans yet" hint="Create the first plan." action={{ label: "New plan", href: "/admin/plans" }} />);
    expect(screen.getByText("No plans yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New plan" })).toHaveAttribute("href", "/admin/plans");
  });
});

describe("controls", () => {
  it("chips show counts and one is pressed", () => {
    function H() {
      const [v, setV] = useState("");
      return (
        <StatusChips
          label="Segment"
          value={v}
          onChange={setV}
          chips={[
            { value: "", label: "All", count: 9 },
            { value: "trial", label: "Trial", count: 0 },
          ]}
        />
      );
    }
    render(<H />);
    expect(screen.getByRole("button", { name: "All 9" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Trial 0" }));
    expect(screen.getByRole("button", { name: "Trial 0" })).toHaveAttribute("aria-pressed", "true");
  });

  it("search applies 300 ms after typing stops, with no Search button", () => {
    vi.useFakeTimers();
    const onSearch = vi.fn();
    render(<SearchInput label="Search users" value="" onSearch={onSearch} />);
    fireEvent.change(screen.getByLabelText("Search users"), { target: { value: "asha" } });
    expect(onSearch).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onSearch).toHaveBeenCalledWith("asha");
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(onSearch).toHaveBeenLastCalledWith("");
    expect(screen.queryByRole("button", { name: "Search" })).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("the styled select works from the keyboard", () => {
    const onChange = vi.fn();
    render(
      <Select
        label="Status"
        value="all"
        onChange={onChange}
        options={[
          { value: "all", label: "All statuses" },
          { value: "active", label: "Active" },
          { value: "trial", label: "Trial" },
        ]}
      />,
    );
    const box = screen.getByRole("combobox", { name: "Status" });
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(screen.getByRole("listbox", { name: "Status" })).toBeInTheDocument();
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("active");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("a menu opens on its first item, moves with arrows and closes on Escape", async () => {
    const view = vi.fn();
    render(<Menu label="Actions for Asha" showLabel={false} icon="more" items={[{ label: "View", onSelect: view }, { label: "Deactivate", danger: true, separator: true }]} />);
    const trigger = screen.getByRole("button", { name: "Actions for Asha" });
    fireEvent.click(trigger);
    const menu = screen.getByRole("menu");
    await waitFor(() => expect(within(menu).getByRole("menuitem", { name: "View" })).toHaveFocus());
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    await waitFor(() => expect(within(menu).getByRole("menuitem", { name: "Deactivate" })).toHaveFocus());
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("underline tabs move with the arrow keys", () => {
    function H() {
      const [t, setT] = useState<"a" | "b">("a");
      return (
        <Tabs label="Sections" tabs={[{ key: "a", label: "Overview" }, { key: "b", label: "Sessions", count: 2 }]} selected={t} onSelect={setT}>
          <p>{t}</p>
        </Tabs>
      );
    }
    render(<H />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: /Sessions/ })).toHaveAttribute("aria-selected", "true");
  });
});

interface Row {
  id: string;
  name: string;
  amount: number;
  note: string | null;
}

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name", render: (r) => r.name, value: (r) => r.name },
  { key: "amount", header: "Amount", render: (r) => String(r.amount), value: (r) => r.amount, align: "right" },
  { key: "note", header: "Note", render: (r) => r.note ?? "—", value: (r) => r.note },
];

describe("DataTable", () => {
  const rows: Row[] = Array.from({ length: 30 }, (_, i) => ({ id: `r${i}`, name: `Row ${String(i).padStart(2, "0")}`, amount: i * 10, note: null }));

  it("pages, says what it shows, hides all-empty columns and sorts", async () => {
    const { container } = render(<DataTable caption="Rows" columns={COLUMNS} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByText("Showing 1–25 of 30")).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Note/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Showing 26–30 of 30")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    expect(screen.getByRole("columnheader", { name: /Amount/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getAllByRole("rowheader")[0]).toHaveTextContent("Row 29");
    await expectNoViolations(container);
  });

  it("shows its empty state when there are no rows", () => {
    render(<DataTable caption="Rows" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty={<EmptyState title="No rows yet" />} />);
    expect(screen.getByText("No rows yet")).toBeInTheDocument();
  });

  it("selects rows for bulk actions", () => {
    function H() {
      const [sel, setSel] = useState<Set<string>>(new Set());
      return (
        <DataTable
          caption="Rows"
          columns={COLUMNS}
          rows={rows.slice(0, 3)}
          rowKey={(r) => r.id}
          rowLabel={(r) => r.name}
          selectable={{ selected: sel, onChange: setSel, actions: <button type="button">Export</button> }}
        />
      );
    }
    render(<H />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all rows on this page" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("waits for the typed word, sends the reason and reports the result", async () => {
    const onConfirm = vi.fn(async () => "Partner terminated.");
    const onClose = vi.fn();
    render(
      <ToastProvider>
        <ConfirmDialog
          options={{ title: "Terminate Acme?", message: "Ends the partnership.", confirmLabel: "Terminate", danger: true, typeToConfirm: "TERMINATE", reason: true, onConfirm }}
          onClose={onClose}
        />
      </ToastProvider>,
    );
    const go = screen.getByRole("button", { name: "Terminate" });
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "terminate" } });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Contract ended in October" } });
    expect(go).toBeEnabled();
    fireEvent.click(go);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("Contract ended in October"));
    expect(onClose).toHaveBeenCalled();
  });
});
