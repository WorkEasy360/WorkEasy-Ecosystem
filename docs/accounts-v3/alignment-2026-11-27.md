# Accounts V2/V3 alignment audit (27 November 2026)

Brief: "WorkEasy360 — Complete Accounts & Unified Login Audit, Gap Analysis and Alignment" (27 Nov 2026), with the reference images "Account Login" and "User Account Dashboard". Decision record: ADR-077.

**Baseline.** The V2 personal-account note (18 Nov) is not on this machine. The V3 sign-off note (19 Nov) states that it consolidates V2, and it was used, with the brief's sections A-H, as the requirement list. The code audited is `main` at `e5eb2a0`; "Now" is branch `feat/accounts-v2-alignment`.

**Rule.** "Implemented" means code plus a test that asserts the behaviour. Code with no such test is Partial or Not Verified. Paths: `BE` = `backend/workeasy360/`, `T` = `backend/tests/`, `FE` = `frontend/src/`.

## Counts (79 requirements)

| Status | At main e5eb2a0 | On this branch |
|---|---|---|
| Implemented | 46 | 54 |
| Partial | 24 | 20 |
| Missing | 4 | 2 |
| Incorrect | 2 | 0 |
| Not Verified | 3 | 3 |

Percentages are not given: the rows differ in size and risk, so a share of rows is not a measure of completeness.


## A. Accounts ownership and identity

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| A.1 | One stable user_id per person (= OIDC sub) | Implemented | **Implemented** | BE platform/identity/models.py:98; platform/ecosystem/tokens.py:256 | ecosystem/tests/test_oidc_flow.py::test_the_full_flow_issues_a_verifiable_identity_token | - | Low |
| A.2 | Products reuse the identity; no product credential store in the platform | Implemented | **Implemented** | Products are OIDC clients only | T universal_sso/test_one_login_guards.py::test_one_person_model_holds_a_password, ::test_one_endpoint_takes_a_password_to_sign_in | - | Low |
| A.3 | One person in several organizations; membership separate from login | Implemented | **Implemented** | BE platform/ecosystem/authorization.py:432-513 | T universal_sso/test_select_account_several_organizations.py (4) | - | Low |
| A.4 | Entitlement independent of authentication | Implemented | **Implemented** | authorization.py:516,568,618 | T universal_sso/test_explore_sign_in.py::test_sign_in_states_never_read_an_entitlement | - | Low |
| A.5 | No automatic account merge on a matching email | Implemented | **Implemented** | Unique email; change to a held address refused | T unified_login/test_email_change.py::test_an_address_another_account_holds_gets_the_same_answer_and_is_told | - | Low |
| A.6 | Contacts, employees, learners and shoppers are not login accounts | Not Verified | **Not Verified** | No contact model in the platform; products own these records | partner contact test only | Verify in the CRM, HRMS, LMS and Store repositories | Med |

## B. Accounts portal: four menus

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| B1.1 | Photo, name and verified-email header | Implemented | **Implemented** | FE features/account/ProfileOverview.tsx (ProfileSummary); BE accounts_center_views.py _profile_json | FE accounts-center.test.tsx 'opens first…', 'shows an unverified email…' | Redesigned to reference image 2 | Low |
| B1.2 | Edit name, photo, country, language, time zone; Save/Cancel, validation, feedback | Implemented | **Implemented** | BE platform/identity/account.py:520-648; FE ProfileForm.tsx | T accounts_v3/test_profile_and_settings.py::test_the_profile_fields_save_validate_and_clear; FE 'edits in place…', 'says what the server refused…', new 'Edit Profile and the photo button…' | Edit Profile now opens the editor from the card | Low |
| B1.3 | Photo checked by its bytes, metadata stripped | Implemented | **Implemented** | account.py:651-800 | T ::test_a_photo_is_checked_by_its_bytes…; test_review_fixes.py::test_a_photo_is_stored_without_its_metadata | - | Low |
| B1.4 | Profile changes reach products | Partial | **Partial** | BE platform/identity/selectors.py:23-101 (name, picture, locale, zoneinfo, updated_at at sign-in and userinfo) | T ::test_products_read_the_new_profile_at_their_next_sign_in | Pull only; no product reads picture, locale or zoneinfo; no push | Med |
| B1.5 | Historical records (invoices, orders) not rewritten | Not Verified | **Not Verified** | By construction: Invoice has no personal fields | none | Add a regression test | Low |
| B1.6 | Personal language and time zone do not change organization policy | Not Verified | **Not Verified** | By construction | none | Add a test or ADR note | Low |
| B2.1 | Email verification | Implemented | **Implemented** | platform/administration/self_service.py:279-326 | T phase9/test_self_service.py::test_verification_needs_the_link_and_the_password, ::test_an_expired_link_is_refused | - | Low |
| B2.2 | Mobile number and its verification | Missing | **Missing** | No phone field, no SMS provider; FE SignInEmailCard says it is not available | FE '…no mobile sign-in…' | Needs an SMS provider and an owner decision | Med |
| B2.3 | Sensitive identifier change re-authenticates (incl. two-step) | Incorrect | **Implemented** | BE self_service.request_email_change(code=…); account_views.email_change_view (two_step_daily budget); FE ChangeEmailForm two-step code | T accounts_v3/test_alignment_2026_11_27.py (5 tests, 3 fail on main); FE change-email.test.tsx (2 new); browser V.18-V.20 | FIXED: code required when two-step is on | High (was) |
| B2.4 | Pending change, resend, cancel; old login keeps working | Implemented | **Implemented** | self_service.py:549-641; FE SignInEmailCard.tsx | T test_email_change.py::test_a_pending_change_shows_resends_and_cancels…; FE 'a resend too soon…' | - | Low |
| B2.5 | Atomic switch, single-use link, conflicts, old address told | Implemented | **Implemented** | self_service.py:644-692 | T test_email_change.py (5 tests) | - | Low |
| B2.6 | Change password with the current one; other sessions end; owner notified | Partial | **Implemented** | BE account.change_password; NEW account_security.password_changed_notice + template platform.account.password_changed_signed_in | T test_alignment…::test_a_signed_in_password_change_tells_the_verified_address (fails on main), ::test_a_refused_password_change_sends_no_notice | FIXED: notice added | Med (was) |
| B2.7 | Forgot password: non-enumerating, expiring single-use link, sessions revoked | Partial | **Implemented** | self_service.py:353-445 | T test_self_service.py (2); NEW test_alignment…::test_an_expired_reset_link_is_refused_and_the_password_stays | Expiry test added | Low |
| B2.8 | Two-step verified before activation | Implemented | **Implemented** | platform/identity/two_step.py:308-398 | T accounts_v3/test_two_step.py::test_setup_needs_the_password…, ::test_confirming_turns_it_on… | - | Low |
| B2.9 | Recovery codes (hashed, single use, renewable) | Implemented | **Implemented** | two_step.py:163-190,343-415 | T ::test_a_recovery_code_signs_in_once…, ::test_secrets_and_codes_are_never_stored_in_clear | - | Low |
| B2.10 | Replace/turn off with re-authentication; last route kept | Implemented | **Implemented** | two_step.py:400-440 | T ::test_turning_off_needs_password_and_code_and_leaves_the_password_route | - | Low |
| B3.1 | Device/browser, last active, this-device marker | Implemented | **Implemented** | account.py:258-300; FE CenterSessions.tsx | T accounts_center/test_sessions_history_profile.py::test_one_row_per_sign_in_not_per_portal | - | Low |
| B3.2 | Sign out one session | Implemented | **Implemented** | account.py:303-354 | T accounts_center/test_session_revoke.py (3) | - | Low |
| B3.3 | Sign out all other sessions | Implemented | **Implemented** | account.py:357-400 | T ::test_revoking_the_others_keeps_my_whole_sign_in | - | Low |
| B3.4 | Sign out everywhere (central + product sessions) | Implemented | **Implemented** | account.py:403-420; account_views.py | T universal_sso/test_backchannel_logout.py (27) | - | Low |
| B3.5 | Next protected product request rejected after revocation | Partial | **Partial** | Platform: userinfo epoch + w360_sin, redeem check, back-channel outbox | T test_v3_own_organization_and_revocation.py (3) | No product default branch has a receiver; no product calls userinfo; seed manifests lack backchannel_logout_uri | High |
| B4.1 | Personal notification preferences; security notices locked on | Partial | **Partial** | platform/identity/preferences.py; FE SettingsSections.tsx | T ::test_security_notices_are_locked_on_and_the_rest_are_the_persons | product_news toggle has no sender | Low |
| B4.2 | Recent security activity, values redacted | Implemented | **Implemented** | account.py:82-112 | T test_account_sessions.py::test_device_labels_and_prefixes_are_coarse | Also on My Profile now (latest 4) | Low |
| B4.3 | Authorized links to User Admin, subscriptions, billing | Partial | **Partial** | accounts_center_views.py:650-683 | T ::test_the_organization_links_name_only_the_persons_own (billing restriction not asserted) | Add a test: member without billing rights gets no billing link | Low |
| B4.4 | Closure re-confirms identity (password, code, CLOSE) | Implemented | **Implemented** | administration/account_security.py:344-420 | T ::test_closing_with_two_step_on_needs_the_code | - | Low |
| B4.5 | Sole owner / last partner admin cannot close | Implemented | **Implemented** | account_security.py:300-341 | T test_closure_partners_and_invited_owners.py (5) | - | Low |
| B4.6 | Closure explains its effect truthfully; retention | Incorrect | **Partial** | FE SettingsSections.tsx CloseAccount | browser V.16 | FIXED wording (memberships stay, reopening via support); no retention/erasure policy beyond event retention (ADR-075 decision) | Med |
| B4.7 | Exactly four menus, My Profile first | Implemented | **Implemented** | FE centerConfig.ts CENTER_SECTIONS; CenterShell.tsx | FE 'opens first…'; browser V.9; harness H.1 | Only while W360_ACCOUNTS_IDENTITY is on (production: off) | High (release) |
| B4.8 | No wallet, organization admin, catalog or contacts inside Accounts | Partial | **Partial** | No such menu | T accounts_v3/test_platform_boundaries.py | Unused 'My Products' endpoints still routed (urls.py:155-163) | Low |

## C. Ecosystem integration

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| C.1 | User Admin uses the central sign-in | Implemented | **Implemented** | FE proxy.ts:121-145; platform/identity/portals.py (ADR-043 hand-off) | T accounts_portal/test_accounts_identity.py; FE portal-routing.test.ts | Flag-gated; production runs ADR-059 own-host sign-in | High (release) |
| C.2 | Store uses the central sign-in | Implemented | **Implemented** | proxy.ts:165-169; auth_views.py:643-647 | T store_domain/test_store_host.py; FE store-portal.test.ts | Flag-gated | Med |
| C.3 | Super Admin and Partner Admin share the one identity | Implemented | **Implemented** | Own /login on each host, same User table (ADR-059) | FE portal-routing.test.ts | By design not the Accounts page | Low |
| C.4 | Easy CRM (pilot) integrated | Partial | **Partial** | AI-CRM#19 open (connector 0.3.1, back-channel); default branch: older OIDC, no back-channel | CRM CI 11/11; harness 56/56 | Merge after the platform release | High |
| C.5 | Other products integrated | Missing | **Missing** | EasyBook#2, Project#1/#2, Help Desk#1, HRMS#1, Agents#20, LMS#1, Booker#1, SendStack#1, Forms#1, Commerce#1, Campaign#1 all open | per-PR CI only | Merge product by product after the platform | High |
| C.6 | Mobile clients use the same identity | Partial | **Partial** | CRM Android = TWA (browser OIDC); HRMS Capacitor still password | none | HRMS needs Custom Tabs + App Link (PR docs) | Med |

## D. Universal Product Login

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| D.1 | Screen 1: Continue as [real user] / Use another account | Implemented | **Implemented** | FE features/signin/ProductAccessChooser.tsx; BE ecosystem_views.pending_view | FE signin.test.tsx; T universal_sso/test_chooser.py; harness K.* | - | Low |
| D.2 | Screen 2: Log in to another account / Create new account / Back | Implemented | **Implemented** | ProductAccessChooser.tsx:211-275 | FE signin.test.tsx (3); T ::test_using_another_account_signs_this_one_out… | - | Low |
| D.3 | A stale session is never offered as Continue as | Implemented | **Implemented** | pending_view 401; sessions.py:116-153 | T ::test_signed_out_and_unknown_requests_get_the_uniform_answer | - | Low |
| D.4 | Sign-up creates no organization unless chosen; invitation creates none | Implemented | **Implemented** | Org only when a company is typed (optional); FE sign-up copy now says so | T phase9 (no company -> no org); T extension/test_v3_invitation_one_organization.py; FE accounts-brand 'sign-up on Accounts…' | Wording fixed ('Create Organization Account' -> 'Create WorkEasy360 Account') | Low |
| D.5 | Intended destination preserved | Partial | **Partial** | state/resume kept through login, two-step, org choice | T test_oidc_flow.py::test_state_is_echoed_verbatim | Lost through sign-up and forgot-password; chooser's Create new account lands on the Store page | Med |
| D.6 | Organization resolved when ambiguous; URL org never trusted | Implemented | **Implemented** | authorization.py:412-475; ecosystem_views.py:366-420 | T ::test_a_person_with_several_organizations_chooses_and_membership_is_checked_again | - | Low |
| D.7 | Membership, seat, trial/free/paid entitlement, security policy checked | Partial | **Partial** | authorization.py:432-513, re-run at redeem | T security/test_ecosystem_security.py (3); test_authorize.py::test_trial_sso_works_until… | Roles are product-side; an organization cannot require two-step; free entitlement untested at SSO | Med |
| D.8 | Distinct outcomes: not purchased / expired / no seat / outage | Partial | **Partial** | protocol.py:69-88 | token-endpoint outage test only | Expired = product_not_entitled; /authorize outage is a 500 | Med |
| D.9 | Signing in never grants paid access | Implemented | **Implemented** | ecosystem_views.py:205-230 | T test_authorize.py::test_automatic_sign_in_is_no_way_around_entitlement (+2) | - | Low |
| D.10 | No repeated chooser right after a fresh central login | Implemented | **Implemented** | ecosystem_views.py:422-447 | T ::test_whoever_just_signed_in_for_this_request_is_not_asked_again | - | Low |
| D.11 | Unverified identity cannot bypass | Implemented | **Implemented** | login refuses email_not_verified | T ::test_a_pending_account_cannot_sign_in… | - | Low |

## E. Global sign-in preference

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| E.1 | Ask-once default for every product, future ones too | Implemented | **Implemented** | contracts/values/sign_in.py:32 | T universal_sso/test_ask_once.py (12); test_future_product.py | - | Low |
| E.2 | Chooser not shown on internal navigation or background requests | Partial | **Partial** | IdP: prompt=none never renders | T test_authorize.py::test_prompt_none_never_shows_a_page | Product-side session handling is the connector's; not verified here | Low |
| E.3 | Per-product overrides optional | Implemented | **Implemented** | sign_in.py:172-191 | T ::test_a_per_product_override_changes_that_product_only | - | Low |

## F. Future product onboarding

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| F.1 | Add Product creates exactly one draft client | Implemented | **Implemented** | commercial/controlplane/sso_setup.py:437-515 | T product_manager/test_sign_in_setup.py | - | Low |
| F.2 | Discovery document fetched and verified | Partial | **Partial** | Platform side built; connector 0.4.0 has the endpoint | test_sign_in_setup.py | No product pins 0.4.0; no v0.4.0 tag; non-Python clients lack it | Med |
| F.3 | Only approved callback addresses (origin) | Partial | **Implemented** | sso_setup._address_problem now compares scheme, host and port | T test_alignment…::test_setup_addresses_must_be_on_the_applications_scheme_host_and_port (6) + 1 | FIXED: port compared | Low |
| F.4 | Domain proof before activation | Implemented | **Implemented** | ecosystem/domains.py | T phase11/test_product_domains.py | Pre-ADR-070 hosts are 'pre_existing', not proven | Low |
| F.5 | Product identity mapping (code, client, issuer, organization claim) | Implemented | **Implemented** | sso_setup.py:292-311; tokens.py:203-204 | T test_manifest.py | - | Low |
| F.6 | Idempotent setup; readiness states | Implemented | **Implemented** | models.py:1039; sso_setup.py:86-92 | T test_sign_in_setup.py (4) | - | Low |
| F.7 | Publish gate: sign-in verified | Partial | **Partial** | catalog/domain.py:935,987 | T test_sign_in_setup.py:637 asserts the lenient behaviour | An awaiting automatic setup counts as met | Med |
| F.8 | Back-channel logout URI registered | Partial | **Partial** | sso_setup.py:335-337 | - | Seed manifests have none | High |

## G. Security and data isolation

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| G.1 | OIDC: PKCE S256, nonce, private_key_jwt | Implemented | **Implemented** | authorization.py:262-289; clientauth.py | T test_oidc_flow.py::test_pkce_is_required (+2) | state optional at the IdP | Low |
| G.2 | Exact redirect URI match | Implemented | **Implemented** | authorization.py:207-218 | T ::test_an_unregistered_redirect_uri_is_never_redirected_to | - | Low |
| G.3 | Code / callback replay blocked | Implemented | **Implemented** | authorization.py:1084-1190 | T ::test_a_code_cannot_be_redeemed_twice (+2) | - | Low |
| G.4 | CSRF on every session write | Implemented | **Implemented** | global middleware | T architecture/test_architecture.py::test_every_session_authenticated_write_is_csrf_protected | - | Low |
| G.5 | Rate limits on login, sign-up, reset, two-step | Partial | **Partial** | public_site_views.py; auth_views.py:140-150 | login/sign-up/two-step tested | forgot/reset/verify/resend limits untested | Low |
| G.6 | Refresh cannot restore a revoked session | Implemented | **Implemented** | no refresh_token grant | T ::test_an_unsupported_grant_type_is_refused | - | Low |
| G.7 | No tokens or secrets in URLs or logs | Implemented | **Implemented** | observability/middleware.py:139; logging.py:40-60 | T ::test_no_authorization_code_or_token_is_ever_logged | - | Low |
| G.8 | Organization isolation, two tabs two organizations | Partial | **Partial** | path-scoped tenancy, RLS | T isolation/test_rls_isolation.py | Two-tab case untested | Low |
| G.9 | Safe account switching clears the previous person's state | Partial | **Implemented** | FE ProductAccessChooser forgetAccountState on switch/create | FE signin.test.tsx (new) | FIXED | Low |
| G.10 | Safe organization switching | Partial | **Partial** | restart with the chosen organization | T ::test_the_chosen_organization_restarts_the_product_with_it | Product-side cache clearing not verified | Low |
| G.11 | Remember me on the central sign-in | Partial | **Implemented** | FE AccountsLogin.tsx sends remember; BE _apply_remember_me | T admin_panel/test_login.py (3); FE accounts-brand (new); browser V.7 | Added to the Accounts login | Low |

## H. Help and Support

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| H.1 | Account-security support, signed in and signed out | Partial | **Partial** | administration/account_security.py:428-472; FE AccountHelp.tsx | T ::test_support_answers_the_same_for_any_address… | Case not tied to the signed-in user id | Low |
| H.2 | Routed to restricted central support, never partners | Partial | **Partial** | admin_console_views.py:198-236 | only a customer-403 test | Add partner-refused / staff tests | Med |
| H.3 | Help & Support on every Accounts page | Implemented | **Implemented** | FE CenterShell.tsx (top bar + sidebar card) | FE 'opens first…' | - | Low |

## UI. Reference images

| ID | Requirement | At main | Now | Evidence | Tests | Gap / change | Risk |
|---|---|---|---|---|---|---|---|
| U.1 | Accounts login matches reference image 1 | Missing | **Implemented** | FE features/auth/AccountsLogin.tsx (+ .module.css) | FE accounts-brand.test.tsx (5); browser V.1-V.6 | NEW | Low |
| U.2 | Accounts landing matches reference image 2 within the four-menu rule | Missing | **Implemented** | FE CenterShell.tsx, ProfileOverview.tsx, AccountShell/AccountOverview.module.css | FE accounts-center.test.tsx (3); browser V.8-V.15 | NEW; Home/AI/bell/mobile left out (conflicts) | Low |

## Reference-image conflicts (not built)

- **"Home" menu**: V3 04 allows exactly four menus and opens My Profile first. My Profile carries the overview instead.
- **AI Assistant button**: organization-scoped and credit-metered. V3 10.3 keeps account security independent of AI.
- **Notification bell**: Accounts has no personal notification feed. Settings holds the notification choices.
- **Mobile "Verified"**: no phone field or SMS provider exists. V3 8.2 forbids implying phone sign-in.
- **Two-step "Enabled" and "Secure Account"**: shown only from the account's real state.

