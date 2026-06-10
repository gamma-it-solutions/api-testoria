# Execution Plan: Test Case `automation_id` Field

**Date**: 2026-04-15
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Add an optional `automation_id` string field to the test case domain — persisted on the `test_case` table, accepted on create/update, returned on every read — so manual cases can be linked to their automated counterpart in an external framework (Playwright spec, pytest node id, Cypress, etc.).

---

## Context

Today there is no way to record the external identifier of the automated test that covers a given test case. Manual and automated cases are tracked in the same hierarchy (`type: 'manual' | 'automated'`) but the link to the actual automation artifact is missing — when a CI run reports a failure with a Playwright spec id, there is no way for the API or the UI to look up the matching test case.

`TestCase` schemas live in `app/schemas/test_case.py` and the model in `app/models/test_case.py`. Neither references `automation_id`. The frontend (`web-testoria`) plan `plan-102-test-case-automation-id.md` depends on this plan landing first so the field round-trips through the API.

---

## Scope

### In scope
- New nullable `automation_id: str | None` column on the `test_case` table
- Alembic migration adding the column and a non-unique index for lookup
- `automation_id` accepted on `TestCaseCreate` and `TestCaseUpdate`, returned on `TestCaseResponse`
- Service layer passthrough (no transformation, no validation beyond max length)
- Optional `automation_id` filter on `GET /test-cases` (exact match) — small addition that unblocks "find the case by its automation id" lookups
- Unit + integration tests for create / update / list-filter / response

### Out of scope
- Uniqueness enforcement (multiple test cases may legitimately reference the same automation id during refactors)
- Auto-linking from CI run payloads to test cases (separate effort, would belong with the CI integration plans)
- Free-text search inside `automation_id` — only exact-match filter for now
- Per-project namespacing of automation ids — keep it global on the test case row

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_case.py` | Add `automation_id: str \| None = Field(default=None, max_length=255)` to `TestCaseCreate`, `TestCaseUpdate`, `TestCaseResponse`; add `automation_id: str \| None = None` to `TestCaseListFilters` |
| models | `app/models/test_case.py` | Add `automation_id: Mapped[str \| None] = mapped_column(String(255), nullable=True, index=True)` |
| migration | `alembic/versions/` | New revision `add_automation_id_to_test_case` — adds column + index; downgrade drops both |
| services | `app/services/test_case_service.py` | Passthrough on create/update; extend `_build_list_query` to filter by `automation_id` when supplied (exact match) |
| router | `app/api/v1/test_cases.py` | Accept `automation_id: str \| None = Query(None)` and forward to filters; no other changes (Pydantic schemas carry the field through create/update) |
| tests | `tests/unit/test_test_case_service.py` | Cover create/update with and without `automation_id`, list filter |
| tests | `tests/integration/test_test_cases_api.py` | Round-trip create → read; update; list `?automation_id=…` |

### Key decisions

- **Type and length**: `String(255)`. Wide enough for long pytest node ids (`tests/foo/bar.py::TestX::test_y[param]`) and Playwright titles, narrow enough to keep an index cheap.
- **Nullable, not unique**: a case may have no automation, and during refactors two cases can transiently point at the same id. Index for lookup speed only.
- **Indexed**: `index=True` on the column. The expected access pattern is "given an automation id from a CI report, find the test case", which must be O(log n).
- **No coupling with `type` field**: do not auto-set `type='automated'` when `automation_id` is present. Keep the two orthogonal — a case can be marked `manual` while still tracking the in-progress automation id.
- **Empty string vs null**: treat `""` as `None` on the Pydantic side (validator) so the frontend can clear the field by sending an empty string, matching how the editor's other optional text fields behave. Verify that pattern is already used; if not, document the decision.
- **No data backfill** — the column starts entirely null on existing rows.

---

## Tasks

### Implementation
- [x] Add `automation_id` to `TestCaseCreate`, `TestCaseUpdate`, `TestCaseResponse` in `app/schemas/test_case.py`
- [x] Add `automation_id: str | None = None` to `TestCaseListFilters`
- [x] Add empty-string-to-null validator on `automation_id` in `TestCaseCreate` and `TestCaseUpdate`
- [x] Add the `automation_id` column to `TestCase` in `app/models/test_case.py`
- [x] Generate Alembic migration: `alembic revision --autogenerate -m "add automation_id to test_case"`
- [x] Review the migration — ensure it adds both column and index, and that the downgrade drops both in the right order
- [x] Apply locally: `alembic upgrade head`; verify reversibility: `alembic downgrade -1` then `upgrade head`
- [x] Extend `_build_list_query` in `app/services/test_case_service.py` to honor `automation_id` (exact match)
- [x] Accept `automation_id` query param in `app/api/v1/test_cases.py` list endpoint
- [x] Unit tests in `tests/unit/test_test_case_service.py`
- [x] Integration tests in `tests/integration/test_test_cases_api.py`

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — note the new `automation_id` field on test case create/update/response and the new query param on list
- [x] `docs/06-generated/db-schema.md` — add the `automation_id` column and index to the `test_case` table row
- [x] `docs/01-product/features/` — update the test case feature doc to mention `automation_id`
- [x] `docs/08-decisions/changelog.md` — record nullable-not-unique, indexed, empty-to-null, decoupled-from-type decisions
- [x] `docs/04-execution/tech-debt.md` — log "auto-link CI runs to cases via `automation_id`" if confirmed as a follow-up
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Adding an index on a large `test_case` table locks the table during migration | Low (current data volume small) | If volume grows, switch to `CREATE INDEX CONCURRENTLY` in a separate post-deploy migration; not needed today |
| Frontend sends `""` and the column ends up as empty string instead of null | Medium | Validator coerces `""` → `None`; integration test asserts the stored value is `NULL` |
| Future uniqueness requirement forces a backfill | Low | Keep the column nullable and non-unique for now; document the constraint choice in the changelog so the reversal is deliberate |
| Autogenerate migration misses the index | Medium | Manually inspect the generated revision; add `op.create_index(...)` if absent |

---

## Definition of done

- [x] `automation_id` round-trips through `POST /test-cases`, `PATCH /test-cases/{id}`, and `GET /test-cases/{id}`
- [x] `GET /test-cases?automation_id=…` returns matching cases (exact match, can be empty)
- [x] Empty string from the client lands as `NULL` in the database
- [x] Auth and role enforcement unchanged and tested
- [x] Unit test coverage ≥ 85% for the new service paths
- [x] Integration tests cover happy path + 401 + filter
- [x] Migration applies cleanly and is reversible
- [x] Docs updated
