# Tech Debt

Known issues and deferred improvements. Add items when debt is incurred, remove when resolved.

---

## Active items

### Integration `client` fixture shares one session across requests (plan-050 follow-up)
**Impact:** `tests/conftest.py` overrides `get_db` to yield the *same* `db_session` for every request a test makes, while production yields a fresh session (and identity map) per request. Any test that mutates data through one endpoint and then asserts on it through another can read a stale relationship: SQLAlchemy returns the identity-mapped object and `selectinload` does not refresh an already-populated collection. This produced a long-standing failure in `test_result_response_exposes_attachment_urls` (attachment created successfully, list endpoint reported zero) that looked like a product bug and was not.
**Fix:** Either expire the session between requests in the `client` fixture (closest to production, but may surface staleness in other tests that currently rely on the cache), or give each request its own session from the same transaction/connection. Until then, tests crossing a write→read boundary need an explicit `db_session.expire_all()` — as that test now does, with a comment explaining why.

### API key project scope is a write guard, not a read ACL (plan-050)
**Impact:** A key minted with `project_id` is enforced on `POST /test-runs/{id}/results/import`, but `project_id` is not in the path on most endpoints, so a scoped key can still **read** other projects at its effective role (`tester`). Users may reasonably read "scoped to project 7" as stronger than it is.
**Fix:** Either thread the scope through a shared guard on every project-derived route (needs a resolver from run/suite/case → project on each), or resolve scope once in `get_principal` and filter list queries centrally. Until then the limit is documented in `docs/02-architecture/backend/auth.md`.

### `test_results.execution_time` is integer seconds, so sub-second tests round to 0 (plan-050)
**Impact:** JUnit reports `time` as float seconds. Plan 050 changed truncation to rounding (`int(0.6)` → 0 became `round(0.6)` → 1), but a 120 ms unit test still stores 0. For fast automated suites the column is close to meaningless, and per-test duration trends cannot be built from it.
**Fix:** Migrate `execution_time` to milliseconds (or `Numeric`), backfill `value * 1000`, and update the web display plus `TestResultCreate`/`Update`. Cross-repo: web-testoria renders it.

### Web does not consume the `test_result_bulk` realtime event (plan-050)
**Impact:** `submit_many` publishes **one** aggregate `test_result_bulk` event instead of N per-result `test_result` events — deliberate, because a 2000-case CI import must not fire 2000 publishes. The web client only subscribes to `test_result`, so a run being filled by a CI import does not update live until reload.
**Fix:** Subscribe to `test_result_bulk` in web-testoria and refetch the run's results on receipt. Additive — per-result events still fire for UI-driven submits.

### Retire `POST /ci/results/bulk` in favour of `/test-runs/{id}/results/import` (plan-050)
**Impact:** Two import paths now exist. The old one matches by title only, returns counts with no detail, issues one `SELECT` per `<testcase>`, and calls the full per-result `submit()` (≈6 queries + a Centrifugo publish each). It is kept because feature 009 documents it publicly.
**Fix:** Once logs show no inbound traffic for ≥ 2 weeks, delete the route, `ci_service.import_junit_xml`, and its tests; update feature 009 and `endpoints.md`.

### No rate limiting on `forgot-password` (plan-048)
**Impact:** `POST /auth/forgot-password` is public and always returns `202`. Without throttling it can be abused to spam a victim's inbox (one queued email per request) or to probe timing. It does not enumerate users (constant response), but it is unbounded.
**Fix:** Add per-IP + per-email rate limiting (e.g. Redis token bucket) before heavy public use, and consider a captcha. The Redis client (`app/core/redis.py`) is already available.

### Celery-based outbox drain for horizontal scale-out (plan-048)
**Impact:** The email outbox is drained by an in-process loop in the API's FastAPI lifespan (`app/core/email_worker.py`). It's correct across replicas via `FOR UPDATE SKIP LOCKED`, but throughput is bounded by the API processes and pacing. At high volume the drain may lag.
**Fix:** Move draining to a dedicated Celery worker (broker already configured: `CELERY_BROKER_URL`). Keep the same outbox table + claim semantics; just relocate the loop. Decide on a deployment topology (one drain worker vs. many).

### Transactional email provider (SES/SendGrid) above Gmail caps (plan-048)
**Impact:** Gmail SMTP caps volume (~500/day consumer, ~2,000/day Workspace) and throttles connection churn. Bulk onboarding of a large org could hit the daily cap; the outbox would back up and links could expire before delivery.
**Fix:** Swap the transport in `app/core/email.py` for SES or SendGrid (API or SMTP) when sustained volume approaches the cap. The outbox/worker abstraction already isolates the transport. Alert on `email_outbox` backlog age > TTL/2 in the meantime.

### Outbox dead-letter replay endpoint / admin UI (plan-048)
**Impact:** Rows that exhaust `max_attempts` move to `failed` with `last_error` kept, but there is no API/UI to inspect or replay them — triage is a manual SQL `UPDATE … SET status='pending'` (documented in `docs/03-engineering/operations/email.md`).
**Fix:** Add an admin-only endpoint to list `failed` rows and requeue one/all, plus a pending-count / oldest-pending metric for alerting. Pairs with a small outbox admin view in web-testoria.

### `cd.yml` deploy reports success even when the deploy is broken (plan-047 follow-up)
**Impact:** The SSH deploy script has no `set -euo pipefail` and its last command is `docker image prune -f` (always exits 0). A failing health check, a failed `nginx -t`, or a 502 on the final `curl` does **not** fail the GitHub Actions run — the deploy went out half-broken (api detached from the old proxy, API returning 502) while the workflow showed green. Observed live during the 2026-06-01 cutover.
**Fix:** Add `set -euo pipefail` to the script; make the post-deploy health check and the final public `curl --fail` gate the run (exit non-zero on failure); only prune images after success. Consider guarding the host-nginx `install`/`reload` steps so a missing/not-yet-bootstrapped nginx fails loudly instead of being ignored.

### Host nginx config drift / passwordless sudo at deploy (plan-047)
**Impact:** Since the edge moved to host-level nginx, `cd.yml` installs `deploy/api.vhost.conf` + `deploy/nginx-maps.conf` into `/etc/nginx` and runs `sudo systemctl reload nginx` as the deploy user. The host filesystem — not the container image — is now the source of truth for what nginx actually serves, and the deploy user holds a (scoped) passwordless-sudo grant.
**Fix:** Keep the `sudoers.d` grant tightly scoped (see `deploy/README.md`); rely on `nginx -t` before every reload (already wired). Longer term, consider config management (Ansible) or a periodic `nginx -T` vs. repo diff check to detect manual drift on the box.

### Centrifugo realtime not exposed through the host edge (plan-047 follow-up)
**Impact:** `centrifugo` has no host port and no public server block, so the browser can't reach it through the edge. Realtime updates are unavailable end-to-end until this is wired (pairs with web's "Wire Centrifugo `TestRunStatusChanged` subscription").
**Fix:** Publish centrifugo on a loopback port and add a `ws.testoria.gammait.net` (or `api.*/connection`) server block with the `Upgrade`/`Connection` headers (the `$connection_upgrade` map already ships in `deploy/nginx-maps.conf`). Decide on subdomain vs. path routing first.

### Display-order rebalance helper (plan-046 follow-up)
**Impact:** Both `test_suites.display_order` and `test_cases.display_order` use a frontend gap-based bisect (`(prev + next) // 2`) to compute the new value on drop. After enough bisects in the same gap, the integer floor collapses to `prev + 1`, eventually causing collisions. Once two siblings share a `display_order`, the secondary `(created_at, id)` sort resolves it, but reorders on that pair stop having a visible effect.
**Fix:** Add a service-level `renumber_siblings(scope)` that walks a sibling group (same `parent_suite_id` for suites, same `suite_id` for cases) and reassigns `display_order = REORDER_GAP * index` in a single bulk UPDATE. Trigger automatically when the FE detects a tight gap, or expose as an admin-only endpoint. Only worth shipping when a real workload starts hitting collisions.

### `restore_suite` does not restore subtree after delete-cascade (plan-045 follow-up)
**Impact:** After plan-045 (TES-70), `DELETE /test-suites/{id}` cascades to descendants and their cases — but `POST /test-suites/{id}/restore` only restores the single suite row. A user who restores a previously-cascaded delete will get the root suite back with an empty subtree; descendants stay soft-deleted.
**Fix:** Make `restore_suite` symmetric with `delete_suite`: walk the descendant tree and restore every suite + case that was soft-deleted **after** the root's `deleted_at` (a stricter window than "any descendant" — preserves the original `deleted_at` of descendants that were independently deleted before the cascade). Needs a UX decision before implementing: should the user see a "restored partially" hint when descendants are skipped, or should the strict-window logic be invisible? Discuss with QA before coding.

### MinIO root credentials reused as S3 API credentials in prod
**Impact:** A leaked or rotated `S3_ACCESS_KEY` would force a MinIO root credential rotation (and vice versa). Blast radius of either secret is the entire MinIO instance, not just the attachments bucket.
**Fix:** After first prod deploy, create a scoped MinIO user via `mc admin user add` limited to `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` on `testoria-attachments`, then point `S3_ACCESS_KEY`/`S3_SECRET_KEY` at that user and remove `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from `.env.prod` (keep them only in MinIO's startup environment).

### No startup assertion that S3_PUBLIC_ENDPOINT_URL is publicly reachable
**Impact:** If the reverse-proxy route to MinIO is missing or broken, uploads succeed (internal path) but presigned URLs returned to the browser 404. The first user-visible error is a broken `<img>` tag.
**Fix:** On startup, generate a presigned URL for a known bucket-internal probe object and HEAD it from outside the docker network. Or rely on a synthetic monitor against the public endpoint.

### No token blocklist on logout
**Impact:** Access tokens remain valid until expiry after logout — a stolen token can still be used.
**Fix:** Store token JTI (JWT ID) in Redis on logout; check JTI on every request. Requires adding `jti` claim to token creation.

### asyncpg greenlet conflict in session-scoped test fixture
**Impact:** Integration tests fail when `TEST_DATABASE_URL` points to Postgres. The `test_user` fixture calls `db_session.flush()` inside a session-scoped async fixture, triggering `asyncpg.InterfaceError: cannot perform operation: another operation is in progress`.
**Root cause:** `pytest-asyncio 0.23.6` session-scoped async fixtures share a single event loop; concurrent async operations on the same asyncpg connection collide.
**Fix:** Either pin `asyncio_mode` and `asyncio_scope` correctly in `pyproject.toml`, or restructure `setup_test_db` + `test_user` to use function scope and a dedicated per-test engine.

### Test coverage below target for some phases
**Impact:** Untested code paths may contain bugs discovered only during integration.
**Detail:** Phase 2 test coverage <85% target; Phase 8 <80% target.
**Fix:** Add missing service unit tests and endpoint integration tests.

### Per-step status history
**Impact:** The existing `ResultHistory` tracks only overall status changes. Per-step status changes are not audited — if a tester re-marks a step from failed to passed, there is no record of the previous step state.
**Fix:** Either extend `ResultHistory` to include `step_results` snapshots, or add a dedicated `step_result_history` table. Low priority unless product requires step-level audit trail.

### Stable step IDs for test cases
**Impact:** `step_results` uses positional indices. If steps on a test case are reordered after results are recorded, the stored indices become meaningless.
**Fix:** Add stable `id` fields to each step in the `test_case.steps` JSON. Requires a backfill migration and schema change on both sides.

### Consider backfilling legacy runs into test_run_test_cases
**Impact:** Legacy runs (created before plan 025) use the fallback `suite_id` scoping path. If the fallback code is ever removed, those runs would appear to have zero cases.
**Fix:** One-shot SQL: `INSERT INTO test_run_test_cases SELECT tr.id, tc.id FROM test_runs tr JOIN test_cases tc ON tc.suite_id = tr.suite_id WHERE tr.suite_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM test_run_test_cases WHERE test_run_id = tr.id)`. Run once, then the fallback path can be removed.

### Project-scoped tags
**Impact:** Tags are currently global. If multiple projects use the same tag names with different meanings, there is no way to distinguish them.
**Fix:** Add `project_id` FK to `tags` table, update unique constraint to `(project_id, name)`, and update `tag_service` + tag resolution in `test_case_service`. Breaking change — requires migration and frontend updates.

### Legacy attachment shim + local-disk migration
**Impact:** Pre-plan-042 attachments live on local disk (`storage_backend='local'`). The shim `GET /files/legacy/{id}` streams them for backward compat; the route and the legacy code path in `delete_attachment` can't be removed until every row is migrated to MinIO.
**Fix:** Run `python scripts/migrate_attachments_to_minio.py --commit --delete-local` in prod after deployment of plan 042. Verify `SELECT COUNT(*) FROM result_attachments WHERE storage_backend='local'` == 0, then drop the shim router and the `storage_backend='local'` branch in the service.

### Orphan object GC in MinIO bucket
**Impact:** If a DB insert fails after `storage.put_object` succeeds, the service tries to clean up — but any further failure leaves the object stranded. Over time the bucket accumulates orphans.
**Fix:** Nightly Celery task that lists `SELECT object_key FROM result_attachments WHERE storage_backend='s3'` vs `aws s3 ls s3://{bucket}/results/`; delete the set difference (objects with no row).

### Virus scanning of image uploads
**Impact:** Uploaded images hit MinIO directly without AV scanning. A malicious file could theoretically reach another user via presigned URL.
**Fix:** Wire ClamAV (`python-clamd`) into `app/core/uploads.validate_image_upload` before `storage.put_object`.

### Record `step_results` diffs as history events
**Impact:** Plan 038 records history on `status` / `comment` changes only. Per-step status changes (plan 031) are silently missed by the audit trail.
**Fix:** Extend `_should_record_history` to hash-compare the normalised `step_results` list and record on diff. Needs a concise serialisation on the history row so the timeline can render what changed.

### Backfill / compact legacy redundant `result_history` rows
**Impact:** Rows written before plan 038 include no-op resubmits. Web timeline (plan 063) dedups at render, but the DB still carries the noise.
**Fix:** Write a one-shot migration that collapses consecutive identical `(status, comment)` rows per result_id, keeping the earliest per run. Needs product sign-off before running against prod.

### Drag-and-drop reorder UI for test suites
**Impact:** Plan 037 added `display_order` on `test_suites` but no reorder endpoint. Users can't change suite order without touching the DB.
**Fix:** Add `PATCH /test-suites/{id}` accepting `display_order` (already supported in the schema) plus a bulk reorder endpoint when product asks for it. Web drag-and-drop follows.

### "Reset to auto" affordance on `test_runs.cases_mode`
**Impact:** Plan 034 lets a run move from `auto` to `explicit`, but never back. A user who clicks "manual selection" and regrets it has no one-click undo; they'd have to delete and recreate the run.
**Fix:** Add an endpoint (or flag on `PUT /test-runs/{id}`) that sets `cases_mode='auto'` and wipes the junction-table rows in one transaction. Gate behind product confirmation — not a current user complaint.

### Remove deprecated `status` field from `TestCaseWithResult`
**Impact:** Plan 033 added `case_status` as the unambiguous name but kept `status` for one release. Clients still reading `cases[i].status` will break whenever the field is dropped.
**Fix:** After web plan 055 rolls out and logs show no consumers reading the old field for ≥ 2 weeks, drop `status` from `TestCaseWithResult` and the service-side alias.

### Server-side filtering on `GET /test-runs/{id}/cases`
**Impact:** Large runs (thousands of cases) transfer every row to the client even when the UI only wants `no_run` ones. Plan 033 deferred `?status=` / `?tag=` filters.
**Fix:** Add allow-listed filter params (`status`, `tag_ids`, `search`) to `get_with_cases`. The tricky piece is `status=no_run` meaning "case has no result row" — translate to an `outerjoin … WHERE TestResult.id IS NULL` predicate.

### Cursor-based pagination for run-cases
**Impact:** Offset pagination works up to a few thousand cases. If a project grows past that, `OFFSET n` becomes expensive.
**Fix:** Add an additive `cursor` param to `GET /test-runs/{id}/cases` that encodes `(suite_id, case_id)` and translates to a keyset predicate. Keep offset as a fallback.

### Remove `"in_progress"` compat alias from `TestRunUpdate.status`
**Impact:** Plan 039 renamed `in_progress` → `active`. `TestRunUpdate.status` still accepts `"in_progress"` on input and normalises it to `"active"` so in-flight clients (web, CLI) don't break during the rollout. Keeping both forever is confusing and keeps dead branch in the Pydantic validator.
**Fix:** After web-testoria plan-070 ships and server logs show zero inbound `"in_progress"` traffic for ≥ 2 weeks, drop `"in_progress"` from `_TestRunStatusInput`, remove `_normalise_run_status` and the `_coerce_status` validator, and collapse the Literal. Also clean up the `list_runs` query-param handling in `app/api/v1/test_runs.py`.

### Convert `test_runs.status` to a PostgreSQL ENUM type
**Impact:** Status is a plain `String(50)` column; invalid values can be inserted if a code path bypasses the Pydantic schema. Plan 039 did a data rewrite but kept the column type — partly to keep the `in_progress` compat alias simple.
**Fix:** After the compat alias above is removed, create a PG enum `run_status ('planned','active','completed','aborted')` and alter the column. Requires a migration that casts existing values.

### Remove `"skipped"` compat shim from `TestResult.status` Literal
**Impact:** `Literal["passed","failed","blocked","no_run","skipped"]` accepts both values after plan 032, even though every row persisted is `no_run`. Keeps the compat window open indefinitely if nobody closes it.
**Fix:** After the web/CLI rollout is confirmed and dashboards show zero inbound `"skipped"` traffic for ≥ 2 weeks, remove `"skipped"` from the Literal and drop `_normalise_status` from `test_result_service`.

### Convert `test_results.status` to a PostgreSQL enum type
**Impact:** Status is a plain `String(50)` column; invalid values can be inserted if a code path bypasses the Pydantic schema. Plan 032 did a data rewrite but did not change the column type.
**Fix:** Create a PG enum `result_status ('passed','failed','blocked','no_run')` and alter the column. Do the same for `result_history.status`. Requires a migration that casts existing values.

### No purge / permanent-delete pathway for soft-deleted rows
**Impact:** Soft-deleted data accumulates forever. No admin tool to hard-purge old rows or scheduled cleanup job.
**Fix:** Add a scheduled Celery task (or admin endpoint) to hard-delete rows with `deleted_at < now() - retention_window`, with explicit confirmation and audit logging. Also consider a partial index (`WHERE deleted_at IS NOT NULL`) if the soft-delete filter becomes a hot path.

---

## Resolved

### Auto-link CI runs to test cases via `automation_id` (resolved 2026-08-10 — plan 050)
`POST /test-runs/{run_id}/results/import` matches on `automation_id` before falling back to `title`, and reports which rule matched via `matched_by`. The load-bearing addition is the `dotted()` rule: pytest's JUnit output carries no node ID (`junit_family=xunit2` emits neither `file` nor `line`), so a stored node ID like `tests/a/test_a.py::TestA::test_x` is normalised to `tests.a.test_a.TestA.test_x` before comparing with `classname.name`. Verified end-to-end against real pytest 8.3.5 output. The legacy `POST /ci/results/bulk` is unchanged — its retirement is tracked above.

### Dockerized edge proxy coupling: shared `testoria-proxy` network + `resolver` startup hack (resolved 2026-06-01)
Plan 047 moved the edge to host-level nginx. The cross-repo `testoria-proxy` docker network (created by the frontend repo, joined here as `external`) and the `resolver 127.0.0.11 valid=10s` hack that worked around containers vanishing at nginx start are both gone. This stack now runs entirely on the private `internal` network and `api` comes up without any dependency on the web repo's deploy order.

### Phase 3 Amendment — API contract gaps (resolved 2026-06-01)
All four contract gaps required for frontend integration shipped:
- `TestResult` now has `message` and `stack_trace` columns (`app/models/test_result.py`).
- `result_history` table + `ResultHistory` model + `GET /test-results/{id}/history` endpoint exist (`app/api/v1/test_results.py` → `get_history`).
- `GET /test-runs/{id}/cases` exists (`app/api/v1/test_runs.py` → `get_run_with_cases`).
- `DELETE /test-results/{id}/attachments/{attach_id}` exists (`app/api/v1/test_results.py` → `delete_attachment`).

### Phase 4 — WebSocket real-time updates (resolved 2026-03-25)
Centrifugo v5 integrated. Connection/subscription token endpoints, publish calls wired in result/run/case services. See plan 008.

### N+1 per-run status count in `report_service.get_dashboard()` (resolved 2026-04-17)
The dashboard loop at `report_service.py:104–125` issued one `SELECT ... GROUP BY status` per recent run. Replaced with a single grouped query via the new `_aggregate_run_status_counts(db, run_ids)` helper, which is also reused by `get_report_analytics()`. See plan 027.

### Dashboard pass-rate included in-flight runs (resolved 2026-04-22)
`DashboardResponse.pass_rate`, `ReportAnalyticsSummary.overall_pass_rate` / `result_distribution` / `trend`, `ProjectStats.pass_rate`, and `ProjectStatsItem.pass_rate` aggregated results across every run regardless of status, so a project with one in-flight run and one passed case could display "100% pass rate". All four endpoints now filter by `TestRun.status == 'completed'`. See plan 039.

### Synchronous file I/O in attachment handler (resolved 2026-04-22)
Attachment uploads wrote via `Path.write_bytes()` on the event loop. Plan 042 replaced the entire backend with `aioboto3.put_object` against MinIO / S3 — async by construction, and object storage is a better fit for multi-replica deployment than shared filesystem. See plan 042.
