# Feature: User Management

## What it does

Provides full user lifecycle management (CRUD, bulk create, export, search/filter) and a formal role system with 5 predefined permission levels.

## Roles

| Slug | Label | Default | Deletable | Description |
|------|-------|---------|-----------|-------------|
| `no_access` | No Access | No | Yes | Blocked at every protected route after login |
| `read_only` | Read Only | No | Yes | GET on all resources; no write operations |
| `tester` | Tester | No | Yes | Create/edit test cases, suites, results |
| `lead` | Lead | **Yes** | **No** | Full resource access; manages users (cannot manage Admins) |
| `admin` | Admin | No | Yes | Full access including managing Admins |

Permission hierarchy (lowest → highest): `no_access` < `read_only` < `tester` < `lead` < `admin`

The `lead` role is the default for new accounts and cannot be deleted.

`no_access` users can obtain a JWT (login succeeds) but every subsequent protected call returns 403.

## Who can create/manage users (plan 049)

- **Lead and Admin** can reach every `/users*` endpoint (`require_role(LEAD, ADMIN)`); tester / read_only / no_access get 403.
- A **Lead is capped at Lead**: it may not create a user with `role=admin`, change any user's role to `admin`, or update/delete a user who is currently an Admin (all 403). Only an Admin can manage Admins. Enforced in `user_service` via `_assert_can_manage_role` / `_assert_can_manage_user` using the authenticated actor passed by the router.
- **No public self-registration** — the former `POST /auth/register` is removed. Accounts exist only because a Lead or Admin created them.

## API surface

### User management (Lead or Admin)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/v1/users` | Search (`search`, `status`, `role`) + pagination |
| POST | `/api/v1/users` | Create single user (invite-only, no password) |
| POST | `/api/v1/users/bulk` | Best-effort bulk create; returns per-row errors |
| GET | `/api/v1/users/export?format=csv\|excel` | Full user list download |
| GET | `/api/v1/users/{id}` | Get user by ID |
| PUT | `/api/v1/users/{id}` | Update user fields |
| DELETE | `/api/v1/users/{id}` | Delete user; 409 if role is `lead` |

### Roles (any authenticated user)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/v1/roles` | Returns all 5 role definitions with metadata |

## Onboarding via welcome invite (plan 048, tightened in plan 049)

- Creation is **invite-only**: `UserCreate` has **no `password` field** (single
  or bulk). The account is always created with an unusable random hash; the user
  sets a real password through the welcome **set-password invite** link. Leads
  and Admins never handle credentials.
- Both creation paths — `POST /users` and `POST /users/bulk` — enqueue a welcome
  invite email **in the same transaction** as the user INSERT
  (`email_service.queue_welcome_invite`), so a committed user always has its
  invite recorded and a rolled-back one never emits an email.
- Bulk create of ~100 users writes ~100 outbox rows in one transaction; the
  drain worker later sends them over a single, paced SMTP connection rather than
  opening 100 connections. See `docs/03-engineering/operations/email.md`.

## Constraints

- `DELETE /users/{id}` returns 409 if `user.role == "lead"`.
- Bulk create is best-effort: per-row errors are collected and returned; successful rows are committed regardless of failures. Set `strict: true` in the request body for all-or-nothing semantics.
- Bulk create is capped at 100 users per request (`max_length=100` on the `users` field).
- Export columns: `id`, `username`, `email`, `full_name`, `role`, `is_active`, `created_at`.

## Implementation

- `app/core/roles.py` — `UserRole` StrEnum, `ROLE_HIERARCHY`, `ROLE_METADATA`
- `app/services/user_service.py` — all service functions; `_unusable_password_hash` (invite-only); `_assert_can_manage_role` / `_assert_can_manage_user` (Lead-capped-at-Lead guard); enqueues welcome invite in `create_user` / `bulk_create_users`; `set_password` for the reset flow
- `app/services/email_service.py` — `queue_welcome_invite`
- `app/api/v1/users.py` — users and roles routers; `require_role(LEAD, ADMIN)` + actor passed to mutating service calls
- `app/api/v1/auth.py` — login/refresh/logout/me + forgot/reset-password (no register)
- `app/dependencies.py` — `no_access` block in `get_current_user`, `require_role()`
