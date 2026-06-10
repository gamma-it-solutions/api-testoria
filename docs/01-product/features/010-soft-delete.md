# 010 — Soft Delete

## What it does

Replaces hard deletes with soft deletes across the core domain. When a user deletes a project, suite, test case, run, result, milestone, or user, the row stays in the database with a `deleted_at` timestamp instead of being removed. Deleted rows are excluded from normal queries but can be listed with `include_deleted=true` and resurrected via a restore endpoint (where applicable).

## Scope

Soft delete is applied to: `projects`, `test_suites`, `test_cases`, `test_runs`, `test_results`, `milestones`, `users`.

Not applied to: `tags`, `test_case_tags`, `result_attachments`, `result_history`, `audit_logs`, `defects`, `custom_fields` — lightweight, append-only, or system tables.

## API surface

Entities that support deletion expose the same basic shape:

- `DELETE /{entity}/{id}` → 204. Sets `deleted_at` on the row and cascade-soft-deletes owned children in the service layer.
- `POST /{entity}/{id}/restore` → 200 with the refreshed entity. Clears `deleted_at`. Returns 400 if the entity's parent is itself soft-deleted.
- List endpoints accept `include_deleted: bool = False`. When `true`, soft-deleted rows are included in the result.

Restore endpoints exist for: projects, test suites, test cases, test runs, milestones.
Test results do not have a restore endpoint — they follow their parent run's lifecycle.
Users do not have a restore endpoint — admin action or `is_active` reactivation is the recovery path.

All responses for affected entities include `deleted_at: datetime | null`.

## Cascade behavior

Cascade soft-delete is done explicitly in the service layer (not via DB triggers or ORM-level cascade), so the logic is visible and testable:

- Deleting a project soft-deletes its suites, those suites' cases, its runs, and those runs' results.
- Deleting a suite soft-deletes **the suite itself, every descendant suite via `parent_suite_id`, and every test case under any suite in that subtree** (plan-045, fixes TES-70). Implemented via a recursive Postgres CTE in `test_suite_service._descendant_suite_ids` followed by two bulk soft-delete `UPDATE`s. Sibling subtrees and other projects are untouched. Descendants that were independently soft-deleted before the cascade keep their original `deleted_at` timestamp.
- Deleting a run soft-deletes its results.

Restore does **not** cascade — restoring a parent does not resurrect its children. This prevents surprise data resurrection; users explicitly restore what they need. (Note: this creates an asymmetry with the suite delete-cascade introduced in plan-045 — a user who restores a previously-cascaded suite gets the root back with an empty subtree. Tracked in `docs/04-execution/tech-debt.md` as "Restore subtree after suite delete-cascade", pending a UX decision.)

## Constraints

- **No auto-restore of children.** Restoring a project leaves its suites, cases, runs, and results in a soft-deleted state.
- **Restore blocked while parent is deleted.** `POST /test-suites/{id}/restore` returns 400 if the suite's project is still deleted. Same for cases → suite, runs → project, milestones → project.
- **FK constraints tightened.** FKs on soft-deletable children were changed from `ON DELETE CASCADE` to `ON DELETE RESTRICT` so that nothing can silently hard-cascade past the service layer. The one exception is `test_suites.parent_suite_id`, which is `ON DELETE SET NULL` so an orphaned child suite becomes a root suite if its parent is ever hard-removed.
- **Audit log records `DELETE` and `RESTORE`** actions alongside `CREATE` / `UPDATE` / `LOGIN` / `LOGOUT`.
- **Reports, stats, and badges** exclude soft-deleted rows. Dashboards, `GET /projects/{id}/stats`, `GET /test-runs/{id}/progress`, and the CI badge endpoint all filter `deleted_at IS NULL`.

## Out of scope (future work)

- No purge / permanent-delete endpoint yet.
- No scheduled cleanup job to hard-delete rows older than N days. Tracked in `docs/04-execution/tech-debt.md`.
- Soft-delete for tags, attachments, audit logs, and history — intentionally excluded.
