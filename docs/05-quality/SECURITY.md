# Security

Security considerations and controls in the Testoria backend.

---

## Authentication

- JWT-based: `access_token` (30 min, HS256) + `refresh_token` (7 days, HS256)
- Tokens signed with `SECRET_KEY` from environment — never hardcoded
- `access_token` can be refreshed via `POST /auth/refresh` while the refresh token is valid
- On refresh failure: client clears tokens and redirects to login
- Logout is currently stateless — token is not blocklisted. **Known gap**: a stolen token remains valid until expiry. See tech-debt.md.

## Authorization (RBAC)

- Five roles, hierarchical (low → high): `no_access` (0), `read_only` (1), `tester` (2), `lead` (3), `admin` (4). Defined in `app/core/roles.py` (`UserRole`, `ROLE_HIERARCHY`, `ROLE_METADATA`). `lead` is the default role for new users; `no_access` is blocked at every protected route.
- Role is stored in the JWT payload — no DB query per request for role checks
- `require_role(*roles)` dependency enforces the minimum role at the router level
- All protected routes use `Depends(get_current_user)` — no route bypasses auth
- **Important**: `is_active` is checked on every authenticated request — deactivated users are rejected immediately (401)

## Password security

- bcrypt with default cost factor (12 rounds)
- Plain passwords are never stored or logged
- `hashed_password` is excluded from all Pydantic response schemas — never returned to clients
- Users created without a password get an **unguessable random hash** (`secrets.token_urlsafe(32)`), so the account cannot be logged into until the user sets a real password via the invite link. The column stays `NOT NULL` and `verify_password` stays total.

## Password reset & set-password invite tokens (plan 048)

- Tokens are `secrets.token_urlsafe(32)` (≈256 bits), stored in Redis as
  `pwtoken:{token}` → `{user_id, purpose}` with a **native TTL** (invite 24h,
  reset 1h). No token material is persisted to Postgres or logged.
- **Single use**: `reset-password` consumes via `GETDEL` (atomic fetch-and-delete),
  so a replay or a double-click can only succeed once — the second attempt
  returns `400`.
- **No user enumeration**: `forgot-password` always returns `202` with the same
  message regardless of whether the address exists; `reset-password` and
  `validate` return a generic `400` for any invalid/expired/used token, and
  `set_password` returns `400` (not `404`) if the user is gone/inactive.
- Password length is validated (`≥ 8`) and rejected with `422` **before** the
  token is consumed, so a too-weak attempt doesn't burn the link.
- These three endpoints are **public by design** (no `Depends(get_current_user)`).
- Audit events: `WELCOME_INVITE_SENT`, `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET`.

## Email / SMTP secrets (plan 048)

- The Gmail **App Password** (`EMAIL_SMTP_PASSWORD`) lives only in `.env.prod`
  (gitignored) — never committed, never logged, and excluded from any settings
  dump. It is scoped to the sending account and revocable independently of the
  account password.
- `EMAIL_ENABLED=false` by default; outbound SMTP only happens in environments
  that explicitly opt in with valid credentials.
- Reset/invite links are built from `FRONTEND_BASE_URL` (config), not from any
  request header, so a spoofed `Host` cannot redirect the link to an attacker.

## SQL injection

- All DB access through SQLAlchemy ORM with parameterized queries — no raw string interpolation in SQL
- `ilike` searches use `%` wildcards only within the parameter value — not in the query template

## File upload security

- Attachments are stored in object storage (MinIO/S3) via `aioboto3`, not on the local filesystem (since plan 042). `storage_backend` defaults to `"s3"`.
- File size capped at `MAX_UPLOAD_SIZE` (default 10 MB) — checked before the object is written.
- The `object_key` is generated server-side (never user-supplied) — prevents path traversal.
- Browsers fetch attachments via short-lived presigned URLs returned by the API, not by streaming bytes through FastAPI. The public S3 endpoint (`s3.testoria.gammait.net`) must preserve the `Host` header for SigV4 verification.
- MIME type is recorded from the upload but not trusted for execution.
- **Improvement needed**: validate file extension/content against an allowlist (images, PDFs, common doc formats) and AV-scan uploads — see `tech-debt.md` (virus scanning, orphan-object GC).

## CORS

- `CORS_ORIGINS` configures allowed origins — defaults to `http://localhost:5173` and `http://localhost:3000`
- Credentials mode enabled for JWT cookie support (future)
- All methods and headers allowed within the CORS origin list — review if exposing publicly

## Secrets management

- All secrets (`SECRET_KEY`, `CENTRIFUGO_API_KEY`, database credentials) come from environment variables
- `.env` file is in `.gitignore` — never committed
- `.env.example` contains only placeholder values

## HTTPS

- Production deployment behind **host-level nginx + system certbot** (TLS terminated at the host edge); the `api` container is published on `127.0.0.1:8000` only
- FastAPI does not handle TLS directly — all TLS is at the host nginx layer
- `HSTS` header set by host nginx (see `deploy/api.vhost.conf` in this repo)

## Audit logging

- `AuditLog` model records: `user_id`, `action`, `entity_type`, `entity_id`, `changes` (JSONB), `ip_address`, `user_agent`
- Critical actions logged: LOGIN, LOGOUT, CREATE/UPDATE/DELETE on Projects, Test Cases, Test Runs, Test Results
- Logs are append-only — no update or delete on `audit_logs` table

## Dependencies

- Run `pip-audit` or `safety check` regularly to scan for known vulnerabilities
- Keep `fastapi`, `sqlalchemy`, `passlib`, `python-jose` updated
- Pin all dependency versions in `requirements.txt`

## Rate limiting

- Not yet implemented — a future improvement is to add rate limiting per user (100 req/min) using a Redis middleware
- Current mitigation: deploy behind nginx which can be configured for connection limits
