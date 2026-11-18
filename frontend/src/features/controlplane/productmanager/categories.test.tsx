import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";

import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "../testSupport";
import { Modal } from "../Modal";
import { CategoryField } from "./CategoryField";
import { CategoryManager } from "./CategoryManager";
import { ProductManager } from "./ProductManager";

/**
 * 2026-11-16, the owner: "make category customisable". The category field
 * offers the Super Admin's own list, adds a new category in place, and opens
 * Manage categories (add, rename, reorder, delete with a destination for the
 * products in it). Every change is the server's; the popup shows its answer.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/admin/products",
}));

const API = "/api/v1/admin/products/categories";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_pricing.manage", "platform.staff_integration.manage"];

const row = (id: string, name: string, products = 0) => ({ id, name, products });
const listOf = (rows: ReturnType<typeof row>[], extra: Record<string, unknown> = {}) => ({
  categories: rows,
  unlisted: [],
  max_categories: 60,
  can_manage: true,
  ...extra,
});
const failed = (status: number, detail: string, field = "name"): Reply => ({ status, body: { code: "catalog_validation_error", detail, field } });

function Field({ initial = "", canManage = true, onSubmit = vi.fn() }: { initial?: string; canManage?: boolean; onSubmit?: () => void }) {
  const [value, setValue] = useState(initial);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <label htmlFor="cat">Product category</label>
      <CategoryField id="cat" value={value} options={["Sales", "Customer Support"]} canManage={canManage} onChange={setValue} />
      <output data-testid="value">{value}</output>
    </form>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Category field", () => {
  it("offers the list and + New category; a typed one is added to the list and chosen, and Enter never submits the form", async () => {
    const calls = mockApi({
      [`POST ${API}`]: [{ status: 201, body: row("c3", "Field Service") }],
      [`GET ${API}`]: [ok(listOf([row("c1", "Sales"), row("c2", "Customer Support"), row("c3", "Field Service")]))],
    });
    const submitted = vi.fn();
    render(<Field onSubmit={submitted} />);

    const select = screen.getByLabelText("Product category");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose a category", "Sales", "Customer Support", "+ New category…"]);

    fireEvent.change(select, { target: { value: "__new_category__" } });
    const input = await screen.findByRole("textbox", { name: "Product category" });
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "  Field   Service " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("Field Service"));
    expect(calls.find((c) => c.key === `POST ${API}`)?.body).toEqual({ name: "Field Service" });
    expect(submitted).not.toHaveBeenCalled();
    const back = screen.getByLabelText("Product category");
    expect(back).toHaveValue("Field Service");
    expect(within(back).getAllByRole("option").map((o) => o.textContent)).toContain("Field Service");
  });

  it("a name already on the list (any case) is chosen without adding a second one", async () => {
    const calls = mockApi({});
    render(<Field />);
    fireEvent.change(screen.getByLabelText("Product category"), { target: { value: "__new_category__" } });
    const input = await screen.findByRole("textbox", { name: "Product category" });
    fireEvent.change(input, { target: { value: "customer support" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("Customer Support"));
    expect(calls).toHaveLength(0);
  });

  it("shows the server's refusal beside the new name and keeps it", async () => {
    mockApi({ [`POST ${API}`]: [failed(400, "The list holds at most 60 categories. Delete one first.")] });
    render(<Field />);
    fireEvent.change(screen.getByLabelText("Product category"), { target: { value: "__new_category__" } });
    const input = await screen.findByRole("textbox", { name: "Product category" });
    fireEvent.change(input, { target: { value: "Robotics" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 60 categories");
    expect(input).toHaveValue("Robotics");
    expect(screen.getByTestId("value")).toHaveTextContent("");
  });

  it("without the manage grant it is a plain choice: no new category, no Manage categories", () => {
    mockApi({});
    render(<Field canManage={false} />);
    expect(within(screen.getByLabelText("Product category")).queryByText("+ New category…")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage categories" })).not.toBeInTheDocument();
  });

  it("follows a rename made in Manage categories, and Escape closes only that popup", async () => {
    mockApi({
      [`GET ${API}`]: [ok(listOf([row("c1", "Sales", 2), row("c2", "Customer Support")])), ok(listOf([row("c1", "Sales & CRM", 2), row("c2", "Customer Support")]))],
      [`PATCH ${API}/c1`]: [ok(row("c1", "Sales & CRM", 2))],
    });
    const closeOuter = vi.fn();
    render(
      <Modal labelledBy="outer" onClose={closeOuter}>
        <h2 id="outer">Edit listing</h2>
        <Field initial="Sales" />
      </Modal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Manage categories" }));
    const manager = await screen.findByRole("dialog", { name: "Product categories" });
    fireEvent.click(await within(manager).findByRole("button", { name: "Rename Sales" }));
    const name = within(manager).getByRole("textbox", { name: "New name for Sales" });
    fireEvent.change(name, { target: { value: "Sales & CRM" } });
    fireEvent.click(within(manager).getByRole("button", { name: "Save" }));
    expect(await within(manager).findByRole("status")).toHaveTextContent("Renamed “Sales” to “Sales & CRM” and its 2 products.");
    await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("Sales & CRM"));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Product categories" })).not.toBeInTheDocument());
    expect(closeOuter).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Edit listing" })).toBeInTheDocument();
  });
});

describe("Manage categories", () => {
  it("lists each category with its products; adds, reorders and deletes; passes axe", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [
        ok(listOf([row("c1", "Sales", 3), row("c2", "Finance"), row("c3", "HR", 1)], { unlisted: [{ name: "Field Service", products: 1 }] })),
        ok(listOf([row("c1", "Sales", 3), row("c2", "Finance"), row("c3", "HR", 1), row("c4", "Robotics")])),
      ],
      [`POST ${API}`]: [{ status: 201, body: row("c4", "Robotics") }],
      [`POST ${API}/order`]: [ok(listOf([row("c2", "Finance"), row("c1", "Sales", 3), row("c3", "HR", 1), row("c4", "Robotics")]))],
      [`DELETE ${API}/c3`]: [ok(listOf([row("c2", "Finance"), row("c1", "Sales", 4), row("c4", "Robotics")]))],
    });
    const changed = vi.fn();
    const outerSubmit = vi.fn();
    const { container } = render(
      // Opened from a form (the Listing popup): its own forms must not submit that one.
      <form
        onSubmit={(e) => {
          e.preventDefault();
          outerSubmit();
        }}
      >
        <CategoryManager onClose={vi.fn()} onChanged={changed} />
      </form>,
    );
    const manager = await screen.findByRole("dialog", { name: "Product categories" });
    const list = await within(manager).findByRole("list", { name: "Categories" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([expect.stringContaining("Sales3 products"), expect.stringContaining("FinanceNo products"), expect.stringContaining("HR1 product")]);
    expect(within(manager).getByRole("region", { name: "In use but not on the list" })).toHaveTextContent("Field Service1 product");
    await expectNoViolations(container.ownerDocument.body);

    fireEvent.change(within(manager).getByLabelText("New category"), { target: { value: "Robotics" } });
    fireEvent.click(within(manager).getByRole("button", { name: "Add category" }));
    expect(await within(manager).findByRole("status")).toHaveTextContent("Added “Robotics”.");
    expect(outerSubmit).not.toHaveBeenCalled();
    expect(changed).toHaveBeenLastCalledWith({ kind: "added", name: "Robotics" }, expect.anything());

    fireEvent.click(within(manager).getByRole("button", { name: "Move Sales down" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${API}/order`)?.body).toEqual({ order: ["c2", "c1", "c3", "c4"] }));
    expect(within(manager).getByRole("button", { name: "Move Finance up" })).toBeDisabled();

    // HR has a product: the delete needs somewhere to move it.
    fireEvent.click(await within(manager).findByRole("button", { name: "Delete HR" }));
    const group = within(manager).getByRole("group", { name: "Delete HR" });
    const confirm = within(group).getByRole("button", { name: "Delete" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(group).getByLabelText(/Move the 1 product in “HR” to/), { target: { value: "c1" } });
    fireEvent.click(confirm);
    expect(await within(manager).findByText("Deleted “HR”; its products are now in “Sales”.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `DELETE ${API}/c3`)?.url).toContain("move_to=c1");
    expect(changed).toHaveBeenLastCalledWith({ kind: "deleted", name: "HR", movedTo: "Sales" }, expect.anything());
  });

  it("shows the server's refusal in the popup", async () => {
    mockApi({
      [`GET ${API}`]: [ok(listOf([row("c1", "Sales"), row("c2", "Finance")]))],
      [`PATCH ${API}/c1`]: [failed(400, "There is already a category called 'Finance'.")],
    });
    render(<CategoryManager onClose={vi.fn()} />);
    const manager = await screen.findByRole("dialog", { name: "Product categories" });
    fireEvent.click(await within(manager).findByRole("button", { name: "Rename Sales" }));
    fireEvent.change(within(manager).getByRole("textbox", { name: "New name for Sales" }), { target: { value: "finance" } });
    fireEvent.click(within(manager).getByRole("button", { name: "Save" }));
    expect(await within(manager).findByRole("alert")).toHaveTextContent("There is already a category called 'Finance'.");
    expect(within(manager).getByRole("textbox", { name: "New name for Sales" })).toHaveValue("finance");
  });

  it("is read-only without the manage grant", async () => {
    mockApi({ [`GET ${API}`]: [ok(listOf([row("c1", "Sales", 1)], { can_manage: false }))] });
    render(<CategoryManager onClose={vi.fn()} />);
    const manager = await screen.findByRole("dialog", { name: "Product categories" });
    expect(await within(manager).findByText("Sales")).toBeInTheDocument();
    expect(within(manager).queryByLabelText("New category")).not.toBeInTheDocument();
    expect(within(manager).queryByRole("button", { name: "Rename Sales" })).not.toBeInTheDocument();
  });

  it("opens from Products > Categories, and the page reloads its filter after a change", async () => {
    const calls = mockApi({
      [`GET /api/v1/admin/products/console`]: [
        ok({ products: [], can_see_integrations: true, categories: ["Sales"] }),
        ok({ products: [], can_see_integrations: true, categories: ["Sales", "Robotics"] }),
      ],
      [`GET ${API}`]: [ok(listOf([row("c1", "Sales")])), ok(listOf([row("c1", "Sales"), row("c2", "Robotics")]))],
      [`POST ${API}`]: [{ status: 201, body: row("c2", "Robotics") }],
    });
    render(
      <AdminContextForTest permissions={MANAGE}>
        <ProductManager />
      </AdminContextForTest>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Categories" }));
    const manager = await screen.findByRole("dialog", { name: "Product categories" });
    fireEvent.change(await within(manager).findByLabelText("New category"), { target: { value: "Robotics" } });
    fireEvent.click(within(manager).getByRole("button", { name: "Add category" }));
    await within(manager).findByText("Added “Robotics”.");
    fireEvent.click(within(manager).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Product categories" })).not.toBeInTheDocument());
    await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/v1/admin/products/console")).toHaveLength(2));
  });
});
