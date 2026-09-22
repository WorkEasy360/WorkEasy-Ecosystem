"use client";

import { useRef } from "react";
import { getCsrfToken } from "@/lib/api/client";
import { LAUNCH_PATH } from "@/lib/api/launcher";

/**
 * A real HTML form POST, not a `fetch` - see lib/api/launcher.ts. This is
 * the only way to make the browser follow the backend's 302 to the
 * product as a genuine top-level navigation (Task 16, Task 54), which is
 * also what keeps a raw token or URL from ever passing through this app's
 * own JavaScript (Task 17).
 *
 * The CSRF token is read from the cookie at submit time, into an
 * uncontrolled input via a ref - not React state. This component never
 * renders during the server pass with real content (it mounts only after
 * the shell's own client-side auth check resolves), so there is nothing to
 * hydrate against; reading it eagerly into state would just be an extra
 * render for no benefit.
 */
export function LaunchForm({ orgSlug, productCode }: { orgSlug: string; productCode: string }) {
  const tokenInputRef = useRef<HTMLInputElement>(null);

  return (
    <form
      method="POST"
      action={LAUNCH_PATH(orgSlug, productCode)}
      onSubmit={() => {
        if (tokenInputRef.current) tokenInputRef.current.value = getCsrfToken() ?? "";
      }}
    >
      <input ref={tokenInputRef} type="hidden" name="csrfmiddlewaretoken" defaultValue="" />
      <button type="submit" className="w360-btn w360-btn-primary">
        Open
      </button>
    </form>
  );
}
