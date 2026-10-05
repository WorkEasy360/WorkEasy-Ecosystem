# Phase 8 — Security, privacy, retention and audit

## 1. Who can do what (Tasks 62, 64)

| Actor | Can | Cannot |
|---|---|---|
| **Product** (Phase 3 client credential) | register/retire its own skills and metrics; invoke own, shared and platform skills; index, delete and query its own knowledge; query own and shared metrics — each for an organization it is entitled to | touch another product's namespace, knowledge or metrics; name an endpoint, a model outside the skill's contract, or a price; see a provider credential; read another product's job |
| **Organization member** with `platform.ai.view` | see AI settings, usage, recent requests (metadata only) | change anything |
| **Organization administrator** with `platform.ai.manage` (dangerous: step-up) | switch AI off, cap monthly spend, restrict providers, allow cross-vendor fallback | exceed the platform cap, use premium models the platform has not allowed, lift a platform suspension |
| **Organization member** with `platform.analytics.view` | query the organization's metrics across its products | read raw facts or another organization's data |
| **Staff** with `platform.staff_ai.view` (live StaffGrant) | read the registry, prices, provider health, an organization's AI state | change anything |
| **Staff** with `platform.staff_ai.manage` (dangerous: StaffGrant + AAL2) | switch providers and models on/off, add models (created disabled), publish prices, set an organization's platform cap, premium allowance and suspension — each with an audited reason | enter a URL or a credential; re-price history; read prompts, results or documents |

`owner` and `admin` system roles receive the three organization permissions
(migration `authz.0007`). The Phase 7 staff role matrix gains the two staff
permissions; `manage` implies `view`, and both require the control-plane
session (Phase 7 staff model, D-016).

## 2. Security test matrix (Task 63)

| Threat | Test |
|---|---|
| another tenant's nearest passage is returned | `test_the_nearest_passage_in_another_tenant_is_never_returned` |
| another product's knowledge in the same org | `test_a_product_never_searches_another_products_knowledge` |
| a product edits another product's documents | `test_a_product_cannot_touch_another_products_documents` |
| tag-restricted content reaches the wrong user | `test_tagged_documents_are_candidates_only_for_callers_with_a_tag` |
| a product registers in another's namespace | `test_a_product_cannot_register_outside_its_namespace`, `test_a_product_owns_only_its_own_namespace` |
| a private skill is invoked by another product | `test_a_private_skill_is_invisible_to_other_products_until_shared` |
| another product's job is polled | `test_an_async_job_runs_on_the_queue_and_is_polled_by_its_product_only` |
| another product's metric is guessed | `test_guessing_another_products_metric_key_learns_nothing` |
| an unentitled product gets AI or data | `test_a_product_without_ai_in_the_plan_gets_no_ai`, `test_a_product_not_entitled_to_the_organization_reads_nothing`, `test_indexing_without_ai_entitlement_is_refused` |
| organization existence is probed | `test_an_unknown_organization_is_indistinguishable_from_an_unentitled_one` |
| a suspended organization spends | `test_a_suspended_organization_starts_no_ai_work`, `test_a_job_whose_entitlement_lapsed_while_queued_spends_nothing` |
| prompt injection escalates | `test_a_hostile_document_is_quoted_data_and_escalates_nothing`, `test_untrusted_text_cannot_close_its_delimiter` |
| a model is offered tools | `test_the_anthropic_request_carries_no_tools_and_the_key_only_in_its_header`, `test_the_openai_requests_carry_no_tools`, fitness `test_no_model_is_ever_offered_a_tool` |
| secrets reach a provider or the index | `test_secrets_in_the_input_never_reach_the_provider`, `test_secrets_are_redacted_before_they_are_stored_or_embedded`, `test_forbidden_secrets_never_leave_the_platform` |
| a fabricated citation or a script link | `test_citations_are_never_fabricated`, `test_a_model_cannot_invent_a_source`, `test_citation_links_are_https_and_never_scripts` |
| a stored prompt, price, metric or fact is rewritten | `test_a_stored_prompt_cannot_be_rewritten_even_by_sql`, `test_a_new_price_is_a_new_version_and_history_is_never_repriced`, `test_a_registered_metric_version_is_immutable`, `test_a_raw_fact_cannot_be_rewritten` |
| the organization sets the platform's fields | `test_the_organization_cannot_set_the_platforms_fields` |
| AI settings changed without step-up | `test_changing_ai_settings_needs_step_up` |
| staff act without a live grant / without AAL2 | `test_the_registry_needs_a_live_staff_grant`, `test_switching_a_provider_off_needs_step_up_and_stops_its_use` |
| a credential is exposed | `test_the_registry_shows_whether_a_credential_is_configured_never_its_value`, `test_an_administrator_sees_limits_usage_and_providers_but_no_secret`; fitness: no secret-named column |
| keyspace explosion via dimensions | `test_unbounded_dimension_values_collapse_into_other_and_unknown`, `test_dimension_values_outside_the_enumeration_cannot_grow_the_keyspace` |
| unbounded queries | `test_queries_outside_the_bounds_are_refused`, `test_a_query_that_could_return_too_many_rows_is_refused`, `test_retrieval_is_bounded` |
| AI becomes an authority | import-linter `ai-is-never-an-authority`, pinned by `test_the_ai_authority_contract_cannot_be_removed_quietly` |
| product-specific code in the platform | fitness `test_intelligence_defines_no_product_business_meaning` |

The Phase 7 matrix (`tests/phase7/test_security_matrix.py`) lists
`platform.staff_ai.manage` among the dangerous staff permissions.

## 3. What is stored, and for how long (Tasks 46, 83, 84)

| Data | Kept | Where / setting |
|---|---|---|
| prompts and raw model responses | **never stored** | — |
| a validated result (for idempotent replay and job polling) | 15 min sync, 24 h async | `ai_execution_content`, `AI_RESULT_RETENTION_S`, `AI_JOB_RESULT_RETENTION_S` |
| an async job's input | until the job finishes, at most 24 h | `ai_execution_content` |
| execution metadata (skill, model, tokens, cost, outcome, timings, counts) | 400 days | `AI_EXECUTION_RETENTION_DAYS` |
| usage lines (one per provider call) | with their execution | — |
| budget periods | 36 months | — |
| document content | only until its version is indexed (needed to retry) | `rag_document_version.content` |
| chunks and embeddings | only while their version serves | — |
| document tombstones, finished versions | 30 days | `RAG_TOMBSTONE_RETENTION_DAYS` |
| analytics facts | 400 days (only once projected) | `ANALYTICS_FACT_RETENTION_DAYS` |
| hourly / daily aggregates | 90 days / 5 years | `ANALYTICS_*_RETENTION_DAYS` |

The daily `intelligence.prune` task (heavy) visits each organization in its
own scope, in bounded batches, isolating failures. Registry, skills, prices
and metric definitions are configuration and are not pruned.

## 4. Logs (Task 47)

**Never logged, by default or at any level:** a prompt, a response, a
retrieved passage, a document body, an embedding input, a skill's input, a
provider key, a provider's error body. Log lines carry ids, counts, sizes,
durations, outcomes and reason codes. Enforced by the fitness test
`test_nothing_in_intelligence_logs_content`, and metric labels are bounded (no
execution, document, skill, source or metric ids — `FORBIDDEN_LABELS`).
There is no debug switch that turns content logging on.

## 5. Deletion and organization purge (Tasks 37, 85)

- **A product deletes a document** → chunks purged in the same transaction
  ([05-rag.md](05-rag.md) §8).
- **A product revokes a source** → unretrievable at commit; purged behind a
  timer, which holds even while the organization is suspended
  (`test_revocation_holds_while_the_organization_is_suspended`).
- **Organization purge is discoverable.** Every Phase 8 tenant table is a
  `TenantScopedModel` with `organization_id`, RLS and composite foreign keys
  (migration `tenancy.0008`); the system check `check_tenant_scoped_models`
  refuses a tenant table without them. The platform's purge of an
  organization therefore finds every AI execution, usage line, result, budget
  row, source, document, version, chunk, fact and aggregate by
  `organization_id`, with no Phase 8-specific step.
- **Metering survives the write block.** A suspended or read-only
  organization's in-flight executions must still settle, and a deletion must
  still purge, so the metering and RAG tables are exempt from the tenancy
  write block (`tenancy_write_block_exempt`) — new work is refused by the
  access decision instead (debt register D-023).

## 6. Provider-side deletion (Task 86)

Content sent to a vendor is governed by the vendor account's terms, which the
provider row describes (input retention, training use). **The platform cannot
delete what a vendor retained**: deleting a document removes it from the
platform's index and storage, not from a vendor's logs. This is stated to the
organization's administrator on Settings → AI, and why the platform sends
nothing but what the caller supplied or retrieved under authorization.

## 7. Audit (Task 65)

| Action | Audit event |
|---|---|
| organization changes AI settings | `ai.settings.updated` (tenant audit, old → new, revision) |
| staff switch a provider | `ai.provider.status_changed` (with reason) |
| staff add or change a model | `ai.model.created`, `ai.model.updated` |
| staff publish a price | `ai.price.published` |
| staff change an organization's platform policy | `ai.policy.changed_by_staff` — in the platform audit **and** the organization's own audit, so the customer sees that staff acted |

Product-side registrations (skills, metrics) are recorded as immutable
versioned rows with their owner and creation time, which is the record. Every
AI execution — refused after reservation, failed, expired or succeeded — is
itself an audit-grade metering record: who (product, organization), what
(skill version, model, price version), how much, and the outcome.
