# Execution Plan: Tags CRUD + Test Case Filter by Tag

**Date**: 2026-04-15
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Expose proper REST endpoints for tags (list, search, create) and add a multi-value `tag_ids` filter to the test case list endpoint, so the frontend can search/create tags inline on the test case editor and filter test cases by tag on the project test cases page.

---

## Context

The frontend currently calls `GET /tags` and `POST /tags` (see `web-testoria/src/api/tags.ts`) but **no tags router exists** in the backend (`app/api/v1/` has no `tags.py`; tag resolution is inline-only inside `app/services/test_case_service.py::_resolve_tags` at lines 36–49). Those frontend calls 404 in production.

In parallel, the frontend wants to add a multi-select tag filter on the project test cases page (`/projects/:id/test-cases`). The current test case list query (`app/services/test_case_service.py::_build_list_query`, lines 52–76) filters by `suite_id`, `priority`, `type`, `status`, `search` — there is no tag filter.

The `Tag` model and the `test_case_tags` association table already exist (`app/models/tag.py:14–45`) and the test case ↔ tag relationship is configured with `lazy="selectin"` (`app/models/test_case.py:45–50`), so the data layer is in place — only the API surface and one query addition are needed.

Related: plan `021-be-fix-test-case-tags-response.md` (active) is about the response shape of tags inside the test case payload — separate concern, this plan does not touch it.

---

## Scope

### In scope
- New `app/api/v1/tags.py` router with: list, search-by-name, create
- New `app/services/tag_service.py` extracted from the inline `_resolve_tags` helper
- Add `tag_ids: list[int] | None` filter to `TestCaseListFilters` and `_build_list_query`
- Pydantic schemas in `app/schemas/tag.py` (`TagCreate`, `TagResponse`)
- Wire router in `app/main.py`
- Unit tests for `tag_service` and integration tests for the new endpoints + the new filter

### Out of scope
- Tag rename / delete / merge (no frontend need yet)
- Tag colors or grouping
- Per-project tag scoping — tags remain global (matches current model)
- Changing the tags array shape inside `TestCaseResponse` (owned by plan 021)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/tag.py` (new) | `TagCreate { name }`, `TagResponse { id, name }` |
| schemas | `app/schemas/test_case.py` | Add `tag_ids: list[int] \| None` to `TestCaseListFilters` |
| services | `app/services/tag_service.py` (new) | `list_tags`, `search_tags(q, limit)`, `create_tag(name)` (idempotent — return existing on conflict), `get_or_create_many(names)` (move `_resolve_tags` here) |
| services | `app/services/test_case_service.py` | `_build_list_query` joins `test_case_tags` and filters by `tag_ids` (use `.in_()` + `.distinct()`); `_resolve_tags` becomes a thin call into `tag_service.get_or_create_many` |
| router | `app/api/v1/tags.py` (new) | `GET /tags` (list, optional `?q=` for prefix/contains search, `?limit=` default 50, max 200), `POST /tags` (create, requires auth) |
| router | `app/api/v1/test_cases.py` | Accept repeated `tag_ids` query params (`tag_ids: list[int] = Query(default=None)`) and pass through filters |
| main | `app/main.py` | Register `tags_router` under `/api/v1` |
| tests | `tests/unit/test_tag_service.py` (new) | Cover create idempotency, search case-insensitive, get_or_create_many de-duplication |
| tests | `tests/integration/test_tags_api.py` (new) | Happy paths + 401 |
| tests | `tests/integration/test_test_cases_api.py` | Add cases for filtering by single and multiple `tag_ids` (AND-vs-OR semantics — see decision below) |

### Key decisions

- **Tag name uniqueness**: enforce by lowercasing the name on write and storing both `name` (display) and a generated `slug` if needed. Simpler alternative: keep schema as-is, add a unique index on `lower(name)` via Alembic. Pick the unique-index approach to avoid touching existing rows or response shapes. Confirm no migration already added it; if not, add one.
- **`POST /tags` is idempotent**: if a tag with the same normalized name already exists, return it with `200 OK` (not 201). This avoids racing creates from the autocomplete + filter UIs hammering the endpoint with the same string.
- **Search semantics**: `?q=foo` does a case-insensitive `ILIKE 'foo%'` (prefix). Prefix is faster, supports btree on `lower(name)`, and matches the autocomplete UX. Document this — frontend should not expect substring search.
- **`tag_ids` filter is OR semantics** (test case has *any* of the given tags). AND semantics ("has all") is rare in test management UIs and adds query complexity (`HAVING count(distinct …) = N`). Out of scope unless requested.
- **No tag scoping by project**: tags stay global to match the current model. If product wants project-scoped tags later, that is a migration + breaking change — log as tech debt if confirmed.
- **Auth**: `GET /tags` requires authenticated user (no role); `POST /tags` requires authenticated user (any role that can edit test cases). Reuse `get_current_user`.

---

## Tasks

### Implementation
- [x] Define `TagCreate` and `TagResponse` Pydantic schemas in `app/schemas/tag.py`
- [x] Add `tag_ids: list[int] | None = None` to `TestCaseListFilters` in `app/schemas/test_case.py`
- [x] Create `app/services/tag_service.py` with `list_tags`, `search_tags`, `create_tag` (idempotent), `get_or_create_many`
- [x] Refactor `_resolve_tags` in `app/services/test_case_service.py` to delegate to `tag_service.get_or_create_many`
- [x] Extend `_build_list_query` in `app/services/test_case_service.py` to honor `tag_ids` (join `test_case_tags`, `.in_()`, `.distinct()`)
- [x] Create Alembic migration adding a unique index on `lower(tag.name)` if one does not already exist
- [x] Apply migration locally (`alembic upgrade head`) and confirm reversibility (`alembic downgrade -1` then `upgrade head`)
- [x] Create `app/api/v1/tags.py` router with `GET /tags`, `GET /tags?q=…`, `POST /tags`
- [x] Update `app/api/v1/test_cases.py` to accept `tag_ids` as repeated query params and forward to the service
- [x] Wire `tags_router` in `app/main.py` under `/api/v1`
- [x] Write unit tests in `tests/unit/test_tag_service.py`
- [x] Write integration tests in `tests/integration/test_tags_api.py`
- [x] Add tag-filter cases to `tests/integration/test_test_cases_api.py`

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — add `GET /tags`, `GET /tags?q=`, `POST /tags`, document new `tag_ids` query param on `GET /test-cases`
- [x] `docs/06-generated/db-schema.md` — note the new unique index on `lower(tag.name)` if added
- [x] `docs/02-architecture/ARCHITECTURE.md` — add `tag_service` to the codemap and the "Where is X?" table
- [x] `docs/02-architecture/backend/service-layer.md` — note the extraction of tag handling from `test_case_service`
- [x] `docs/01-product/features/` — create `NNN-tags.md` describing the tag feature surface
- [x] `docs/08-decisions/changelog.md` — record idempotent POST, prefix search, OR-semantics filter, global-not-scoped decision
- [x] `docs/04-execution/tech-debt.md` — log "project-scoped tags" if product confirms interest
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing duplicate tag rows block the new unique index migration | Medium | Migration runs a dedup step first (keep lowest id, repoint `test_case_tags` rows) before creating the index |
| Race between two concurrent `POST /tags` with the same name | Low | Idempotent service: catch `IntegrityError`, re-fetch by name, return existing row |
| `tag_ids` filter triggers N+1 or duplicate rows | Medium | Single join with `.distinct()`; assert in integration tests that result count matches expectations |
| `lazy="selectin"` on test case → tags causes greenlet errors under the new join | Low | Already in use elsewhere; integration tests will surface any regression |

---

## Definition of done

- [x] All new endpoints return correct status codes and response shapes
- [x] `POST /tags` is idempotent (returns existing row on duplicate name, no 500)
- [x] `GET /test-cases?tag_ids=1&tag_ids=2` returns test cases that have either tag, no duplicates
- [x] Auth and role enforcement tested (401 on unauthenticated)
- [x] Unit test coverage ≥ 85% for `tag_service`
- [x] Integration tests cover happy path + 401
- [x] Migration applies cleanly and is reversible
- [x] Docs updated
