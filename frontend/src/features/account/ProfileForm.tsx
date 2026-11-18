"use client";

import { useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountCenterApi, type AccountProfile } from "@/lib/api/console";
import { Button } from "@/features/controlplane/kit";

/** The person's display name (ADR-072). Only the name: the sign-in email has its own
 * confirmed change, and the server re-checks the name it is sent. */
export function ProfileForm({ profile, onSaved }: { profile: AccountProfile; onSaved?: () => void }) {
  const id = useId();
  const [name, setName] = useState(profile.name);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const unchanged = name.trim().replace(/\s+/g, " ") === profile.name;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        setMessage(null);
        accountCenterApi
          .updateProfile(name)
          .then((saved) => {
            setName(saved.name);
            setMessage({ ok: true, text: "Your name is saved." });
            onSaved?.();
          })
          .catch((err: unknown) => {
            const body = err instanceof ApiError ? (err.body as { detail?: string } | undefined) : undefined;
            setMessage({ ok: false, text: body?.detail || "Your name could not be saved. Try again." });
          })
          .finally(() => setBusy(false));
      }}
    >
      <label className="w360-label" htmlFor={`${id}-name`}>
        Full name
      </label>
      <input
        id={`${id}-name`}
        className="w360-input"
        name="full_name"
        autoComplete="name"
        maxLength={201}
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <div>
        <Button type="submit" variant="primary" loading={busy} disabled={unchanged || !name.trim()}>
          Save name
        </Button>
      </div>
      {message && <p role={message.ok ? "status" : "alert"}>{message.text}</p>}
    </form>
  );
}
