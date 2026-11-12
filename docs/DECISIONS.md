# Decisions vs SafeNest_component_design.pdf

Simplifications for a locally runnable demo. Each still satisfies the *function* in Table 37.

| PDF | Demo | Why |
|---|---|---|
| MQTT from home gateway | `POST /events` HTTP (UI Simulator or `simulator/send_event.py`) | One process, no broker |
| Redis Streams classification queue | `asyncio` tasks inside FastAPI (classification, escalation timers, background evaluation) | Same async pipeline, no extra service |
| Postgres + Mongo | SQLite | One file, `make seed` |
| S3 audio clips + signed URLs | `simulator/clips/*.wav` (synthesised per scenario) + 5-minute HMAC token | No AWS account |
| Raw audio classifier | **Text sound description → Ollama chat JSON** | Ollama is a local LLM, not an audio model. Stated in the demo. Room, level and duration are passed as context. |
| SMS/push/email providers | `LogNotifier` writes `notifications` rows | Timeline is visible; `NOTIFY_FAIL_CHANNELS=push,sms` simulates provider outages to show fallback; `AwsNotifier` is a stub behind `NOTIFIER=aws` |
| Microservices | Single FastAPI app | Rubric is functions + AI, not ops topology |
| Cloud GPU host for the model | Ollama on the laptop | Cloud would need a GPU/CPU host; noted on the Operations card |
| Terraform apply / ECS / EKS | Feature-flagged files only (`ENABLE_CLOUD_DEPLOY=false`); Terraform resources use `count = var.enable_cloud_deploy ? 1 : 0` and the CI deploy job is skipped unless the repo variable is `true` | Implemented, not deployed |
| Cognito login | Seeded users + bearer tokens, role switch in the header | Demo needs no sign-up |

## AI

- **Confidence from an LLM is not calibrated.** Values are clamped to [0, 1]; house rules still apply min-confidence and the emergency high mark (0.85). Shown under every AI explanation.
- **Prompt v1** was tuned once against the evaluation set before release: the first draft labelled "dragging furniture and laughing" as `unknown_voice`, so the prompt now says laughter/chatter are normal and `unknown_voice` needs an unfamiliar or suspicious voice. With `qwen2:7b` the set scores 36/40; the misses are deliberately tricky household sounds.
- **Model version = Ollama tag + prompt version.** Registering the same tag with the same prompt twice is a 409.
- **Health check** = tag exists in `GET /api/tags` + warm-up prompt returns valid JSON. The warm-up uses a 90 s timeout because a cold model load can take far longer than the 8 s per-event timeout; the active model is also warmed at startup.
- **Simulated outage**: the Operations switch (and the *Fail loud* scenario) point the Ollama adapter at a closed port, so the real retry/timeout path runs and ends `unclassified`. In mock mode an `OutageAdapter` does the same.
- **Evaluation** runs as a background task and pushes progress over the WebSocket; live latency/error metrics come from the last 200 production events per tag, separate from the evaluation numbers.

## Alerts

- **Time zone.** Houses have a time zone (seed: `America/Los_Angeles`). Quiet hours, Table 44 "night", activity profiles and the noon rule all use house-local time; events are stored in UTC.
- **Escalation timeout:** 30 s in `make run`, 10 s in the recorded video, configurable with `ESCALATION_TIMEOUT_SEC` (PDF uses longer operational timers). Timers run only for **emergency and security** alerts (the ones that page someone); safety/info alerts wait on the dashboard. Escalation always pages the next contact by push + SMS, ending at the care center.
- **Care-center link** (PDF 1.4, 4.5): the SMS/email to the care center carries `/link/{alertId}?t=…`, an HMAC token valid 24 h that opens only that alert without a login and may only acknowledge.
- **Ack timeouts per rule** (Fig. 72): fall/scream/glass 120 s, door/unknown voice 180 s, call for help 300 s, cough/long silence 600 s. `ESCALATION_TIMEOUT_SEC` caps them so the demo escalates in seconds; set it to 3600 to use the house values.
- **L4 targets**: `family` = next contact, `careCenter` = jump to the care center, `emergency` = care center + a logged “dispatch emergency services” call (simulated).
- **Defaults follow PDF 4.1**: call for help is *safety* (not emergency).
- **All channels fail** for a contact → audit `notify_failed` and escalate immediately to the next contact (repeats until someone is reachable or the care center is reached).
- **Fail-loud** warnings use severity *safety* (push by day, dashboard at night) with type `unclassified`. Device-offline warnings use severity *info* (dashboard).
- **Rule channels** narrow Table 44's channels (e.g. SMS only); an emergency rule must keep at least one channel.
- **Pattern learner.** Every event increments the house-local hourly profile for today. The baseline is the latest 14 days flagged as baseline; the seed adds 14 normal days plus two abnormal recent days (night activity) so "routine changed" is demoable. Today's deviation only compares hours elapsed so far; the noon rule only fires after 12:00 local.
- **Device missed** fast-forwards the offline clock past the 10-minute threshold (`FAST_FORWARD_DEVICE=true`); any new event from the device clears it.

## UI

- Browser page loads of UI routes (`/alerts/...`, `/models`, …) get `index.html` from both Vite and FastAPI, while API GETs (fetch with `Accept: application/json`) still reach FastAPI; `/health` and `/config/features` open as JSON in the browser.
- Python pins are lower bounds (`>=`) so `make setup` works on Python 3.11–3.14 (the old exact pins had no wheels for 3.14).
