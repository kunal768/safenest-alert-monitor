# Compliance check: rubric and design report (Section IV)

Source of truth: `SafeNest_component_design.pdf`, Section IV "Alert Tracking and Monitoring" (pp. 58–75), and the CMPE 281 component implementation rubric.

Legend: ✅ implemented as designed · 🟡 implemented with a documented simplification (see [DECISIONS.md](DECISIONS.md)) · ➖ out of scope for this component

## 1. Rubric

| Criterion | Pts | Evidence |
|---|---|---|
| **Component live demo**: complete and correct implementation of the required functions with UI and backend | 3 | All 12 functions of Table 37 run end to end in the React UI against the FastAPI backend (table below). AI classification uses a local LLM through Ollama; model management covers evaluate, health-checked deploy, failed deploy and rollback. One-click scenarios on the Operations page. `make test` shows 90+ tests passing with about 92 % coverage. A Playwright smoke test drives the UI path. |
| **Component scenario demo, video recording** | 2 | `docs/video/safenest-demo-hd.mp4`: about 5 minutes, narrated, all 9 scenarios plus the unit-test run. |

## 2. Functions (Table 37)

| ID | Function | Status | How |
|---|---|---|---|
| F3.1 | Event ingestion | ✅ 🟡 | `POST /events`; checks house, device and the device-belongs-to-house rule; a repeated `eventId` returns the stored result (`duplicate: true`) and never creates a second effect; stored, then classified. HTTP instead of MQTT. |
| F3.2 | AI classification | ✅ 🟡 | Ollama `/api/chat`, JSON mode, temperature 0, 8 s timeout, 2 retries with backoff plus one repair prompt, Pydantic validation; label, confidence, model version, prompt version and latency stored per event. Text description instead of a raw clip. |
| F3.3 | Alert decision | ✅ | Table 43 + quiet hours (house-local time) + merge into an open alert of the same type and room within 10 minutes, each `eventId` applied once. |
| F3.4 | Notification | ✅ 🟡 | Table 44; priority-1 contact first; push → SMS → email fallback, every attempt logged; all failed → immediate escalation; dashboard push over WebSocket. `LogNotifier` instead of SNS/SES. |
| F3.5 | Alert lifecycle | ✅ | Table 45 as code, optimistic `version` (409), who and when kept, every change audited, optional note. |
| F3.6 | Escalation | ✅ | Per-alert timer using the house rule's acknowledge timeout (capped for the demo); next contact, then the care center; acknowledge cancels it; timers re-armed after a restart. |
| F3.7 | Alert rules | ✅ | `GET/PUT /houses/{id}/alert-rules` with version check and service limits (emergency cannot be disabled and needs a channel, confidence floor 0.5, ack timeout 10–3600 s, emergency ≤ 300 s). |
| F3.8 | Pattern learning | ✅ 🟡 | Hourly profile per house-local day (fed by every event), 14-day baseline, deviation score 0–1. Runs on demand (button / `POST /patterns/run`) instead of a nightly cron. |
| F3.9 | Pattern alerts | ✅ | Table 46: ≥ 7 baseline days; no activity by noon → safety alert; score > 0.8 on two days → "routine changed" safety alert; else learn only. |
| F3.10 | Device alerts | ✅ | `POST /devices/{id}/missed` accepts the `device.missed` payload (`eventId`, `lastSeen`, `offlineSince`); warning when offline > 10 min. Demo button fast-forwards the clock. |
| F3.11 | Alert search and detail | ✅ 🟡 | Filters (house, severity, type, status, from, to) + paging; detail with event, confidence, model version, notification log, status history, and a clip URL signed for 5 minutes (local file instead of S3). |
| F3.12 | AI model management | ✅ | Register / sync from `ollama list`; background evaluation (accuracy, per-label precision/recall, confusion table, latency, error rate); deploy is health-checked (tag exists + warm-up JSON) before traffic moves; failure → Failed, old model keeps serving; rollback; live latency/p95/error rate. |

## 3. Web service APIs (Table 38)

| ID | Endpoint | Status | Notes |
|---|---|---|---|
| L1 | `GET /alerts` | ✅ | houseId, severity, type, status, from, to, page, size; owners scoped to own houses |
| L2 | `GET /alerts/{id}` | ✅ | event, confidence, modelVersion, `clipUrl` (5 min), notifications, history; owner, staff, **link** |
| L3 | `PATCH /alerts/{id}` | ✅ | acknowledge / resolve / falseAlarm, `note`, `version`; 409 when not allowed |
| L4 | `POST /alerts/{id}/escalate` | ✅ | `target` = family / careCenter / emergency → **202** + notification ids; 409 when closed |
| L5 | `GET /alerts/open` | ✅ | `houseIds` list; used for map colours |
| L6 | `GET /houses/{id}/alert-rules` | ✅ | eventClass, minConfidence, severity, quiet hours, ackTimeoutSec, channels |
| L7 | `PUT /houses/{id}/alert-rules` | ✅ | 400 below service limit, 409 stale version |
| L8 | `GET /houses/{id}/patterns` | ✅ 🟡 | today vs baseline + deviation; per house, not per resident |
| L9 | `GET /alerts/{id}/notifications` | ✅ | channel, contact, status, sentAt (+ care-center link) |
| L10 | `GET /ai/models` | ✅ | name, version, task, status, latency, errorRate, accuracy |
| L11 | `POST /ai/models/{id}/deploy` | ✅ 🟡 | deploy / rollback; returns when the health check finishes (200) rather than 202, and the UI shows *Deploying* live over the WebSocket |
| L12 | `GET /health` | ✅ | deps, queue depth, active model; 503 when the database is down |

## 4. Business logic (Section D)

| Rule | Status | Test |
|---|---|---|
| Table 43 severity (R1–R7, emergency below high mark → security) | ✅ | `test_rules.py::test_table43_*`, `test_quiet_hours_suppress_safety_only` |
| Table 44 notification (channels, first contact, subscription) | ✅ | `test_table44_channels`, `test_inactive_subscription_is_dashboard_only` |
| Table 45 status changes | ✅ | `test_table45_legal_and_illegal`, `test_lifecycle_ack_resolve_and_illegal_409` |
| Table 46 pattern alerts | ✅ | `test_patterns.py`, `test_pattern_table46` |
| 4.5: closed alert read-only | ✅ | `test_lifecycle_ack_resolve_and_illegal_409` |
| 4.5: merge same type + room for 10 minutes | ✅ | `test_merge_*` |
| 4.5: all channels fail → escalate at once | ✅ | `test_all_channels_fail_escalates_immediately` |
| 4.5: model gets traffic only after health check; old version stays | ✅ | `test_broken_tag_fails_and_old_model_keeps_serving` |
| 4.5: clip links expire after 5 min; care-center link opens one alert only | ✅ | `test_clip_token_signed_and_expiring`, `test_care_center_link_opens_only_that_alert_and_may_only_acknowledge` |
| 2.4: model failure + loud event (> 85 dB) still warns | ✅ | `test_fail_loud_*` |
| 2.2: retries reuse eventId and never double-count | ✅ | `test_duplicate_flag_and_no_double_count` |
| Owners only their own houses | ✅ | `test_owner_*` |

## 5. User flows (2.5) and GUI (Section E)

| Item | Status |
|---|---|
| Receive and act: notification → link or dashboard → detail → play clip → Acknowledge (+ note) → Resolve / False alarm | ✅ |
| Escalate by hand → choose target | ✅ |
| Change rules → edit event class, confidence, quiet hours → Save | ✅ |
| Deploy a model → choose a version → Deploy or Roll back | ✅ |
| Fig. 69 partition tree (alert list, detail, admin) | ✅ Alerts, Alert detail, House rules, AI models (+ Patterns, Operations) |
| Fig. 70 storyboard (SMS link → detail → clip → acknowledge with note → resolve) | ✅ |
| Fig. 71 alert detail (clip waveform, notifications, actions) | ✅ |
| Fig. 72 alert rules (event, confidence, severity, quiet hours, notifications, timeouts) | ✅ |
| Fig. 73 AI models (model, version/task, latency/errors, state, Deploy version, Roll back, safe-actions note) | ✅ |
| Style: red/yellow/green/gray always with a word, large controls, phone layout | ✅ |

## 6. Known simplifications (all deliberate, in DECISIONS.md)

MQTT → HTTP · Redis Streams → in-process asyncio · PostgreSQL/MongoDB/S3 → SQLite and local files · SNS/SES → `LogNotifier` · audio/video model → text description classified by a local LLM · nightly job → on-demand button · pattern score is a normalised L1 distance (no first/last-activity term) and per house rather than per resident · flat error body `{error, message}` · L11 completes synchronously · cloud deployment is implemented as files and gated by `ENABLE_CLOUD_DEPLOY=false`.

➖ Not in this component: device registration (F2), dashboard map/floor plan/reports (F1), data access API and backups (F4).
