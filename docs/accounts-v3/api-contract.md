# Accounts V3: API contract (backend <-> frontend)

Status: working contract for branch `feat/accounts-v3` (stacked on `feat/universal-login-complete`, PR #14).
Spec: "WorkEasy360 Accounts & Universal Product Login - Final Update and Development Sign-off Note, Version 3" (19 Nov 2026).

All endpoints are under `/api/v1/`. Session cookie + CSRF header on every write, as today.
Errors keep the platform shape: `{"code": "...", "detail": "...", "field"?: "..."}`.
"center" = served only on the WorkEasy360 Accounts host and only while `W360_ACCOUNTS_IDENTITY` is on (404 otherwise), like the existing `auth/account/*`.

## 1. My Profile (center)

`GET auth/account/profile`, `PATCH auth/account/profile`

```json
{
  "user_id": "uuid", "email": "a@b.c", "name": "Ravi Kumar", "email_verified": true,
  "created_at": "iso", "last_sign_in_at": "iso|null",
  "country": "IN|\"\"", "language": "en|\"\"", "time_zone": "Asia/Kolkata|\"\"",
  "photo_url": "/api/v1/public/profile-photos/<token>|null",
  "pending_email": {"new_email": "x@y.z", "requested_at": "iso", "expires_at": "iso"} | null,
  "two_step_enabled": false
}
```

PATCH takes any subset of `full_name`, `country` (ISO 3166-1 alpha-2), `language` (a code from the options list), `time_zone` (IANA name). `""` clears country/language/time_zone. 400 `profile_invalid` with `field`.

`GET auth/account/profile/options` -> `{"countries": ["AD", ...], "languages": ["en", "hi", ...], "time_zones": ["Africa/Abidjan", ...]}` (codes only; names come from `Intl.DisplayNames` in the browser)

`PUT auth/account/profile/photo`: raw request body, `Content-Type: image/png | image/jpeg | image/webp`, at most 262144 bytes (256 KB). The client should first scale the picture to at most 512x512 on a canvas (that also drops camera metadata). Answers the profile JSON. 400 `photo_invalid`.
`DELETE auth/account/profile/photo` -> the profile JSON with `photo_url: null`.
`photo_url` is a capability URL that changes on every upload; use it directly as `<img src>`.

## 2. Login & Security (every web host, like the existing `auth/password`, `auth/email`)

### Sign-in email change (existing `POST auth/email {new_email, password}` unchanged)

- `GET auth/email/pending` -> `{"pending": {"new_email","requested_at","expires_at"} | null}`
- `POST auth/email/pending/resend` -> 202 `{"pending": {...}}`; 429 when throttled; 404 `no_pending_email_change`.
- `DELETE auth/email/pending` -> `{"pending": null}`. The old address keeps working throughout.

### Two-step verification (authenticator app + recovery codes)

- `GET auth/two-step` -> `{"available": bool, "enabled": bool, "method": "totp"|null, "enabled_at": "iso|null", "recovery_codes_remaining": int}`.
  `available: false` = this deployment has no two-step key configured: show "not available yet", no setup button.
- `POST auth/two-step/setup {password, code?}` -> `{"secret": "BASE32", "otpauth_uri": "otpauth://totp/...", "qr_svg_data_uri": "data:image/svg+xml;base64,...", "expires_at": "iso"}`.
  `code` (current authenticator code or a recovery code) is required when two-step is already on: that is "replace my authenticator". Nothing changes until confirm.
- `POST auth/two-step/setup/confirm {code}` -> `{"enabled": true, "recovery_codes": ["abcde-12345", ... 10], "other_sessions_ended": true}`.
  Show the codes once, with copy/download, and a checkbox "I saved these codes" before closing.
- `POST auth/two-step/recovery-codes {password, code}` -> `{"recovery_codes": [...10]}` (old codes stop working).
- `POST auth/two-step/disable {password, code}` -> `{"enabled": false}`.
- Errors: 400 `two_step_code_invalid` (field `code`), 400 `password_invalid` (field `password`), 409 `two_step_not_enabled` / `two_step_setup_missing`, 429 throttled, 503 `two_step_unavailable`.

### Sign-in with two-step (every login form: customer/Accounts/product sign-in, Admin, Partner)

`POST auth/login {email, password, remember?}` answers, for an account with two-step on:
`401 {"code": "two_step_required", "challenge": "<opaque>", "detail": "Enter the 6-digit code from your authenticator app."}` - no session yet.

`POST auth/login/two-step {challenge, code}` -> exactly what a successful `auth/login` answers (`{user_id, email, name}`), session established. `code` is a 6-digit authenticator code or a recovery code (`xxxxx-xxxxx`).
Errors: 401 `two_step_code_invalid` (try again), 401 `two_step_challenge_expired` (start again from the password), 429 throttled.
The `remember` choice made on the password step is kept by the server.

Mobile number: there is no mobile sign-in and no SMS provider. Do not offer a phone field. One line under the email card: "Sign-in with a mobile number is not available."

## 3. Devices & Sessions (existing, unchanged)

`GET auth/account/sessions` (center), `POST auth/sessions/<id>/revoke`, `POST auth/sessions/revoke-others`, `POST auth/sessions/sign-out-everywhere`, `GET auth/account/connections` + `POST auth/account/connections/<code>/sign-out` (center), `GET auth/account/history` (center).

## 4. Settings (center)

- `GET auth/account/notifications` -> `{"preferences": [{"key","label","description","enabled","required"}]}`. `required: true` rows (security notices) are shown on and locked.
- `PATCH auth/account/notifications {"<key>": bool, ...}` -> same shape. 400 `preference_invalid` for an unknown or required key.
- `GET auth/activity` (existing): recent security activity.
- `GET auth/account/organizations` -> `{"organizations": [{"organization_id","name","slug","status","is_owner","roles":[...],"can_view_billing": bool}]}` for the links to User Admin (`<app origin>/o/<slug>`), subscriptions and billing (only when `can_view_billing`).
- `GET auth/account/closure` -> `{"can_close": bool, "blocking_organizations": [{"name","slug"}], "two_step_required": bool}`.
- `POST auth/account/closure {password, code?, confirmation: "CLOSE"}` -> `{"closed": true}`. The session is ended by the server; go to `/login?closed=1`.
  409 `sole_owner` with `blocking_organizations` when the person is the only owner of an organization.

### Account-security support (signed-out or signed-in)

- `POST public/account-support {email, name, topic, message}` -> 202 `{"reference": "AS-XXXXXXXX"}`. Same answer whether or not an account exists. `topic`: `cannot_sign_in` | `lost_two_step` | `account_compromised` | `closure` | `other`. 429 throttled.
- Super Admin: `GET admin/account-support?status=open|resolved` -> `{"results": [{"id","reference","email","name","topic","message","status","created_at","resolved_at","note"}]}`; `PATCH admin/account-support/<id> {status, note}`.
  Permission: `platform.staff_user.view` to list, `platform.staff_user.manage` to change.
