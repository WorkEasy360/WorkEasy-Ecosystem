# Phase 6 — API surface and frontend

## 1. Product-facing API (Task 8)

| Method | Path | Auth | Answers |
|---|---|---|---|
| `POST` | `/api/v1/integrations/events` | product client authentication (form-encoded, as the token endpoint) | `202 {"status":"accepted"}`, `200 {"status":"duplicate"}`, `400`/`403`/`409`/`413`/`422`/`429`/`503` OAuth-shaped errors |

Discovery (`/.well-known/workeasy360-configuration`) advertises it as
`workeasy360_event_endpoint`. The contract is [01-event-model.md](01-event-model.md).
The reference product client in `backend/tests/factories/reference_product_client.py`
gains `publish_event()`, and still imports no platform code.

## 2. Customer API (Tasks 84, 85)

All routes are under `/api/v1/orgs/<org_slug>/`. They use the existing
session + CSRF + tenancy middleware, and return JSON errors in the platform
shape: `{"error": {"code", "message", "field"?}}`.

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `connectors` | connection.view | registered connector types and their config fields |
| GET | `event-catalog` | connection.view or automation.view | events of entitled products, as declared |
| GET | `connections` | connection.view | keyset-paginated; `?status=` |
| POST | `connections` | connection.manage (AAL 2) | `201`; the **only** response carrying the secret; `no-store` |
| GET / PATCH | `connections/<id>` | view / manage | PATCH needs `revision` (optimistic) |
| POST | `connections/<id>/status` | manage | `{"status": "active" \| "suspended" \| "disabled", "reason"?}` |
| POST / DELETE | `connections/<id>/secret` | manage | rotate (`201`, secret once, `no-store`) / end overlap now |
| GET / PUT | `connections/<id>/subscriptions` | view / manage | PUT replaces the set atomically: `{"event_filters": [...]}` |
| POST | `connections/<id>/ping` | manage | `202`; one signed test event; rate limited (10/min) |
| GET | `deliveries` | connection.view | `?status=&connection_id=&cursor=&limit=` |
| GET | `deliveries/<id>` | connection.view | attempts, categories, HTTP status, ≤ 200-char excerpt; **never** the body or headers sent |
| POST | `deliveries/<id>/replay` | webhook.replay | `202`; dead deliveries only; one open replay at a time |
| GET | `automation-actions` | automation.view | registered actions and their config fields |
| GET / POST | `automations` | view / manage | `?status=` |
| GET / PUT | `automations/<id>` | view / manage | PUT needs `revision`; creates a new version |
| POST | `automations/<id>/status` | manage | `{"status", "revision"}` |
| GET | `automations/<id>/executions` | automation.view | keyset-paginated |

**Conventions.**

- **Validation first, then one atomic write (D-016).** A 4xx never leaves a
  partial write (`test_a_rejected_connection_writes_nothing`,
  `test_a_refused_rotation_writes_nothing`,
  `test_an_invalid_rule_edit_creates_no_version`,
  `test_one_invalid_filter_adds_no_subscription`).
- Lists are keyset-paginated, with page size capped at 100.
- Another organization's object is 404.
- A dangerous change at AAL 1 is `403 step_up_required`.

## 3. Frontend (Tasks 86, 92)

Everything lives under Settings. The navigation gains **Integrations** and
**Automations**; the delivery log is reached from Integrations.

| Route | Component | Does |
|---|---|---|
| `/o/<slug>/settings/integrations` | `ConnectionsSettings` | list, create (webhook URL + name), activate / suspend / disable, rotate and end overlap, event subscriptions picked from the catalogue, send test event, connection health |
| `/o/<slug>/settings/webhooks` | `DeliveryLog` | filterable delivery log, attempt history, replay (dead only) |
| `/o/<slug>/settings/automations` | `AutomationsSettings` | rules list; a form-based editor (trigger, conditions, actions from the registry); activate / pause / archive; execution history |

This is a **form**, not a visual workflow builder (explicitly out of scope).

- **Secrets are shown once.** `SecretReveal` holds the value in component
  state only. There is a copy button and an "I've stored it" button that
  drops it. It is never put in `localStorage`, a URL or a log.
- **Step-up in place.** `useStepUp` wraps a dangerous call. On
  `step_up_required` it shows the existing Phase 5 `StepUpForm`, then
  retries the *same* change. `StepUpForm` and the MFA error copy were
  extracted from `MfaSettings` so the two share one implementation, not a
  copy.
- **Permissions:** the server is the only authority. The UI does not
  pre-hide controls. A refused view shows a permission notice
  (`role="alert"`), and a refused change says so in plain words.
- **Accessibility:**
  - labelled controls;
  - status changes announced through `role="status"`;
  - the one-time secret panel is keyboard-reachable, with focus moved to
    it;
  - axe-clean in the component tests.

  A **manual screen-reader pass was NOT EXECUTED** (no screen reader
  available in this environment).
- **CSP:** every route is dynamically rendered and carries the nonce
  (`scripts/assert-dynamic-rendering.mjs` passes on the production build).

**Component tests** (`src/features/integrations/integrations.test.tsx`, 6):
- shows a new signing secret once, stores it nowhere, and forgets it;
- offers step-up when the change needs it, then retries the same change;
- is accessible (axe), including the one-time secret panel;
- offers replay only for dead deliveries and never shows a body or header;
- builds a generic rule definition from the form;
- keeps condition values typed the way the server compares them.

**Bundle:** measured on production builds with and without the Phase 6 UI.

| | Raw | Gzip |
|---|---|---|
| Shared shell chunk (the two nav links) | +118 B | +24 B |
| The three new routes (loaded only on those pages) | +42.3 KB across 3 new chunks | +12.4 KB |
| All client JS | 645,621 → 688,680 B | 204,623 → 217,206 B |
