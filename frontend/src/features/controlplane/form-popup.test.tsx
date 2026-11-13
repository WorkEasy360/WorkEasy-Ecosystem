import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";

import { dialog, FormPopup } from "./Modal";
import { TextField } from "./widgets";
import { expectNoViolations } from "./testSupport";

/**
 * The shared popup form (2026-11-05, the owner: every form in a popup,
 * aligned). A button opens it; the fields sit in one grid; the main button
 * is disabled until the caller says the form can be sent; a failure stays in
 * the popup with the typed value kept; a success closes it, reports the
 * message beside the button and gives focus back to the button.
 */

function Harness({ save }: { save: (name: string) => Promise<void> }) {
  const [name, setName] = useState("");
  return (
    <FormPopup
      label="+ New thing"
      title="New thing"
      intro="A short explanation."
      submitLabel="Create"
      canSubmit={name.trim() !== ""}
      onOpen={() => setName("")}
      onSubmit={async () => {
        await save(name);
        return `Thing ${name} created.`;
      }}
    >
      <TextField label="Name" value={name} onChange={setName} />
      <p className={dialog.span}>Spans both columns.</p>
    </FormPopup>
  );
}

describe("FormPopup", () => {
  it("opens from its button, sends, closes and reports the outcome", async () => {
    const save = vi.fn(async () => {});
    render(<Harness save={save} />);
    expect(screen.queryByRole("dialog")).toBeNull();

    const opener = screen.getByRole("button", { name: "+ New thing" });
    expect(opener).toHaveAttribute("aria-haspopup", "dialog");
    // A browser focuses a clicked button; jsdom does not.
    opener.focus();
    fireEvent.click(opener);

    const popup = screen.getByRole("dialog", { name: "New thing" });
    expect(within(popup).getByText("A short explanation.")).toBeInTheDocument();
    const create = within(popup).getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();
    // The first field has focus when the popup opens.
    expect(within(popup).getByLabelText("Name")).toHaveFocus();

    fireEvent.change(within(popup).getByLabelText("Name"), { target: { value: "Alpha" } });
    expect(create).toBeEnabled();
    await expectNoViolations(document.body);
    fireEvent.click(create);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(save).toHaveBeenCalledWith("Alpha");
    expect(screen.getByRole("status")).toHaveTextContent("Thing Alpha created.");
    expect(opener).toHaveFocus();
  });

  it("keeps a failure and the typed values inside the popup", async () => {
    const save = vi.fn(async () => {
      throw new Error("Nope");
    });
    render(<Harness save={save} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New thing" }));
    const popup = screen.getByRole("dialog");
    fireEvent.change(within(popup).getByLabelText("Name"), { target: { value: "Beta" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Create" }));

    expect(await within(popup).findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(popup).getByLabelText("Name")).toHaveValue("Beta");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Cancel and the close button close it without sending; it opens empty again", () => {
    const save = vi.fn(async () => {});
    render(<Harness save={save} />);
    fireEvent.click(screen.getByRole("button", { name: "+ New thing" }));
    fireEvent.change(within(screen.getByRole("dialog")).getByLabelText("Name"), { target: { value: "Gamma" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "+ New thing" }));
    expect(within(screen.getByRole("dialog")).getByLabelText("Name")).toHaveValue("");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });
});
