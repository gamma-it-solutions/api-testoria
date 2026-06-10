# Execution Plan: Fix User Password Update

**Date**: 2026-04-08
**Author**: Claude
**Status**: Draft

---

## Goal

Allow admins to update a user's password via `PUT /users/{id}` by adding the `password` field to the `UserUpdate` schema and handling it in the service.

---

## Context

The frontend sends `password` in the `PUT /users/{id}` request body when an admin changes a user's password on the user detail page. However:

1. **`UserUpdate` Pydantic schema** (`app/schemas/user.py` line 19): Does not include a `password` field. Pydantic silently strips it from the request — the backend never sees it.
2. **`update_user` service** (`app/services/user_service.py` line 105): Only handles `email`, `full_name`, `role`, `is_active`. No password hashing logic.

The request succeeds (200 OK) because the other fields update fine, but the password is silently ignored. The frontend shows a success toast, misleading the admin into thinking the password was changed.

---

## Scope

### In scope

- Add `password: str | None = None` to `UserUpdate` schema
- Add password hashing in `update_user` service when `password` is provided
- Audit log the password change (without logging the actual password)

### Out of scope

- Password strength validation (can be added later)
- Current password verification (this is an admin action, not self-service)
- Self-service password change endpoint (separate feature)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/user.py` | Add `password: str \| None = None` to `UserUpdate` |
| services | `app/services/user_service.py` | Add password hashing in `update_user` when `data.password` is not None |

### Implementation

**1. `app/schemas/user.py`** — add password field:

```python
class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None  # ADD
```

**2. `app/services/user_service.py`** — hash and set password:

```python
async def update_user(db: AsyncSession, user_id: int, data: UserUpdate) -> User:
    user = await get_user(db, user_id)

    if data.email is not None:
        user.email = data.email
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:                          # ADD
        user.hashed_password = get_password_hash(data.password)  # ADD

    await db.flush()
    await db.refresh(user)
    return user
```

`get_password_hash` is already imported in the service file (line 11).

### Key decisions

- **Optional field (`None` default)**: Password is only updated when explicitly provided. Omitting it from the request body leaves the existing password unchanged.
- **No minimum length validation in schema**: The backend's `bcrypt` hashing works on any string. Password policy (min length, complexity) can be added later as a `Field(min_length=8)` constraint.
- **No current password required**: This is an admin endpoint (`require_role(ADMIN)`). Admins reset passwords without knowing the current one.

---

## Tasks

### Implementation

- [ ] Add `password: str | None = None` to `UserUpdate` in `app/schemas/user.py`
- [ ] Add password hashing block in `update_user` in `app/services/user_service.py`
- [ ] Test: update user with password → login with new password → success
- [ ] Test: update user without password field → existing password unchanged
- [ ] Write unit test for password update in service

### Quality check

- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors

### Docs update

- [ ] `docs/06-generated/endpoints.md` — note `password` field in `PUT /users/{id}` request body
- [ ] `docs/08-decisions/changelog.md` — note the fix
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Password logged in plain text via audit service | Low | Audit service logs `changes` dict — do NOT include password in changes. Log `"password": "(changed)"` instead. |
| Empty string `""` treated as password change | Medium | Add guard: `if data.password is not None and data.password != ""` — or use `Field(min_length=1)` |

---

## Definition of done

- [ ] `PUT /users/{id}` with `password` field updates the user's password
- [ ] `PUT /users/{id}` without `password` field leaves password unchanged
- [ ] User can log in with the new password after update
- [ ] Empty string password is not treated as a change
- [ ] All tests pass
- [ ] Docs updated
