# 02 — App Directory

`GET /api/v1/orgs/<slug>/apps` — `platform.launcher.selectors.app_directory`.

Returns every product `ProductDirectory.list_marketable()` names (catalog
status `available` or `deprecated`; Task 21), each decided through
`can_launch_product` ([03](03-launcher-access-model.md)):

```json
{
  "apps": [
    {
      "product_code": "crm",
      "display_name": "CRM",
      "description": "…",
      "licensing_mode": "per_seat",
      "state": "active",
      "can_launch": true
    }
  ]
}
```

**Never in the response** (Task 6): `launch_url`, any redirect/client
configuration, subscription/plan/price internals, seats remaining as a raw
number tied to billing. `seats_total`/`seats_assigned` do appear, but only
on the separate, permission-gated seat-overview endpoint
(`GET .../apps/<code>/seats`, Task 13), not the directory every member can
read.

**Data-driven, not hardcoded** (Tasks 21/22): the product set comes entirely
from `ProductDirectory.list_marketable()`; nothing in `platform/launcher`,
the interfaces view, or the frontend branches on a specific product code.
`tests/architecture/test_architecture.py`'s existing
`test_no_platform_module_branches_on_a_product_code` already covers the
backend half unconditionally; the frontend has no equivalent automated
check yet (see [12-cleanup-review.md](12-cleanup-review.md)).

Product icons (Task 23) are not a stored asset at all: the frontend renders
a deterministic monogram from `display_name` (`AppCard.tsx`), so there is no
image URL - trusted or otherwise - anywhere in this surface.
