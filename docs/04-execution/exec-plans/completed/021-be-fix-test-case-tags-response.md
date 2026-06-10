# Execution Plan: Fix TestCase Tags Response Schema

**Date**: 2026-04-08
**Author**: Claude
**Status**: Draft

---

## Goal

Return full tag objects (`{ id, name }`) instead of plain tag name strings in the `TestCaseResponse` schema, so the frontend can display and manage tags correctly.

---

## Context

The `TestCaseResponse` Pydantic schema has a `field_validator` (`extract_tag_names`) that converts tag ORM objects to a `list[str]` of names. The frontend `TestCase` type expects `tags: Tag[]` (objects with `id` and `name`). This mismatch causes the edit page to receive tag names as strings instead of objects, breaking tag display and tag ID-based operations.

The frontend also sends `tag_ids: number[]` on update, but the backend `TestCaseUpdate` schema expects `tags: list[str]` (names). Since the backend's `_resolve_tags` service function creates-or-finds tags by name, keeping name-based input for create/update is correct — but the frontend needs to adapt to send names instead of IDs. That's a frontend-only change. The backend change here is only the response schema.

---

## Scope

### In scope

- Change `TestCaseResponse.tags` from `list[str]` to `list[TagResponse]` (objects with `id` and `name`)
- Create a `TagResponse` schema (or reuse if one exists)
- Remove the `extract_tag_names` field validator

### Out of scope

- Changing `TestCaseCreate.tags` or `TestCaseUpdate.tags` input format (keep as `list[str]` names — the service's `_resolve_tags` works with names)
- Tag CRUD endpoints (already exist)
- Frontend changes (separate plan)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_case.py` | Add `TagResponse` (or import from shared), change `TestCaseResponse.tags` type to `list[TagResponse]`, remove `extract_tag_names` validator |

### Implementation

In `app/schemas/test_case.py`:

```python
class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str

class TestCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # ...
    tags: list[TagResponse]  # was list[str]
    # Remove the extract_tag_names field_validator
```

Since `TestCase.tags` is a relationship with `lazy="selectin"`, the ORM objects are loaded and Pydantic's `from_attributes=True` will serialize them directly into `TagResponse` objects — no custom validator needed.

### Key decisions

- **Return full objects, not just names**: The frontend needs `id` for display, selection, and removal. Returning only names forces the frontend to look up IDs separately.
- **Keep create/update as `list[str]`**: The backend's `_resolve_tags` creates tags by name if they don't exist. This is the correct UX — users type tag names, not IDs.

---

## Tasks

### Implementation

- [ ] Add `TagResponse` schema to `app/schemas/test_case.py` (or a shared `app/schemas/tag.py`)
- [ ] Change `TestCaseResponse.tags` type from `list[str]` to `list[TagResponse]`
- [ ] Remove the `extract_tag_names` `field_validator`
- [ ] Verify `GET /test-cases/{id}` returns tags as `[{ "id": 1, "name": "smoke" }]`
- [ ] Verify `GET /projects/{id}/test-cases` list endpoint also returns tag objects
- [ ] Update existing tests if they assert on tag response format

### Quality check

- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors

### Docs update

- [ ] `docs/06-generated/endpoints.md` — update TestCase response schema to show tags as objects
- [ ] `docs/08-decisions/changelog.md` — note tags response format change
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Frontend breaks if deployed before frontend fix | Low | Frontend already expects `Tag[]` objects — this change makes the API match the frontend type |
| Import/export flows depend on tag format | Low | Import uses `TestCaseCreate.tags` (names) — unaffected. Export may need check. |

---

## Definition of done

- [ ] `GET /test-cases/{id}` returns `tags: [{ "id": 1, "name": "smoke" }]`
- [ ] `POST /projects/{id}/test-cases` and `PUT /test-cases/{id}` still accept `tags: ["smoke", "regression"]` (names)
- [ ] All tests pass
- [ ] Docs updated
