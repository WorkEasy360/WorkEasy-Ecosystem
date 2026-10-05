"use client";

import { MySecurityScreen } from "@/features/console/MySecurityScreen";

/**
 * Security → My security: the signed-in person's own account (authenticator
 * app, password, sessions, recent activity). Global identity data, shown
 * inside the organization shell for navigational consistency.
 */
export default function SecurityPage() {
  return <MySecurityScreen />;
}
