"use client";

import { useId } from "react";
import { useRouter } from "next/navigation";
import { AddPartnerForm } from "./addpartner/AddPartnerForm";
import { Modal } from "./Modal";

/**
 * Partners → Add Partner (ADR-048): the short form - partner name, administrator
 * email and password, partner type, optional country - in a dialog over All
 * Partners (2026-10-26). `?type=` only preselects the type; nothing in the
 * address grants anything. Everything else is set in the partner's Complete
 * Your Profile. Closing returns to All Partners.
 */
export function AddPartner({ onClose }: { onClose?: () => void } = {}) {
  const router = useRouter();
  const titleId = useId();
  const close = onClose ?? (() => router.push("/admin/partners/all"));
  return (
    <Modal labelledBy={titleId} onClose={close}>
      <AddPartnerForm titleId={titleId} onClose={close} />
    </Modal>
  );
}
