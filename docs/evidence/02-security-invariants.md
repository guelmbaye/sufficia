# 02 — Security invariants

Generated 2026-10-01T17:35:24.763993+00:00 by `python -m sufficia.evidence_pack` from a pytest run (JUnit XML).
Commit: `a445e5face24d16aca745fbd15f0f6f1cbd9ce5f` · SUFFICIA 1.0.0

**Result: 15/15 invariants PASS** (46/46 mapped tests passing).

| ID | Statement | Result | Tests |
|---|---|---|---|
| INV-01 | No authorization without AUTHORIZE. | PASS | `test_non_authorize_decisions_are_refused` (engine) — PASS<br>`test_every_non_authorize_state_is_403_and_never_reaches_the_chain` (api) — PASS<br>`test_deny_decision_has_no_action_and_no_authorization` (blockchain) — PASS<br>`test_tampered_decision_snapshot_cannot_be_authorized` (api) — PASS |
| INV-02 | No AUTHORIZE with missing required evidence. | PASS | `test_missing_mandatory_evidence_abstains` (engine) — PASS<br>`test_p1_adding_unsupported_mandatory_requirement_breaks_authorize` (engine) — PASS |
| INV-03 | No AUTHORIZE with stale required evidence. | PASS | `test_freshness_boundaries` (engine) — PASS<br>`test_stale_value_is_not_evaluated_as_current_truth` (engine) — PASS<br>`test_p2_staling_all_evidence_of_a_requirement_breaks_authorize` (engine) — PASS |
| INV-04 | No AUTHORIZE with unresolved critical conflicts. | PASS | `test_conflict_boundary_is_strictly_greater_than_tolerance` (engine) — PASS<br>`test_conflict_precedes_threshold_failure` (engine) — PASS<br>`test_two_source_mode_enables_conflict_detection` (evidence) — PASS |
| INV-05 | No AUTHORIZE when a mandatory threshold fails. | PASS | `test_p3_failing_threshold_breaks_authorize` (engine) — PASS<br>`test_negative_inflow_is_valid_and_fails_threshold` (engine) — PASS |
| INV-06 | An authorization is bound to one action. | PASS | `test_verify_binding_detects_action_mutations` (engine) — PASS<br>`test_fuzz_different_amount_gives_different_action_hash` (contracts) — PASS<br>`test_attacker_bypassing_the_backend_is_stopped_by_the_contract` (api) — PASS<br>`test_tampered_action_in_database_is_refused_before_submission` (api) — PASS |
| INV-07 | An authorization is bound to one Evidence Contract. | PASS | `test_decision_for_another_contract_is_refused` (engine) — PASS<br>`test_authorize_rejects_contract_hash_mismatch` (contracts) — PASS<br>`test_p6_changing_threshold_changes_contract_hash` (engine) — PASS |
| INV-08 | An authorization is bound to one Evidence Set. | PASS | `test_single_mutations_of_the_evidence_break_the_binding` (engine) — PASS<br>`test_p5_changing_evidence_changes_evidence_root` (engine) — PASS |
| INV-09 | An authorization expires no later than its supporting evidence. | PASS | `test_authority_window_bounded_by_oldest_mandatory_evidence` (engine) — PASS<br>`test_deadline_beyond_evidence_expiry_is_refused` (engine) — PASS<br>`test_authorize_cases_expire_no_later_than_supporting_evidence` (engine) — PASS<br>`test_expired_authority_never_reaches_execution` (api) — PASS<br>`test_execute_expired_authorization` (contracts) — PASS |
| INV-10 | An authorization can be consumed only once. | PASS | `test_target_revert_leaves_authorization_unconsumed` (contracts) — PASS<br>`test_reuse_is_rejected` (blockchain) — PASS<br>`test_attacker_bypassing_the_backend_is_stopped_by_the_contract` (api) — PASS |
| INV-11 | The LLM cannot issue or override execution authority. | PASS | `test_llm_override_is_irrelevant_to_the_decision` (engine) — PASS<br>`test_llm_output_cannot_be_smuggled_in_as_evidence` (engine) — PASS<br>`test_override_that_relaxes_the_intent_is_rejected` (api) — PASS<br>`test_prompt_injection_in_the_intent_creates_no_authority` (api) — PASS |
| INV-12 | System uncertainty fails closed. | PASS | `test_pathological_numbers_fail_closed` (engine) — PASS<br>`test_adapter_bug_fails_closed_and_visible` (evidence) — PASS<br>`test_gateway_all_rpcs_down_fails_closed` (blockchain) — PASS |
| INV-13 | Replay with identical canonical inputs is deterministic. | PASS | `test_every_state_is_exercised_and_replayed_cross_process` (engine) — PASS<br>`test_f1_high_precision_values_replay_identically` (engine) — PASS<br>`test_f2_address_valued_claims_replay_identically` (engine) — PASS |
| INV-14 | Human explanations faithfully represent machine decision reasons. | PASS | `test_explanation_deny_contains_observed_and_required_values` (engine) — PASS<br>`test_unfaithful_explanation_is_detected` (engine) — PASS<br>`test_f10_threshold_message_shows_the_violation` (engine) — PASS |
| INV-15 | Removing valid mandatory evidence cannot increase authority. | PASS | `test_a15_monotonic_safety_on_arbitrary_worlds` (engine) — PASS<br>`test_a15_removing_mandatory_evidence_from_authorize_never_authorizes` (engine) — PASS |

Re-run: `cd apps/api && python -m pytest tests/api/test_api_security.py::test_attacker_bypassing_the_backend_is_stopped_by_the_contract tests/api/test_api_security.py::test_every_non_authorize_state_is_403_and_never_reaches_the_chain ...` (full list above).
