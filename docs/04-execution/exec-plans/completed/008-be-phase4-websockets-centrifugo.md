# Execution Plan: 008 — WebSockets / Real-Time Updates (Centrifugo)

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: HIGH

---

## Goal

Integrate Centrifugo v5 so the frontend receives live updates (result submissions, run status changes, test case edits) without polling.

---

## Context

The frontend already has `useCentrifuge` composable and subscribes to `project:{id}` and `testrun:{id}` channels. The backend needs to: run Centrifugo as a Docker sidecar, issue connection/subscription JWTs to authenticated users, and publish events after state mutations in services.

---

## Scope

### In scope
- Centrifugo Docker service in `docker-compose.yml` and `docker-compose.prod.yml`
- `centrifugo/config.json` with namespace + Redis engine config
- `app/core/centrifugo.py` — connection JWT, subscription JWT, publish
- `app/services/realtime_service.py` — typed publish helpers
- `app/api/v1/websocket.py` — token endpoints
- Wire publish calls in `TestResultService`, `TestRunService`, `TestCaseService`
- Integration tests for token endpoints

### Out of scope
- Presence feature (enabled via Centrifugo config, no backend code needed)
- Push notification emails
- Frontend composable changes

---

## Technical approach

### Architecture

```
FastAPI service method
  → RealtimeService.publish_result_update(run_id, result)
  → POST http://centrifugo:8000/api  (HTTP publish, async, non-blocking)
  → Centrifugo → WebSocket → Frontend
```

### Centrifugo channels

| Channel | Listeners | Events published |
|---------|-----------|-----------------|
| `project:{id}` | All users on project | `test_result`, `test_run_status`, `test_case_update` |
| `testrun:{id}` | Execution view users | `test_result`, `test_run_status` |

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| infra | `docker-compose.yml` | Add centrifugo + env |
| infra | `docker-compose.prod.yml` | Add centrifugo + env |
| config | `centrifugo/config.json` | New file |
| config | `app/config.py` | `CENTRIFUGO_URL`, `CENTRIFUGO_API_KEY`, `CENTRIFUGO_TOKEN_SECRET` |
| core | `app/core/centrifugo.py` | JWT generation + HTTP publish |
| services | `app/services/realtime_service.py` | Typed publish helpers |
| services | `app/services/test_result_service.py` | Call publish after submit |
| services | `app/services/test_run_service.py` | Call publish on status change |
| services | `app/services/test_case_service.py` | Call publish on update |
| router | `app/api/v1/websocket.py` | Token endpoints |
| main | `app/main.py` | Wire websocket router |
| env | `.env.example` | Add Centrifugo vars |
| tests | `tests/integration/test_websocket_api.py` | Token endpoint tests |
| docs | `docs/06-generated/endpoints.md` | Add token endpoints |

### Token endpoints

```
GET  /api/v1/websocket/connection-token
  → Returns: { token: str }  (JWT signed with CENTRIFUGO_TOKEN_SECRET, sub=user_id)

POST /api/v1/websocket/subscription-tokens
  Body: { channels: ["project:42", "testrun:7"] }
  → Returns: { tokens: { "project:42": "<jwt>", "testrun:7": "<jwt>" } }
```

### Publish resilience

Publish calls are fire-and-forget — wrapped in `try/except`. A Centrifugo outage must not cause result submission to fail:

```python
async def publish_result_update(self, run_id, result):
    try:
        await self._publish(f"testrun:{run_id}", {"type": "test_result", "data": ...})
    except Exception:
        log.warning("Centrifugo publish failed — event dropped", exc_info=True)
```

---

## Tasks

### Infrastructure setup
- [x]Add centrifugo service to `docker-compose.yml` (image: centrifugo/centrifugo:v5)
- [x]Add centrifugo service to `docker-compose.prod.yml`
- [x]Create `centrifugo/config.json` with token_hmac_secret_key, api_key, namespaces, Redis engine
- [x]Add Centrifugo env vars to `app/config.py` and `.env.example`

### Implementation
- [x]Implement `app/core/centrifugo.py`:
  - `generate_connection_token(user_id, username)` → JWT
  - `generate_subscription_token(user_id, channel)` → JWT
  - `publish(channel, data)` → async POST to Centrifugo HTTP API
- [x]Implement `app/services/realtime_service.py`:
  - `publish_result_update(run_id, project_id, result_data)`
  - `publish_run_status(run_id, project_id, status)`
  - `publish_case_update(project_id, case_id, action)`
- [x]Create `app/api/v1/websocket.py` with token endpoints
- [x]Wire websocket router in `app/main.py`
- [x]Call `realtime_service.publish_result_update()` in `TestResultService.submit()`
- [x]Call `realtime_service.publish_run_status()` in `TestRunService` on status change
- [x]Call `realtime_service.publish_case_update()` in `TestCaseService.update()`
- [x]Write integration tests for token endpoints (auth required, token is a valid JWT)

### Quality check
- [x]`pytest` passes
- [x]`ruff check app tests` clean
- [x]`mypy app` clean
- [x]Manual test: submit result → frontend updates live without refresh

### Docs
- [x]`docs/06-generated/endpoints.md` — add 2 token endpoint rows
- [x]`docs/02-architecture/ARCHITECTURE.md` — note Centrifugo in deployment section
- [x]`docs/02-architecture/backend/auth.md` — add Centrifugo token section
- [x]`docs/08-decisions/changelog.md` — record Centrifugo decision
- [x]`docs/04-execution/tech-debt.md` — mark Phase 4 resolved
- [x]Move to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Token secret mismatch between backend and Centrifugo config | Medium | Both read from same env; add startup assertion |
| Centrifugo outage breaks result submission | Low | All publishes wrapped in try/except — events dropped, submission succeeds |
| Redis connection saturation (Celery + Centrifugo sharing) | Low | Use separate Redis DB indexes (0 for app cache, 1 for Centrifugo) |

---

## Definition of done

- [x]Centrifugo container starts and is reachable from backend (`docker compose ps`)
- [x]`GET /websocket/connection-token` returns a valid JWT — frontend can connect
- [x]`POST /websocket/subscription-tokens` returns channel-scoped JWTs
- [x]Submitting a test result causes a live update in the frontend execution view
- [x]Centrifugo being down does NOT cause a 500 from result submission
- [x]Integration tests for token endpoints pass
- [x]Docs updated
