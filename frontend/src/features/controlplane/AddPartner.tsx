"use client";

import { AddPartnerForm } from "./addpartner/AddPartnerForm";

/**
 * Partners → Add Partner (ADR-048): the short form - partner name, administrator
 * email and password, partner type, optional country. `?type=` only preselects a
 * card; nothing in the address grants anything. Everything else is set in the
 * partner's Complete Your Profile.
 */
export function AddPartner() {
  return <AddPartnerForm />;
}
