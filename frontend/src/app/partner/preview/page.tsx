import { GuestPreviewScreen } from "@/features/partnerpanel/GuestPreview";

/** Public (2026-11-10): "Continue as Guest" from the partner sign-in. A static,
 * read-only preview with example figures - no session, no API call, no rights
 * (`proxy.ts`, PUBLIC_PATHS). */
export default function Page() {
  return <GuestPreviewScreen />;
}
