# 001 — Authentication

## What it does

Issues and validates JWT access + refresh tokens. Controls access to all protected endpoints. Deactivated users are rejected at login and on every request.

## API surface

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/auth/login` | None | Credentials → access + refresh tokens |
| POST | `/api/v1/auth/refresh` | None | Refresh token → new access + refresh tokens |
| GET | `/api/v1/auth/me` | Bearer | Current user profile |
| POST | `/api/v1/auth/logout` | Bearer | Invalidates session (token blocklist via Redis — pending) |
| POST | `/api/v1/auth/forgot-password` | None | `{email}` → always `202`; queues a reset email if an active user matches |
| POST | `/api/v1/auth/reset-password` | None | `{token, new_password}` → set password (serves reset **and** welcome set-password) |
| GET | `/api/v1/auth/reset-password/validate` | None | `?token=` → `{valid, username}` (peek, no consume) |

## Password reset & set-password invite (plan 048)

A forgotten password and a brand-new user's "set your password" invite are the
same operation — *prove you own the email, then choose a password* — so they
share one token type and one `reset-password` endpoint.

- **Tokens** are `secrets.token_urlsafe(32)`, stored in Redis as
  `pwtoken:{token}` → `{user_id, purpose}` with a native TTL (invite 24h, reset
  1h). Single-use: `reset-password` consumes via `GETDEL`, so a second use (or a
  double-click) returns `400`.
- **No user enumeration**: `forgot-password` always returns `202` with the same
  message; `reset-password` / `validate` return a generic `400` for any
  invalid/expired/used token.
- A password shorter than 8 chars is rejected with `422` **before** the token is
  consumed (so the user can retry with the same link).
- Emails are delivered through the durable outbox — see
  `docs/03-engineering/operations/email.md`.

## Token shape

```json
{ "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```

Access token lifetime: `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30).
Refresh token lifetime: `REFRESH_TOKEN_EXPIRE_DAYS` (default 7).

## DB tables

- `users` — stores `hashed_password` (bcrypt), `role`, `is_active`. When a user
  is created without a password, `hashed_password` holds an unguessable random
  hash until they set one via the invite link.
- `email_outbox` — durable queue for the reset/invite emails (plan 048)

## Constraints

- Inactive users (`is_active=false`) are rejected with 401 at login
- Passwords are hashed with bcrypt via direct `bcrypt.hashpw` / `bcrypt.checkpw` (passlib removed — see changelog)
- Access tokens carry `sub` (user id) and `type: "access"`; refresh tokens carry `type: "refresh"`
- Using an access token on the refresh endpoint is rejected
- Token blocklist on logout not yet implemented — stolen tokens remain valid until expiry (tracked in tech-debt.md)
