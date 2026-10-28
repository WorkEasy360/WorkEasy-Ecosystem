"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getSession } from "@/lib/api/auth";
import { publicApi } from "@/lib/api/public";
import { REFERRAL_TOKEN } from "@/features/auth/SelfService";

/**
 * A partner's referral link (ADR-053): `/r/<code>`. Public, and served by the
 * sign-in portal so the referral cookie the server sets is the one sign-up and
 * conversion read. The server records the visit and answers where the link
 * leads - a path on the platform's own origin, derived from the link's product.
 * Anything else (an unknown code, an outage, an odd answer) opens sign-up: a
 * referral link never sends anyone off the platform.
 */
const SAFE_PATH = /^\/(?!\/)[A-Za-z0-9/_?=&.-]*$/;

export default function ReferralLinkPage() {
  const params = useParams<{ code: string }>();
  const [message, setMessage] = useState("Taking you to Workeasy360…");

  useEffect(() => {
    const code = String(params?.code ?? "");
    const go = (path: string) => window.location.replace(SAFE_PATH.test(path) ? path : "/signup");
    if (!/^[A-Za-z0-9]{4,16}$/.test(code)) {
      go("/signup");
      return;
    }
    // A first visit has no CSRF cookie yet: reading the session issues it,
    // then the capture (a POST) carries it like every other request.
    getSession()
      .catch(() => null)
      .then(() => publicApi.captureReferral(code))
      .then(
      ({ token, destination }) => {
        try {
          sessionStorage.setItem(REFERRAL_TOKEN, token);
        } catch {
          // Private mode: the HttpOnly cookie the server set still carries it.
        }
        go(destination || "/signup");
      },
      () => {
        setMessage("This link is no longer active. Opening sign-up…");
        go("/signup");
      },
    );
  }, [params]);

  return (
    <main className="w360-container" style={{ padding: "48px 16px" }}>
      <p aria-live="polite">{message}</p>
    </main>
  );
}
