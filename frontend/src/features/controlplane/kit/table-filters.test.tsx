import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";

const replace = vi.fn();
let query = "";
let pathname = "/admin/things";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => pathname,
}));

import {
  Button,
  DataTable,
  DateRangeFilter,
  FilterBar,
  dayLabel,
  droppedMessage,
  isDay,
  rangeLabel,
  rangeProblem,
  useUrlFilters,
  utcDay,
  type ActiveFilter,
  type Column,
} from ".";
import { LoadGate, pagedTable, useLoad, usePaged } from "../ui";
import { expectNoViolations } from "../testSupport";

/**
 * Admin UX phase 3 (2026-11-18): tables, filters and actions - the one Actions
 * column, error and load-more states, folding columns, active filters, the date
 * range and filters kept in the address.
 */

beforeEach(() => {
  replace.mockReset();
  query = "";
  pathname = "/admin/things";
});

interface Row {
  id: string;
  name: string;
  status: string;
  email: string;
}

const ROWS: Row[] = [
  { id: "1", name: "Acme", status: "Active", email: "a@acme.example" },
  { id: "2", name: "Bolt", status: "Draft", email: "b@bolt.example" },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name", value: (r) => r.name, render: (r) => r.name },
  { key: "status", header: "Status", priority: "essential", value: (r) => r.status, render: (r) => r.status },
  { key: "email", header: "Email", priority: "low", value: (r) => r.email, render: (r) => r.email },
];

describe("DataTable: the Actions column", () => {
  it("puts the row's buttons, then its menu, in one Actions column at the end", async () => {
    const { container } = render(
      <DataTable
        caption="Things"
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.id}
        rowLabel={(r) => r.name}
        actions={(r) => (
          <Button size="sm" icon="eye" aria-label={`View ${r.name}`}>
            View
          </Button>
        )}
        rowMenu={() => [{ label: "Archive", onSelect: () => undefined }]}
      />,
    );
    const table = screen.getByRole("table", { name: "Things" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Name", "Status", "Email", "Actions"]);
    const row = within(table).getByRole("row", { name: /Acme/ });
    const cell = within(row).getAllByRole("cell").at(-1)!;
    const controls = [...cell.querySelectorAll("a, button")].map((el) => el.getAttribute("aria-label") ?? el.textContent);
    expect(controls).toEqual(["View Acme", "Actions for Acme"]);
    await expectNoViolations(container);
  });

  it("names a menu-only Actions column too", () => {
    render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} rowMenu={() => [{ label: "Archive", onSelect: () => undefined }]} />);
    expect(screen.getAllByRole("columnheader").at(-1)).toHaveTextContent("Actions");
  });

  it("has no Actions column when a row has no actions", () => {
    render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Status", "Email"]);
  });
});

describe("DataTable: small screens", () => {
  it("opens a row's details, listing the columns that fold away", async () => {
    const { container } = render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} rowLabel={(r) => r.name} />);
    const toggle = screen.getByRole("button", { name: "Show details of Acme" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const open = screen.getByRole("button", { name: "Hide details of Acme" });
    expect(open).toHaveAttribute("aria-expanded", "true");
    const details = document.getElementById(open.getAttribute("aria-controls")!)!;
    const list = details.querySelector("dl")!;
    expect(list).toHaveAttribute("aria-label", "Details of Acme");
    // The essential status never folds, so only the email is listed.
    expect(within(details).getAllByRole("term").map((t) => t.textContent)).toEqual(["Email"]);
    expect(details).toHaveTextContent("a@acme.example");
    await expectNoViolations(container);
    fireEvent.click(open);
    expect(document.getElementById(open.getAttribute("aria-controls") ?? "none")).toBeNull();
  });

  it("has no details buttons when every column is essential, or folding is off", () => {
    const view = render(
      <DataTable caption="Things" columns={COLUMNS.map((c) => ({ ...c, priority: "essential" as const }))} rows={ROWS} rowKey={(r) => r.id} />,
    );
    expect(screen.queryByRole("button", { name: /details of/ })).not.toBeInTheDocument();
    view.unmount();
    render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} responsive={false} />);
    expect(screen.queryByRole("button", { name: /details of/ })).not.toBeInTheDocument();
  });
});

describe("DataTable: states", () => {
  it("shows the error with Try again", () => {
    const retry = vi.fn();
    render(<DataTable caption="Things" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} error="Couldn't load things." onRetry={retry} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load things.");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("keeps the rows when the next page fails, and tries it again", () => {
    const more = vi.fn();
    render(
      <DataTable
        caption="Things"
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.id}
        onLoadMore={more}
        hasMore
        moreError="Couldn't load more things."
      />,
    );
    expect(screen.getByRole("row", { name: /Acme/ })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load more things.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(more).toHaveBeenCalledOnce();
  });

  it("says that a sort covers only the rows loaded while the server has more", () => {
    render(<DataTable caption="Things" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onLoadMore={() => undefined} hasMore />);
    expect(screen.getByText(/^Showing 1–2$/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Name" }));
    expect(screen.getByText("Showing 1–2 · sorted within the 2 loaded")).toBeInTheDocument();
  });
});

describe("usePaged and pagedTable", () => {
  it("keeps the rows when a next page fails, then loads it on the second try", async () => {
    let calls = 0;
    const fetchPage = vi.fn(async (cursor?: string) => {
      calls += 1;
      if (!cursor) return { results: [ROWS[0]], next_cursor: "c2", has_more: true };
      if (calls === 2) throw new Error("network");
      return { results: [ROWS[1]], next_cursor: null, has_more: false };
    });
    const { result } = renderHook(() => usePaged(fetchPage, []));
    await waitFor(() => expect(result.current.state).toBe("ready"));
    await act(async () => {
      await result.current.more();
    });
    expect(result.current.rows.map((r) => r.name)).toEqual(["Acme"]);
    expect(result.current.state).toBe("ready");
    expect(pagedTable(result.current, "things").moreError).toBe("Couldn't load more things.");
    await act(async () => {
      await result.current.more();
    });
    expect(result.current.rows.map((r) => r.name)).toEqual(["Acme", "Bolt"]);
    expect(pagedTable(result.current, "things").moreError).toBeNull();
    expect(result.current.hasMore).toBe(false);
  });

  it("offers Try again when the first page fails", async () => {
    let fail = true;
    const fetchPage = vi.fn(async () => {
      if (fail) throw new Error("down");
      return { results: ROWS, next_cursor: null, has_more: false };
    });
    const { result } = renderHook(() => usePaged(fetchPage, []));
    await waitFor(() => expect(result.current.state).toBe("error"));
    const props = pagedTable(result.current, "things");
    expect(props.error).toBe("Couldn't load things.");
    fail = false;
    act(() => props.onRetry!());
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
  });
});

describe("LoadGate", () => {
  it("offers Try again on an error, which asks again", async () => {
    let fail = true;
    const fetcher = vi.fn(async () => {
      if (fail) throw new Error("down");
      return "loaded";
    });
    function Probe() {
      const [load] = useLoad(fetcher, []);
      return (
        <LoadGate load={load} what="things">
          {(data) => <p>{data}</p>}
        </LoadGate>
      );
    }
    render(<Probe />);
    const alert = await screen.findByRole("alert");
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("loaded")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("FilterBar: active filters", () => {
  function Bar() {
    const [status, setStatus] = useState("active");
    const [q, setQ] = useState("acme");
    const active: ActiveFilter[] = [
      ...(q ? [{ key: "q", label: "Search", value: `“${q}”`, onRemove: () => setQ("") }] : []),
      ...(status ? [{ key: "status", label: "Status", value: "Active", onRemove: () => setStatus("") }] : []),
    ];
    return (
      <FilterBar
        label="Filter things"
        active={active}
        onClear={
          active.length
            ? () => {
                setQ("");
                setStatus("");
              }
            : undefined
        }
      >
        <span>controls</span>
      </FilterBar>
    );
  }

  it("lists each applied filter as a tag that removes it, and resets them all", async () => {
    const { container } = render(<Bar />);
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getAllByRole("listitem").map((t) => t.textContent)).toEqual(["Search: “acme”", "Status: Active"]);
    // The old inline link is not drawn beside the tags.
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(within(tags).getByRole("button", { name: "Remove filter Status: Active" }));
    expect(within(screen.getByRole("list", { name: "Active filters" })).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(screen.queryByRole("list", { name: "Active filters" })).not.toBeInTheDocument();
  });
});

describe("DateRangeFilter", () => {
  it("formats and checks ranges in whole UTC days", () => {
    expect(isDay("2026-11-18")).toBe(true);
    expect(isDay("2026-02-30")).toBe(false);
    expect(isDay("18/11/2026")).toBe(false);
    // The console's one date format, whatever the browser's language (CI runs en-US).
    expect(dayLabel("2026-11-01")).toBe("1 Nov 2026");
    expect(dayLabel("2026-09-30")).toBe("30 Sep 2026");
    expect(rangeLabel({ from: "", to: "" })).toBe("Any time");
    expect(rangeLabel({ from: "2026-11-01", to: "" })).toMatch(/^From /);
    expect(rangeLabel({ from: "", to: "2026-11-01" })).toMatch(/^Until /);
    expect(rangeProblem({ from: "2026-11-10", to: "2026-11-01" }, "2026-11-18")).toBe("The start date is after the end date.");
    expect(rangeProblem({ from: "2026-12-01", to: "" }, "2026-11-18")).toBe("The start date is in the future.");
    expect(rangeProblem({ from: "2026-11-01", to: "2026-11-10" }, "2026-11-18")).toBeNull();
    expect(utcDay(0, new Date("2026-11-18T23:30:00Z"))).toBe("2026-11-18");
  });

  it("applies a range only on Apply, refuses a reversed one, and resets", async () => {
    const onApply = vi.fn();
    function Probe() {
      const [value, setValue] = useState({ from: "", to: "" });
      return (
        <DateRangeFilter
          label="Created"
          value={value}
          onApply={(r) => {
            onApply(r);
            setValue(r);
          }}
        />
      );
    }
    render(<Probe />);
    const button = screen.getByRole("button", { name: /Created: Any time/ });
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(button);
    const panel = screen.getByRole("dialog", { name: "Created between" });
    await expectNoViolations(panel);
    const from = within(panel).getByLabelText("From");
    const to = within(panel).getByLabelText("To");
    fireEvent.change(from, { target: { value: "2026-11-10" } });
    fireEvent.change(to, { target: { value: "2026-11-01" } });
    expect(within(panel).getByRole("alert")).toHaveTextContent("The start date is after the end date.");
    expect(within(panel).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.change(to, { target: { value: "2026-11-12" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenLastCalledWith({ from: "2026-11-10", to: "2026-11-12" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    expect(button).toHaveTextContent(/Created:.*10.*Nov.*2026.*–.*12.*Nov.*2026/);
    fireEvent.click(button);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset" }));
    expect(onApply).toHaveBeenLastCalledWith({ from: "", to: "" });
  });

  it("offers quick ranges, closes on Escape and keeps Tab inside", () => {
    render(<DateRangeFilter label="Issued" value={{ from: "", to: "" }} onApply={() => undefined} />);
    const button = screen.getByRole("button", { name: /Issued: Any time/ });
    fireEvent.click(button);
    const panel = screen.getByRole("dialog", { name: "Issued between" });
    const quick = within(panel).getByRole("group", { name: "Quick ranges" });
    expect(within(quick).getAllByRole("button").map((b) => b.textContent)).toEqual(["Today", "Last 7 days", "Last 30 days", "This month", "Last month"]);
    fireEvent.click(within(quick).getByRole("button", { name: "Last 7 days" }));
    expect(within(quick).getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getByLabelText("To")).toHaveValue(utcDay());
    // Focus starts in the panel; Shift+Tab from the first control wraps to the last.
    const first = within(quick).getByRole("button", { name: "Today" });
    first.focus();
    fireEvent.keyDown(panel, { key: "Tab", shiftKey: true });
    expect(within(panel).getByRole("button", { name: "Apply" })).toHaveFocus();
    fireEvent.keyDown(panel, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
});

describe("useUrlFilters", () => {
  const SPEC = { status: { options: ["open", "paid"] as const }, q: {}, from: { date: true }, to: { date: true } };

  it("reads the filters from the address, dropping what it does not allow", () => {
    query = "status=bogus&q=acme&from=2026-11-01&to=nope&tab=billing";
    const { result } = renderHook(() => useUrlFilters(SPEC));
    expect(result.current.values).toEqual({ status: "", q: "acme", from: "2026-11-01", to: "" });
    expect(result.current.dropped).toEqual(["status", "to"]);
    expect(droppedMessage(result.current.dropped)).toMatch(/not recognised/);
    expect(droppedMessage([])).toBeNull();
  });

  it("drops a range that ends before it starts", () => {
    query = "from=2026-11-10&to=2026-11-01";
    const { result } = renderHook(() => useUrlFilters(SPEC));
    expect(result.current.values).toMatchObject({ from: "", to: "" });
    expect(result.current.dropped).toEqual(["from", "to"]);
  });

  it("writes a change to the address, keeping its other parameters", () => {
    query = "tab=billing&q=acme";
    const { result } = renderHook(() => useUrlFilters(SPEC));
    act(() => result.current.set({ status: "paid", from: "2026-11-01" }));
    expect(result.current.values).toEqual({ status: "paid", q: "acme", from: "2026-11-01", to: "" });
    expect(replace).toHaveBeenLastCalledWith("/admin/things?tab=billing&q=acme&status=paid&from=2026-11-01", { scroll: false });
    act(() => result.current.reset());
    expect(replace).toHaveBeenLastCalledWith("/admin/things?tab=billing", { scroll: false });
    expect(result.current.values).toEqual({ status: "", q: "", from: "", to: "" });
  });

  it("keeps a change while the router catches up, then follows a later change from outside", () => {
    query = "status=open";
    const { result, rerender } = renderHook(() => useUrlFilters(SPEC));
    act(() => result.current.set({ status: "paid" }));
    // The address has not changed yet: the change stands.
    rerender();
    expect(result.current.values.status).toBe("paid");
    // The router catches up with this list's own change: nothing is re-read.
    query = "status=paid";
    rerender();
    expect(result.current.values.status).toBe("paid");
    // Back, or a link: the address changes from outside, and the list follows.
    query = "status=open&q=bolt";
    rerender();
    expect(result.current.values).toMatchObject({ status: "open", q: "bolt" });
  });

  it("follows the address when it changes from outside", () => {
    query = "status=open";
    const { result, rerender } = renderHook(() => useUrlFilters(SPEC));
    expect(result.current.values.status).toBe("open");
    query = "status=paid";
    rerender();
    expect(result.current.values.status).toBe("paid");
  });
});
