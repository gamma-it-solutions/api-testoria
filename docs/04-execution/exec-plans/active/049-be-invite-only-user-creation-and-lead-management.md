# Execution Plan: Invite-only user creation, opened to Lead + Admin

**Date**: 2026-06-03
**Author**: gabriel.arapan
**Status**: In Progress — backend code + tests + docs landed; pairs with web plan-098

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.
>
> **Cross-repo plan.** This is the **authoritative** backend half of a change that spans
> `api-testoria` and `web-testoria`. The web companion is
> `web-testoria/docs/04-execution/exec-plans/active/plan-098-invite-only-user-creation-and-lead-management.md`.
> Backend ships first (it removes a public endpoint and tightens role rules); web follows.

---

## Goal

Make adding a user a **role-gated, invite-only** operation: only **Admin** and **Lead** can create users, every new account is set up via the existing email set-password invite (no password is ever supplied at creation), and a Lead can never create, elevate to, or modify an **Admin**.

---

## Context

Two things are true today and need to change:

1. **Anyone can self-register, and user management is Admin-only.** `POST /auth/register`
   (`app/api/v1/auth.py`) is public — gated only by the `REGISTRATION_OPEN` settings flag, no
   auth, no role check. Meanwhile every endpoint on `users_router` (`app/api/v1/users.py`) is
   `require_role(UserRole.ADMIN)`, so a **Lead** — whose role metadata even reads *"Full resource
   access; no user management"* — cannot onboard anyone. The product wants the opposite: no public
   self-signup, and Leads able to manage users.

2. **Password is still an input at creation.** `UserCreate.password` is *optional* today (api
   plan 048 — blank → unusable password + welcome set-password invite), but it is still a field,
   and **bulk create still requires a per-row password** (open item in `docs/04-execution/tech-debt.md`,
   mirrored in web plan-097). The product wants the invite flow to be the **only** path: drop the
   field entirely so creation never accepts a password.

The invite machinery this plan rides on already exists (plan 048): `email_service.queue_welcome_invite()`,
`password_token_service`, `user_service.set_password()`, and the `POST /auth/reset-password` /
`GET /auth/reset-password/validate` endpoints. This plan only changes **who** may create users and
**how** the create payload looks — not the set-password flow itself.

Decisions taken before writing this plan:
- **Lead gets full user management** (create, list, get, update, delete) — same surface as Admin.
- **Public `POST /auth/register` is removed** entirely (no self-signup; invite-only).
- **Lead is capped at Lead**: a Lead may not create a user with `role=admin`, may not change any
  user's role to `admin`, and may not update or delete a user who is currently an Admin. Only an
  Admin can manage Admins. This is a privilege-escalation guard.

---

## Scope

### In scope
- Remove `POST /auth/register` and the now-unused `REGISTRATION_OPEN` setting.
- Open all `users_router` endpoints to `require_role(UserRole.LEAD, UserRole.ADMIN)`.
- Service-layer **role-ceiling guard** so a non-Admin actor cannot create/elevate/modify/delete Admins.
- Remove `password` from `UserCreate` (and therefore from `UserBulkCreate`, which is `list[UserCreate]`),
  making creation **invite-only**. `create_user` always mints an unusable password + enqueues the
  welcome invite.
- Update `ROLE_METADATA[LEAD]` description to reflect that Leads now manage users (except Admins).
- Tests + docs.

### Out of scope
- `UserUpdate.password` (admin direct password set on edit) — **retained as-is**; password *reset*
  for end users already flows through the email link. Revisit separately if we want edit to be
  invite-only too.
- Any change to the set-password / reset-password / token machinery (plan 048).
- `forgot-password` rate limiting (separate open tech-debt item).
- Self-service signup UX (deliberately removed, not redesigned).

---

## Technical approach

No DB schema change → **no Alembic migration**.

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/user.py` | Delete `password` field from `UserCreate`. `UserBulkCreate` inherits the change. (`UserUpdate.password` untouched.) |
| router (auth) | `app/api/v1/auth.py` | Delete the `POST /register` route and its imports/usages. |
| router (users) | `app/api/v1/users.py` | `require_role(UserRole.ADMIN)` → `require_role(UserRole.LEAD, UserRole.ADMIN)` on list/create/bulk/export/get/update/delete. Pass the authenticated actor into the service for create/bulk/update/delete (replace the throwaway `_:` dependency with a named `current_user`). |
| service | `app/services/user_service.py` | Add `_assert_can_manage_role(actor, target_role)` / `_assert_can_manage_user(actor, target_user)` guard helpers; thread `actor: User` into `create_user`, `bulk_create_users`, `update_user`, `delete_user`. `create_user` no longer reads `data.password` — always unusable-password + `queue_welcome_invite`. |
| roles | `app/core/roles.py` | `ROLE_METADATA[LEAD]["description"]` → e.g. *"Full resource access; manages users (cannot manage Admins)."* |
| config | `app/config.py`, `.env.example`, `.env.prod`, `.env.local` | Remove `REGISTRATION_OPEN`. |
| tests | `tests/unit/test_user_service.py`, `tests/integration/test_users_api.py`, `tests/integration/test_auth_api.py` | See Tasks. |

### Key decisions

- **`require_role` stays exact-match, list both roles.** `require_role(*roles)` checks
  `current_user.role not in roles` (`app/dependencies.py`) — it is not hierarchy-based. So opening to
  Lead+Admin means passing both explicitly: `require_role(UserRole.LEAD, UserRole.ADMIN)`. (Admin is
  not implied by Lead here.) Do **not** refactor `require_role` to hierarchy in this plan — that would
  silently widen every other call site.
- **The role-ceiling guard lives in the service, not the router.** Invariant #1 ("no business logic
  in routers") forbids putting the "lead can't touch admins" comparison in the endpoint. The router
  passes `current_user`; the service decides. Guard raises `ForbiddenError` (→ 403).
- **Guard rule (precise):** an actor whose role is **not** `ADMIN` is rejected (403) when:
  (a) creating a user with `role == ADMIN`; (b) updating any user's `role` to `ADMIN`;
  (c) updating or deleting a user whose current role **is** `ADMIN`. Admin actors are unrestricted.
- **Invite-only by schema removal, not by validation.** Dropping `password` from `UserCreate` makes
  "no password at creation" structurally impossible and fixes bulk create for free (it is
  `list[UserCreate]`), resolving the long-standing tech-debt item rather than patching the CSV parser.
- **No migration.** Existing accounts and `hashed_password` columns are untouched; only the create
  *input contract* changes.

---

## Tasks

### Implementation
- [ ] `app/schemas/user.py`: remove the `password` field (and its comment) from `UserCreate`.
- [ ] `app/api/v1/auth.py`: delete the `POST /register` endpoint; drop now-unused `UserCreate` /
      `settings.REGISTRATION_OPEN` / `ForbiddenError` imports if no longer referenced there.
- [ ] `app/config.py` + `.env.example` / `.env.prod` / `.env.local`: remove `REGISTRATION_OPEN`.
- [ ] `app/services/user_service.py`: add `_assert_can_manage_role(actor, target_role)` and
      `_assert_can_manage_user(actor, target_user)`; thread `actor: User` into `create_user`,
      `bulk_create_users`, `update_user`, `delete_user`; remove all `data.password` reads in
      `create_user` so it always enqueues the welcome invite.
- [ ] `app/api/v1/users.py`: switch the six endpoints to
      `require_role(UserRole.LEAD, UserRole.ADMIN)`; bind the dependency to a named `current_user`
      and pass it to the service on create/bulk/update/delete.
- [ ] `app/core/roles.py`: update `ROLE_METADATA[LEAD]` description.
- [ ] Write unit tests for the guard + invite-only creation in `tests/unit/test_user_service.py`.
- [ ] Write integration tests in `tests/integration/test_users_api.py` and update
      `tests/integration/test_auth_api.py` (register endpoint gone).

### Quality check
- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [ ] `docs/06-generated/endpoints.md` — remove `POST /auth/register`; note Lead+Admin on `/users*`.
- [ ] `docs/06-generated/db-schema.md` — no change (note: schema unchanged, no migration).
- [ ] `docs/02-architecture/ARCHITECTURE.md` — update the `auth.py` route list (drop `/register`),
      the "Where is X?" rows for user creation, and any mention of user mgmt being Admin-only.
- [ ] `docs/02-architecture/backend/auth.md` — correct the RBAC description (Lead manages users; cap rule).
- [ ] `docs/01-product/features/002-user-management.md` + `docs/01-product/index.md` — invite-only,
      Lead+Admin, no self-registration.
- [ ] `docs/08-decisions/changelog.md` — record the three decisions and rationale.
- [ ] `docs/04-execution/tech-debt.md` — resolve "Bulk Create still requires a per-row password" area
      on the backend side; note the web counterpart.
- [ ] `docs/05-quality/QUALITY_SCORE.md` — update security/RBAC line if affected.
- [ ] Keep in sync with web plan-098; move both to `completed/` together once verified.
- [ ] This plan moved from `active/` to `completed/`.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing clients still call `POST /auth/register` | Low | No known consumer (web has no signup screen; CLI plans are `do-not-execute`). Grep the repos; announce removal in changelog. |
| Lead privilege escalation by creating/elevating an Admin | Medium | Service-layer ceiling guard with explicit unit + integration tests for the 403 paths. |
| Lead modifies/deletes an existing Admin | Medium | Guard covers update/delete against current-Admin targets, not just role changes. |
| A creation path is missed and still tries to read `password` | Low | Field removed at the schema level → any leftover read fails type-check (`mypy`) / tests. |
| Seed/admin bootstrap regresses | Low | `scripts/seed.py` sets the password directly (not via `UserCreate`); verify it still runs. |

---

## Definition of done

- [ ] `POST /auth/register` no longer exists; `REGISTRATION_OPEN` removed from config and all `.env*`.
- [ ] All `/users*` endpoints reachable by Lead and Admin; 403 for tester/read_only.
- [ ] `UserCreate` has no `password` field; single + bulk create always trigger the welcome invite.
- [ ] Role-ceiling guard enforced and tested: Lead → 403 on create-admin, elevate-to-admin,
      update-admin, delete-admin; Admin unrestricted.
- [ ] Unit test coverage ≥ 85% for the new service code; integration tests cover happy path + 401/403.
- [ ] No migration required (schema unchanged) — confirmed.
- [ ] Docs updated; verified together with web plan-098 before both move to `completed/`.
