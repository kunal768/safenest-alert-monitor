# Traceability F3.1–F3.12

| ID | Function | Code | Tests | Demo scenario |
|---|---|---|---|---|
| F3.1 | Event ingestion + dedupe | `backend/app/pipeline.py` `ingest_event` (unique `event_id` PK, house/device validation), `POST /events`, `simulator/send_event.py` | `test_pipeline.py::test_dedupe_same_event_id`, `test_duplicate_flag_and_no_double_count`, `test_unknown_device_and_wrong_house_rejected` | 3 |
| F3.2 | AI classification (Ollama) | `ai/classifier.py` `OllamaAdapter` (JSON format, temp 0, 8 s timeout, 2 retries + repair prompt, room context), `MockAdapter`, `OutageAdapter`; latency/model/prompt stored on `events` | `test_classifier.py` (httpx MockTransport payload, repair, invalid label, timeout → unclassified, outage switch); live: `pytest -m ollama` | 1, 5, 6, 7 |
| F3.3 | Alert decision + merge | `rules.py` `decide_severity` (Table 43, quiet hours in house-local time), `should_merge`; `pipeline._create_or_merge_alert` | `test_rules.py::test_table43_*`, `test_quiet_hours_*`, `test_merge_window_same_type_room`; `test_merge_ten_coughs_one_alert`, `test_merge_does_not_cross_rooms`, `test_merge_window_expired_creates_new_alert`, `test_quiet_hours_use_house_local_time` | 1, 3, 4 |
| F3.4 | Notification | `notify.py` `plan_and_notify` (Table 44, rule channels, fallback push→SMS→email), `LogNotifier` (`NOTIFY_FAIL_CHANNELS` to simulate provider failure), `AwsNotifier` stub | `test_table44_channels`, `test_push_and_sms_fail_falls_back_to_email`, `test_safety_push_failure_falls_back_to_sms`, `test_all_channels_fail_escalates_immediately`, `test_inactive_subscription_is_dashboard_only`, `test_rule_channels_limit_paging` | 1, 2 |
| F3.5 | Alert lifecycle | `lifecycle.apply_action` (Table 45, optimistic `version`, 409 on illegal/closed/stale), audit per change | `test_table45_legal_and_illegal`, `test_lifecycle_ack_resolve_and_illegal_409`, `test_glass_scenario_end_to_end` | 1 |
| F3.6 | Escalation | `pipeline.escalate_now` (next contact → care center; targets family / careCenter / emergency), `pipeline.ack_timeout` (house rule timeout), `lifecycle.auto_escalate` timers, manual `POST /alerts/{id}/escalate`, care-center one-alert link (`/link/{id}`) | `test_escalation.py` (full chain → ack → resolve; ack stops timer; manual escalate; targets; care-center link; rule timeout; restart re-arm) | 2 |
| F3.7 | Alert rules | `GET/PUT /houses/{id}/alert-rules`, `rules.validate_rules_limits` (emergency can't be disabled, needs a channel, min confidence ≥ 0.5), version conflict 409 | `test_cannot_disable_emergency_rule`, `test_rules_version_conflict_and_validation`, `test_rules_limits*` | 4 |
| F3.8 | Pattern learning | `pipeline._record_activity` (hourly profile per event), `patterns.pattern_summary` / `baseline_hours` (14-day baseline), `rules.deviation_score` | `test_deviation_score_range`, `test_event_updates_today_activity_profile`, `test_pattern_endpoint_shape` | 8 |
| F3.9 | Pattern alerts | `patterns.run_patterns` + `rules.pattern_decision` (Table 46), `POST /patterns/run` | `test_patterns.py` (noon rule after noon, routine changed, < 7 baseline days → none, normal day → learn), `test_pattern_table46` | 8 |
| F3.10 | Device alerts | `patterns.mark_device_missed`, `POST /devices/{id}/missed` (fast-forward flag) | `test_device_missed_warning`, `test_info_alerts_do_not_start_escalation` | 8 |
| F3.11 | Search / detail / clip | `GET /alerts` (filters incl. from/to + paging), `GET /alerts/open`, `GET /alerts/{id}` (event, merged events, history, clipUrl), `GET /alerts/{id}/clip` (HMAC token, 5 min) | `test_alert_paging`, `test_open_alerts_and_time_filters`, `test_clip_token_signed_and_expiring`, `test_owner_house_scoping` | 1 |
| F3.12 | AI model management | `registry.py`: register / sync from `ollama list`, background `evaluate_model` (accuracy, per-label P/R, confusion, latency, error rate), `deploy_model` (Registered/Retired/Failed → Deploying → Active / Failed, health check = tag exists + warm-up JSON), `rollback_model`, rolling live metrics | `test_registry.py` (eval, background eval, deploy B → rollback A, broken tag Failed + A serving, health check, illegal states, duplicate register, sync, owner 403), `test_model_state_machine*` | 6 |

Cross-cutting:

| Rule | Test |
|---|---|
| Same eventId never creates a second alert or double-counts | `test_dedupe_same_event_id`, `test_duplicate_flag_and_no_double_count` |
| Merge only open, same type + room, within 10 min | `test_merge_window_same_type_room`, `test_merge_does_not_cross_rooms`, `test_merge_window_expired_creates_new_alert` |
| Severity per Table 43; emergency below high mark → security | `test_table43_*` |
| Quiet hours suppress only info/safety | `test_quiet_hours_suppress_safety_only`, `test_quiet_hours_suppress_cough`, `test_quiet_hours_use_house_local_time` |
| Channel fallback push → SMS → email; all fail → immediate escalation | `test_push_and_sms_fail_falls_back_to_email`, `test_all_channels_fail_escalates_immediately` |
| Closed alerts read-only; illegal transition → 409 | `test_lifecycle_ack_resolve_and_illegal_409`, `test_manual_escalate_pages_next_contact` |
| Owners only see their own houses | `test_owner_house_scoping`, `test_owner_cannot_touch_other_house_or_simulator`, `test_owner_cannot_manage_models` |
| New model gets traffic only after health check; old model stays on failure | `test_broken_tag_fails_and_old_model_keeps_serving`, `test_health_check_requires_valid_json_from_warmup` |
| LLM failure + loud event (> 85 dB) still raises a warning | `test_fail_loud_warning`, `test_fail_loud_with_simulated_outage`, `test_quiet_unclassified_event_raises_nothing` |
| Pattern alert only with ≥ 7 baseline days | `test_no_pattern_alert_with_fewer_than_7_baseline_days` |
| Every status change, rule change and model action is audited | `test_audit_on_status_and_rules`, `test_deploy_b_then_rollback_to_a` (audit actions), history assertions in `test_glass_scenario_end_to_end` |
| Live UI updates | `test_live_websocket_pushes_new_alerts`; `web/e2e/smoke.mjs` (Playwright, Phase 6 path) |
| Cloud flag | `test_features_flag`, Operations card, `make cloud-check` |

Full rubric + PDF Section IV check: [COMPLIANCE.md](COMPLIANCE.md).
