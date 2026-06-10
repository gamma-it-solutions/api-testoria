# Authentication and Authorization

JWT auth and RBAC in the Testoria backend.

---

## Token lifecycle

```
POST /api/v1/auth/login
  → verify username + bcrypt password
  → create access_token (short-lived, 30 min default)
  → create refresh_token (long-lived, 7 days default)
  → return { access_token, refresh_token, token_type: "bearer" }

All subsequent requests:
  Authorization: Bearer <access_token>

On 401 (access token expired):
  POST /api/v1/auth/refresh  { refresh_token }
  → validate refresh token signature + type claim
  → issue new access_token + new refresh_token
  → return new token pair

POST /api/v1/auth/logout
  → currently: returns success (stateless — token is not blocklisted)
  → future improvement: store token JTI in Redis blocklist
```

---

## Password reset & set-password invite (plan 048)

Separate from JWT auth: a credential-recovery / onboarding flow built on
single-use Redis tokens, not JWTs.

```
POST /api/v1/auth/forgot-password { email }
  → always 202 (no user enumeration)
  → if an active user matches: mint pwtoken (Redis, 1h TTL), enqueue reset email

# Welcome invite (on user creation) mints the same kind of token with a 24h TTL
# and a /set-password link — same machinery, different copy.

POST /api/v1/auth/reset-password { token, new_password }
  → new_password < 8 chars → 422 (before consuming the token)
  → consume_token via GETDEL (single use) → 400 if invalid/expired/used
  → user_service.set_password(user_id, new_password) → 400 if user gone/inactive
  → audit PASSWORD_RESET → 200

GET /api/v1/auth/reset-password/validate?token=...
  → peek_token (no consume) → 200 { valid, username } | 400
```

These three routes are **public by design** (no `Depends(get_current_user)`).
Token service: `app/services/password_token_service.py`. Email delivery is via
the durable outbox — see `docs/03-engineering/operations/email.md`.

---

## JWT structure

**Access token payload:**
```json
{
  "sub": 42,
  "username": "alice",
  "role": "tester",
  "exp": 1711234567,
  "type": "access"
}
```

**Refresh token payload:**
```json
{
  "sub": 42,
  "username": "alice",
  "exp": 1711234567,
  "type": "refresh"
}
```

The `type` claim is checked in `decode_token` — an access token cannot be used as a refresh token and vice versa.

---

## Core functions (`app/core/security.py`)

| Function | Purpose |
|----------|---------|
| `create_access_token(data, expires_delta)` | Encode + sign JWT with `type: access` |
| `create_refresh_token(data)` | Encode + sign JWT with `type: refresh` |
| `decode_token(token, expected_type)` | Decode + verify; raises `UnauthorizedError` on failure |
| `get_password_hash(password)` | bcrypt hash |
| `verify_password(plain, hashed)` | bcrypt verify |

---

## Dependency injection (`app/dependencies.py`)

### `get_current_user`

Validates Bearer token via `OAuth2PasswordBearer`, decodes the JWT, queries the user from DB. Raises:
- 401 if token is invalid, expired, or user not found
- 401 if user is inactive (`is_active == False`)
- 403 if user has `no_access` role

### `require_role(*roles: UserRole)`

Returns a FastAPI dependency that calls `get_current_user` and checks the user's role is in the allowed set. Raises 403 if not.

```python
# app/dependencies.py
def require_role(*roles: UserRole) -> Callable[..., object]:
    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise ForbiddenError()
        return current_user
    return checker
```

Usage in routers:
```python
_VIEWER  = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_TESTER  = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)

@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),  # lead or admin only
) -> ProjectResponse:
    ...
```

---

## Role-based access control (RBAC)

Five roles defined in `app/core/roles.py` as `UserRole` StrEnum:

| Role | Level | Access |
|------|-------|--------|
| `no_access` | 0 | Blocked at every protected route (after login) |
| `read_only` | 1 | GET on all resources; no write operations |
| `tester` | 2 | Create/close test runs, submit results, upload attachments |
| `lead` | 3 | Full resource access (projects, suites, cases, runs); manages users **except Admins** (plan 049) |
| `admin` | 4 | Full access including managing Admins |

`lead` is the default role for new users. `no_access` blocking is enforced in `get_current_user` (not per-route), so any `no_access` user is rejected at every protected endpoint.

Role checking uses explicit role lists (not hierarchy-based minimum), giving each endpoint precise control over which roles are allowed.

**User management (`/users*`)** requires `require_role(LEAD, ADMIN)`. Because `require_role` is a coarse gate, the **Lead-capped-at-Lead** rule (a Lead cannot create, elevate to, or modify/delete an Admin) is enforced one layer deeper, in `user_service` (`_assert_can_manage_role` / `_assert_can_manage_user`), using the authenticated actor the router forwards. There is **no public self-registration** — the `POST /auth/register` endpoint was removed in plan 049; accounts are created only through `/users*` (invite-only, no password field).

---

## Security notes

- Passwords are hashed with bcrypt — never stored in plaintext
- `hashed_password` is excluded from all Pydantic response schemas
- Refresh tokens are currently stateless (JWT only) — no server-side storage
- The frontend stores tokens in `localStorage` (XSS risk, documented trade-off)
- Future improvement: JWT blocklist in Redis for true logout and refresh token rotation

---

## Centrifugo tokens

The backend issues short-lived JWTs (5-minute TTL) signed with `CENTRIFUGO_TOKEN_SECRET` (separate from the API JWT secret) for Centrifugo WebSocket connections.

**Implementation:** `app/core/centrifugo.py`

### Connection token

`GET /api/v1/websocket/connection-token` — requires Bearer auth (any role except `no_access`).

Returns a JWT with `sub` = user ID and `info.username`. The frontend uses this to establish the WebSocket connection with Centrifugo.

### Subscription tokens

`POST /api/v1/websocket/subscription-tokens` — requires Bearer auth.

Request body: `{ channels: ["project:42", "testrun:7"] }`
Response: `{ tokens: { "project:42": "<jwt>", "testrun:7": "<jwt>" } }`

Each token contains `sub` = user ID and `channel` = the requested channel name. The frontend sends these when subscribing to private channels.

### Channels

| Channel pattern | Events | Published by |
|----------------|--------|--------------|
| `project:{id}` | `test_result`, `test_run_status`, `test_case_update` | `realtime_service` |
| `testrun:{id}` | `test_result`, `test_run_status` | `realtime_service` |

### Publish resilience

All publish calls are fire-and-forget — wrapped in `try/except` in `realtime_service._safe_publish()`. A Centrifugo outage does not cause API errors; events are dropped and logged at WARNING level.
