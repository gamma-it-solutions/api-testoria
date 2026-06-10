# Feature: 005 — Real-Time Updates (Centrifugo)

## What it does

Delivers live updates to the frontend without polling. When a tester submits a result, changes a run status, or updates a test case, all connected users see the change instantly via WebSocket.

## Architecture

```
Service method (e.g., test_result_service.submit)
  -> realtime_service.publish_result_update()
    -> POST http://centrifugo:8000/api/publish  (async, fire-and-forget)
      -> Centrifugo broadcasts via WebSocket to subscribed frontends
```

Centrifugo runs as a Docker sidecar. The backend communicates with it over HTTP (publish) and issues JWTs for client authentication.

## API surface

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/websocket/connection-token` | read_only | JWT to authenticate WebSocket connection |
| POST | `/api/v1/websocket/subscription-tokens` | read_only | Channel-scoped JWTs for private subscriptions |

### Connection token

Returns `{ token: "<jwt>" }` signed with `CENTRIFUGO_TOKEN_SECRET`. Payload: `sub` = user ID, `info.username`, 5-minute TTL.

### Subscription tokens

Request: `{ channels: ["project:42", "testrun:7"] }`
Response: `{ tokens: { "project:42": "<jwt>", "testrun:7": "<jwt>" } }`

Each token contains `sub`, `channel`, and a 5-minute TTL.

## Channels and events

| Channel | Events | Triggered by |
|---------|--------|--------------|
| `project:{id}` | `test_result`, `test_run_status`, `test_case_update` | Result submit, run status change, case update |
| `testrun:{id}` | `test_result`, `test_run_status` | Result submit, run status change |

## Key constraints

- Tokens use a separate secret (`CENTRIFUGO_TOKEN_SECRET`) from the API JWT secret (`SECRET_KEY`).
- All publish calls are fire-and-forget. Centrifugo being down does not cause API errors — events are dropped and logged at WARNING level.
- Centrifugo uses Redis DB index 1 (app cache uses index 0) to avoid connection saturation.
- No channel-level authorization beyond valid subscription token — any authenticated user can subscribe to any channel they request a token for.

## Infrastructure

- Docker service: `centrifugo/centrifugo:v5` in `docker-compose.yml`
- Config: `centrifugo/config.json` (namespaces: `project`, `testrun`; engine: Redis)
- Env vars: `CENTRIFUGO_URL`, `CENTRIFUGO_API_KEY`, `CENTRIFUGO_TOKEN_SECRET`

## Backend files

| File | Purpose |
|------|---------|
| `app/core/centrifugo.py` | JWT generation + HTTP publish |
| `app/services/realtime_service.py` | Typed publish helpers with error handling |
| `app/api/v1/websocket.py` | Token endpoints |
| `app/schemas/websocket.py` | Request/response schemas |
