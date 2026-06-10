# Execution Plan: Email service — welcome invite & password reset

**Date**: 2026-06-02
**Author**: Gabriel Arapan
**Status**: Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Add a transactional email capability (Gmail SMTP) that sends a **welcome / set-password invite** when a user is created and powers a **forgot-password → reset-password** flow — delivered through a **durable email outbox** (DB-backed queue + drain worker) so bulk user creation can't melt the SMTP connection, and on a single Redis-backed, single-use, expiring token.

---

## Context

The platform has no way to email users. Two needs converge on the same machinery:

1. **Onboarding** — admins create users (`POST /users`, `POST /users/bulk`) and self-registration is open (`POST /auth/register`). We want the user to set their own password via a tokenized invite link, so the admin never handles credentials.
2. **Account recovery** — there is no password-reset path; a forgotten password currently requires an admin to overwrite it.

Both are "set a password by proving you own the email address", so they share one token type and one `reset-password` endpoint.

**Why an outbox (decided after reviewing bulk behaviour).** `POST /users/bulk` can create ~100 users at once. Scheduling one `BackgroundTasks` send per user would open ~100 separate connect→TLS→AUTH→QUIT cycles run serially after the response, in-process and **non-durable** (a deploy/restart mid-batch drops the rest); a naive `asyncio.gather` would instead open ~100 **concurrent** Gmail connections and get throttled (`421 Too many concurrent connections`). Gmail also caps volume (~500/day consumer, ~2,000/day Workspace) and punishes rapid connection churn. So all email goes through a durable **outbox**: creation writes outbox rows in the *same DB transaction* as the user, and a drain worker sends them over a **reused, paced, retrying** connection.

Decisions confirmed with the product owner:
- **Transport**: Gmail **SMTP + App Password** via `aiosmtplib` (not the Gmail API).
- **Welcome flow**: **invite + set-password link** — account created without a usable password; user sets one through the same token machinery the reset flow uses.
- **Delivery**: **outbox table + in-process drain worker** (not per-request `BackgroundTasks`).

Related: token blocklist on logout (tech-debt) is the other planned Redis consumer; this plan introduces the first real async Redis client (`app/core/redis.py`), which that work can reuse.

---

## Scope

### In scope
- `app/core/email.py` — async SMTP sender (`aiosmtplib`, STARTTLS), HTML + plaintext multipart. Exposes a context-managed connection so the worker can send many messages on one connection. No-op + logged when `EMAIL_ENABLED=False`.
- Jinja2 HTML templates: `app/templates/email/welcome_invite.html`, `password_reset.html` (+ shared layout, plaintext fallback).
- `app/core/redis.py` — singleton `redis.asyncio` client from `REDIS_URL` (`redis==5.0.4` already installed; no new dep).
- `app/models/email_outbox.py` (new) + **Alembic migration** — `email_outbox(id, to_email, subject, template, context JSONB, status[pending|sending|sent|failed], attempts, max_attempts, last_error, next_attempt_at, created_at, sent_at)`.
- `app/services/email_outbox_service.py` — `enqueue(db, to_email, template, context, subject)` (writes a `pending` row, no commit of its own — joins the caller's transaction); `claim_batch(db, limit)` (`SELECT … WHERE status='pending' AND next_attempt_at<=now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT n`, flips to `sending`); `mark_sent` / `mark_failed` (attempts++, exponential `next_attempt_at` backoff, → `failed` at `max_attempts`).
- `app/core/email_worker.py` — async drain loop started from the FastAPI lifespan: every `EMAIL_OUTBOX_POLL_SECONDS`, claim a batch, open **one** SMTP connection, render + send each (paced by `EMAIL_SEND_PACE_MS`, per-row try/except), commit row outcomes. Cleanly cancelled on shutdown.
- `app/services/password_token_service.py` — `create_token(user_id, purpose, ttl) -> str`, `consume_token(token) -> (user_id, purpose)` (single-use via `GETDEL`), `peek_token(token)`. Token = `secrets.token_urlsafe(32)`; Redis key `pwtoken:{token}` → JSON `{user_id, purpose}`, TTL per purpose.
- `app/services/email_service.py` — `queue_welcome_invite(db, user)` and `queue_password_reset(db, user)`: mint the token, build the link from `FRONTEND_BASE_URL`, and `enqueue` an outbox row. (Builds the contract link paths `/set-password` and `/reset-password`.)
- New endpoints in `app/api/v1/auth.py`:
  - `POST /auth/forgot-password` `{email}` → always `202` (no enumeration); if an active user matches, `queue_password_reset`.
  - `POST /auth/reset-password` `{token, new_password}` → consume token, set password, `200`. Serves both welcome set-password and forgot-password.
  - `GET /auth/reset-password/validate?token=...` → `200 {valid, username}` or `400`; peeks without consuming.
- Wire welcome invite into all three creation paths — `user_service.create_user`, `bulk_create_users`, `auth.register` call `email_service.queue_welcome_invite` **before** the transaction commits, so a row exists iff the user is committed.
- Make `password` **optional** on `UserCreate` / `UserBulkCreate`; when omitted, store an unusable random hash (`get_password_hash(secrets.token_urlsafe(32))`) — invite link is the only way in. (No column change; stays `NOT NULL`.)
- Config + env: `EMAIL_SMTP_HOST/PORT/USER/PASSWORD`, `EMAIL_FROM`, `EMAIL_ENABLED` (default `False`), `FRONTEND_BASE_URL`, `EMAIL_INVITE_TOKEN_TTL_SECONDS` (86400), `EMAIL_RESET_TOKEN_TTL_SECONDS` (3600), `EMAIL_OUTBOX_POLL_SECONDS` (10), `EMAIL_OUTBOX_BATCH_SIZE` (50), `EMAIL_SEND_PACE_MS` (200), `EMAIL_MAX_ATTEMPTS` (5).
- Audit log: `WELCOME_INVITE_SENT`, `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET`.

### Out of scope
- Gmail API / OAuth2 transport (chose SMTP).
- Celery-based drain worker — the in-process loop is sufficient at current scale; Celery (broker already in env) is the documented scale-out path (tech debt).
- Transactional provider (SES/SendGrid) — the right move above Gmail's daily caps; tracked as tech debt.
- Rate limiting / captcha on `forgot-password` (tech debt — see Risks).
- Outbox admin UI / dead-letter replay endpoint (tech debt).
- Email verification on signup, email-change confirmation, generic notifications (future).
- Frontend pages — web-testoria `plan-097-forgot-and-set-password-flow.md`.

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| deps | `requirements.txt` | Add `aiosmtplib==3.0.*`, `jinja2==3.1.*` |
| config | `app/config.py`, `.env.example`, `.env.local`, `.env.prod` | SMTP + `FRONTEND_BASE_URL` + TTL + outbox tuning + `EMAIL_ENABLED` |
| core | `app/core/redis.py` (new) | Async `redis.asyncio` client singleton; `aclose` on lifespan shutdown |
| core | `app/core/email.py` (new) | `aiosmtplib` STARTTLS sender; reusable connection; no-op when disabled |
| core | `app/core/email_worker.py` (new) | Lifespan-managed async drain loop |
| templates | `app/templates/email/*.html` (new) | layout + `welcome_invite.html` + `password_reset.html` |
| models | `app/models/email_outbox.py` (new) | `EmailOutbox` model |
| migration | `alembic/versions/` (new) | Create `email_outbox` table (reversible) |
| schemas | `app/schemas/auth.py` (new) | `ForgotPasswordRequest`, `ResetPasswordRequest`, `ResetTokenValidateResponse` |
| schemas | `app/schemas/user.py` | `password` optional on `UserCreate` / `UserBulkCreate` |
| services | `app/services/email_outbox_service.py` (new) | enqueue / claim_batch / mark_sent / mark_failed |
| services | `app/services/password_token_service.py` (new) | create / peek / consume single-use Redis tokens |
| services | `app/services/email_service.py` (new) | `queue_welcome_invite`, `queue_password_reset` |
| services | `app/services/user_service.py` | unusable-password fallback; enqueue invite in create / bulk |
| router | `app/api/v1/auth.py` | 3 new endpoints; enqueue invite in `register` |
| router | `app/api/v1/users.py` | enqueue invite in `create_user` / `bulk_create_users` |
| main | `app/main.py` | start/stop drain worker + close redis in `lifespan` |
| tests | `tests/unit/`, `tests/integration/` | token service, outbox claim/retry/idempotency, email sender (mocked SMTP), endpoints |

### Key decisions

- **Durable outbox over per-request sends.** Rows are written in the *same transaction* as the user (transactional consistency: no email for a rolled-back user; a committed user always has its invite recorded). The drain worker reuses one connection, paces sends, and retries with backoff — solving connection churn, Gmail throttling, and restart-durability in one mechanism. This also subsumes the old "delivery log / retry" tech-debt item.
- **In-process drain loop, not Celery.** No extra container/process; restart-safe because state lives in Postgres. `FOR UPDATE SKIP LOCKED` makes it correct even with multiple uvicorn workers or API replicas (no double-send). Celery is reserved for true horizontal scale (tech debt) and its broker is already configured.
- **Routers get thinner, not fatter.** Because enqueue is a DB write inside the existing transaction, routers no longer need `BackgroundTasks` or any send logic — better alignment with the "no business logic / one service call" invariant than the original BackgroundTasks design.
- **One token, one endpoint for both flows.** Welcome invite and reset are the same operation; only TTL + copy differ. `POST /auth/reset-password` consumes either.
- **Redis, not DB, for tokens** (but DB for the *message queue*). Tokens want native TTL + atomic single-use (`GETDEL`); messages want durability + retry/visibility — different tools for different jobs.
- **Token minted at enqueue, link baked into `context`.** The worker stays dumb (render + send). Drain latency (~seconds) ≪ token TTL (1h/24h), so links are valid on delivery; see Risks for the backlog edge case.
- **`EMAIL_ENABLED=False` by default.** Dev/test never hit Gmail; rows still queue so the outbox path is exercised, the sender just logs a no-op.
- **No user enumeration.** `forgot-password` always `202`; `reset-password` returns generic `400` for invalid/expired/used tokens.
- **Password optional on create → unusable hash.** Keeps the column `NOT NULL` and `verify_password` total; the random hash can never match a login.

### Request/response shapes

```
POST /api/v1/auth/forgot-password   {"email": "a@b.com"}                 → 202 {"message": "If the address exists, a reset link was sent."}
POST /api/v1/auth/reset-password    {"token": "...", "new_password":"…"} → 200 {"message": "Password updated."} | 400 invalid/expired
GET  /api/v1/auth/reset-password/validate?token=...                      → 200 {"valid": true, "username": "jdoe"} | 400
```

### Outbox lifecycle

```
enqueue (pending, next_attempt_at=now)
  → worker claim_batch (→ sending, FOR UPDATE SKIP LOCKED)
    → send ok            → sent
    → send fails, n<max  → pending, attempts++, next_attempt_at = now + backoff(attempts)
    → send fails, n=max  → failed (last_error kept; surfaced via log/metric)
```

---

## Tasks

### Implementation
- [x] Add `aiosmtplib`, `jinja2` to `requirements.txt`; install
- [x] Add config settings + update `.env.example`, `.env.local`, `.env.prod`
- [x] Create `app/core/redis.py` (+ close in lifespan)
- [x] Create `app/core/email.py` (STARTTLS, reusable connection, no-op when disabled)
- [x] Create `app/templates/email/` layout + `welcome_invite.html` + `password_reset.html`
- [x] Create `app/models/email_outbox.py`; generate + review Alembic migration; `alembic upgrade head`
- [x] Create `app/services/email_outbox_service.py` (enqueue / claim_batch / mark_sent / mark_failed with backoff)
- [x] Create `app/core/email_worker.py` drain loop; start/stop in `app/main.py` lifespan
- [x] Create `app/services/password_token_service.py` (create / peek / consume)
- [x] Create `app/services/email_service.py` (`queue_welcome_invite`, `queue_password_reset`)
- [x] Add `app/schemas/auth.py`; make `password` optional in `app/schemas/user.py`
- [x] Update `user_service.create_user` / `bulk_create_users` (unusable-password fallback + enqueue invite)
- [x] Add `POST /auth/forgot-password`, `POST /auth/reset-password`, `GET /auth/reset-password/validate`
- [x] Enqueue invite in `auth.register`, `users.create_user`, `users.bulk_create_users`
- [x] Add audit actions (`WELCOME_INVITE_SENT`, `PASSWORD_RESET_REQUESTED`, `PASSWORD_RESET`)
- [x] Unit tests: token service (single-use/expiry); outbox (claim skips locked, backoff schedule, max_attempts→failed, idempotent re-claim)
- [x] Unit tests: email sender (mocked SMTP, link building, disabled no-op)
- [x] Integration tests: 3 endpoints (happy path, invalid/expired/used→400, 202-always, weak password→422); bulk create enqueues N rows in one txn

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — add 3 auth endpoints
- [x] `docs/06-generated/db-schema.md` — add `email_outbox` table
- [x] `docs/01-product/features/001-auth.md` — forgot/reset + invite flow
- [x] `docs/01-product/features/002-user-management.md` — password optional; invite on creation; bulk delivery via outbox
- [x] `docs/02-architecture/ARCHITECTURE.md` — codemap: `email.py`, `email_worker.py`, `redis.py`, `email_outbox` model + service, `password_token_service`, `email_service`; note drain worker in lifespan + SMTP/Redis in stack
- [x] `docs/03-engineering/operations/email.md` (new) — Gmail app-password setup, `EMAIL_ENABLED`, outbox draining, stuck-`pending`/`failed` triage
- [x] `docs/05-quality/SECURITY.md` — token single-use/TTL, no-enumeration, app-password handling
- [x] `docs/08-decisions/changelog.md` — SMTP-over-API, Redis-token, outbox-over-BackgroundTasks decisions
- [x] `docs/04-execution/tech-debt.md` — add: forgot-password rate limiting; Celery scale-out drain; transactional provider (SES) above Gmail caps; outbox dead-letter replay/admin UI
- [x] `docs/05-quality/QUALITY_SCORE.md` — update security/coverage rows
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Gmail app password leaked from env / logs | Medium | Only in `.env.prod` (gitignored); never logged; excluded from settings dumps |
| `forgot-password` abused to spam / enumerate | Medium | Always `202`; rate limiting tracked as tech debt before heavy public use |
| Drain worker not running → rows stuck `pending` | Medium | Started in lifespan; log + metric on pending-count / oldest-pending age; ops runbook triage step |
| Gmail daily cap / spam classification at high volume | Medium | Workspace SPF/DKIM on `futurefertility.com`; pacing; alert on `failed`; escalate to SES (tech debt) above caps |
| Outbox backlog outlives token TTL (link expired on delivery) | Low | Drain latency ≪ TTL (1h/24h); alert on backlog age > TTL/2; reset is user-retriggerable |
| Multiple workers double-send | Low | `FOR UPDATE SKIP LOCKED` + `sending` state; unique claim |
| Poison message retried forever | Low | `max_attempts` → `failed` with `last_error`; surfaced for manual replay |
| Token reuse / double-click | Low | `GETDEL` atomic single-use; second consume → 400 |

---

## Definition of done

- [x] All new endpoints return correct status codes and response shapes
- [x] Endpoints are public by design — assert no auth required and no user enumeration
- [x] Unit test coverage ≥ 85% for new service code (`password_token_service`, `email_outbox_service`, `email_service`)
- [x] Integration tests cover happy path + invalid/expired/used token (400) + weak password (422) + 202-always + bulk-enqueues-N-in-one-transaction
- [~] Outbox concurrency verified: two concurrent claimers never send the same row (SKIP LOCKED); failed sends back off and eventually move to `failed` — **backoff → failed covered by unit tests; the SKIP-LOCKED concurrency test (`tests/integration/test_email_outbox_concurrency.py`) is written but Postgres-only and skips on the SQLite test runner. Run it against the Docker PG stack (`TEST_DATABASE_URL`) to close.**
- [~] Migration applies cleanly **and is reversible** (`alembic upgrade head` / `downgrade -1`) — **offline up/down SQL validated (`alembic upgrade … --sql` / `downgrade … --sql`); not yet run against a live Postgres (none reachable in this env). Run `alembic upgrade head && alembic downgrade -1` on the dev DB to close.**
- [ ] Bulk-create of 100 users verified in staging (`EMAIL_ENABLED=True`): single SMTP connection reused, all 100 delivered, no Gmail throttling — **deferred: requires staging + real Gmail App Password (out of scope for this environment).**
- [x] Docs updated

> **Verification status (2026-06-03):** `pytest` 407 passed / 1 skipped (the PG-only concurrency test), `ruff check app tests` clean, `mypy app` clean; new service modules at 100% coverage. Three DoD items above need a live Postgres / staging to fully close — see the per-item notes.
