"use client";

import { useId } from "react";
import { useRouter } from "next/navigation";
import { AddPartnerForm } from "./addpartner/AddPartnerForm";
import { Modal } from "./Modal";

/**
 * Partners → New partner (ADR-048): the short form - partner name, administrator
 * email, an invitation (or a password set here), partner type, optional country -
 * in a dialog over All partners (2026-10-26). `?type=` only preselects the type;
 * nothing in the address grants anything. Everything else is set in the
 * partner's profile. Closing returns to All partners.
 */
export function AddPartner({ onClose }: { onClose?: () => void } = {}) {
  const router = useRouter();
  const titleId = useId();
  const close = onClose ?? (() => router.push("/admin/partners/all"));
  return (
    <Modal labelledBy={titleId} onClose={close} size="md">
      <AddPartnerForm titleId={titleId} onClose={close} />
    </Modal>
  );
}
