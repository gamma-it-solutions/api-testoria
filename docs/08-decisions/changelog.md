# DECISION CHANGELOG — Backend

Record of significant architectural and design decisions.
For formal ADRs, see `docs/02-architecture/decisions/`.

---

## 2026-08-06 — CD: production domains restored, GH_PAT dropped for GITHUB_TOKEN

### What
- Reverted the four `testoria-test.gammait.net` values introduced in `2c73fc5`
  back to the production domains in `cd.yml`: `CORS_ORIGINS`,
  `S3_PUBLIC_ENDPOINT_URL`, `FRONTEND_BASE_URL`, and the post-deploy health-check
  URL.
- **Removed `GH_PAT` entirely.** The host-side `git clone`/`pull` and the GHCR
  login both use the automatic `GITHUB_TOKEN` (passed as `GH_TOKEN`), with an
  explicit `permissions: {contents: read, packages: read}` on the deploy job.
  `GHCR_USER` is back to plain `${{ github.actor }}`.
- Added `set -euo pipefail` to the deploy script and moved every secret guard to
  the top, ahead of the clone and the registry login.
- Added `-T` to the in-container `docker compose exec` health check.

### Why
`2c73fc5` changed only the `.env.prod` values, not `deploy/api.vhost.conf` — which
the same deploy script installs and which serves `api.testoria.gammait.net` /
`s3.testoria.gammait.net` under a cert issued for those names. The two halves of
one deploy disagreed: the final `curl` hit a hostname with no vhost and no cert
(so every deploy ended red *after* mutating the live stack), presigned attachment
URLs pointed at an unserved `s3.*` host, and `CORS_ORIGINS` excluded the real
frontend while `centrifugo/config.json` still allowed only the production origins.

The GHCR login paired `GH_PAT` with `GHCR_USER`, and a PAT only ever
authenticates as its own owner — `github.actor` works until someone other than
the PAT owner merges to main. In practice `GH_PAT` also resolved to an empty
string in the deploy job (never diagnosed further, since the token switch removed
the dependency), which surfaced as docker's misleading `cannot perform an
interactive login from a non TTY device` — that message means the password
arriving on stdin was empty, not that a TTY was wanted.

Without `set -e`, that failed login and the failed `docker pull` after it did not
stop the run: the script carried on for another 40 lines and only died at a
secret guard. Had that guard passed, `compose up` would have restarted the stack
on whatever stale image was already on the host.

### Decisions / trade-offs
- **Reverted rather than parametrised.** A real staging environment needs the
  domains templated across `cd.yml`, `deploy/api.vhost.conf`, and
  `centrifugo/config.json` together (GitHub `vars.*` + an envsubst step on the
  vhost), plus its own DNS and certs. Out of scope here; if staging comes back,
  do it as a plan rather than by editing the four env lines again.
- **`GITHUB_TOKEN` over a PAT.** No secret to create, scope, or rotate, and the
  username stops mattering (GHCR ignores it when the password is a
  `GITHUB_TOKEN`). Cost: the token dies with the job, so a human on the EC2 box
  cannot `docker pull` by hand without `docker login ghcr.io` under their own
  credentials. Accepted — manual pulls on the host are not part of any routine.
- The `permissions:` block is required, not decorative: with a restricted org
  default the token would carry no `packages` scope and the pull would 401.
- **No apostrophes in `${VAR:?word}` guard messages.** The word is
  quote-processed, so a lone `'` (as in `the job's permissions`) opens a quote
  that swallows the rest of the file and makes the whole script a syntax error.
  Caught by extracting the script from the YAML and running `bash -n` over it —
  worth repeating after any edit to this step.

---

## 2026-06-03 — Plan 049: Invite-only user creation, opened to Lead + Admin

### What
- Removed public self-registration: deleted `POST /auth/register` and the
  `REGISTRATION_OPEN` setting. Accounts are created only via `/users*`.
- Opened all `/users*` endpoints from Admin-only to **Lead or Admin**
  (`require_role(LEAD, ADMIN)`).
- Made creation **invite-only** by removing the `password` field from
  `UserCreate` (and therefore `UserBulkCreate`). `create_user` /
  `bulk_create_users` always mint an unusable random hash + enqueue the welcome
  set-password invite. `_password_hash_for` → `_unusable_password_hash`.
- Added a **Lead-capped-at-Lead** privilege-escalation guard in `user_service`
  (`_assert_can_manage_role`, `_assert_can_manage_user`): a non-Admin actor
  cannot create a user with `role=admin`, elevate any user to `admin`, or
  update/delete an existing Admin (403). The router forwards the authenticated
  actor to the mutating service functions.

### Why
Product wanted Leads (not just Admins) to onboard users, no open self-signup, and
no password handling by staff — the email invite (plan 048) becomes the only way
a password is ever set.

### Decisions / trade-offs
- **Guard lives in the service, not the router** — invariant #1 (no business
  logic in routers) and `require_role` is too coarse to express "Lead but not on
  Admins". The router passes `actor`; the service decides.
- **`actor` is an optional kwarg defaulting to `None` = unrestricted** — seed
  scripts and the token/reset flows call the service without an actor; every
  externally reachable path goes through the router, which always passes one.
- **Invite-only enforced by schema removal, not validation** — dropping the
  field makes "no password at creation" structurally impossible and fixes bulk
  create for free (it is `list[UserCreate]`).
- **No migration** — only the create input contract changed; the DB is untouched.
- `UserUpdate.password` (admin direct password set on edit) is intentionally
  retained and out of scope.

### Cross-repo
Paired with web-testoria plan-098 (Lead reaches `/users`, password field removed
from the form + bulk CSV, client-side role ceiling).

### Follow-up (same day) — informative create conflicts
Create/bulk conflicts now name the colliding field via `_conflict_detail` (e.g.
`Email '<x>' is already taken`), and `BulkCreateError` gained `username`/`email`
so the client can show which row failed. `UserBulkCreateResult.created` stays a
count.

---

## 2026-06-03 — Plan 048: Transactional email — welcome set-password invite & password reset, via a durable outbox

### What
Added the platform's first email capability. Creating a user (`POST /users`,
`POST /users/bulk`, `POST /auth/register`) now sends a **welcome set-password
invite**, and a new **forgot-password → reset-password** flow lets users recover
accounts. Both flows share one single-use Redis token type and one
`POST /auth/reset-password` endpoint. All email is delivered through a durable
**`email_outbox` table** drained by an in-process worker started in the FastAPI
lifespan. New modules: `app/core/{redis,email,email_worker}.py`,
`app/services/{email_outbox_service,password_token_service,email_service}.py`,
`app/models/email_outbox.py`, `app/schemas/auth.py`, `app/templates/email/*`,
migration `a1c2e3f40576`. Deps: `aiosmtplib`, `jinja2`.

### Key choices
- **Durable outbox over per-request `BackgroundTasks`.** Bulk-create of ~100
  users would otherwise open ~100 SMTP connections (serial = slow + non-durable
  across restarts; concurrent = Gmail `421` throttling). Rows are written in the
  *same transaction* as the user, so an email exists iff the user commits. The
  worker reuses one paced connection and retries with exponential backoff. This
  subsumes the old "delivery log / retry" tech-debt idea.
- **In-process drain loop, not Celery.** No extra process; state lives in
  Postgres so it's restart-safe, and `FOR UPDATE SKIP LOCKED` + a `sending`
  state make it correct across multiple uvicorn workers / replicas (no
  double-send). Celery is the documented scale-out path (tech debt).
- **Routers got thinner, not fatter.** Because enqueue is a DB write inside the
  existing transaction, the invite is wired into `user_service.create_user` /
  `bulk_create_users` (which `auth.register` and the admin routes already call) —
  no `BackgroundTasks`, better alignment with "one service call / no logic in
  routers" than the originally-sketched per-router enqueue.
- **One token, one endpoint for both flows.** Welcome invite and reset are the
  same "prove you own the email, then set a password" operation; only TTL (24h
  vs 1h) and copy differ. `reset-password` consumes either.
- **Redis for tokens, Postgres for the message queue.** Tokens want native TTL +
  atomic single-use (`GETDEL`); messages want durability + retry/visibility.
  `app/core/redis.py` is the first real async Redis client — the planned
  logout-blocklist work can reuse it.
- **SMTP + App Password, not the Gmail API.** Simpler; OAuth2/Gmail-API transport
  deferred. **`EMAIL_ENABLED=false` by default** so dev/test still queue + drain
  rows but the send is a logged no-op (never hits Gmail).
- **No user enumeration.** `forgot-password` always `202`; reset/validate return
  generic `400`. Weak password (<8 chars) → `422` *before* the token is consumed.
- **Password optional on create → unusable random hash.** Keeps the column
  `NOT NULL` and `verify_password` total; the random hash can never match a login.

### Deviation from the plan
- Welcome-invite enqueue lives in **`user_service`** (not the routers, as one
  line of the plan's change-table suggested). The plan's own "routers get
  thinner" decision and the no-logic-in-routers invariant both point to the
  service; putting it in `create_user`/`bulk_create_users` also avoids
  duplicating it across the three creation paths.
- Tests use a tiny hand-rolled pure-async `FakeRedis` in `conftest.py` instead of
  the `fakeredis` package: `fakeredis.aioredis` runs commands off the traced
  frame, which made `coverage` under-report every line after an `await` on it
  (the new services dropped to ~65% despite passing assertions on post-await
  return values). The pure-async fake restores accurate tracing (services now
  100%) and drops a third-party dependency.

### Tech debt added
- Rate limiting / captcha on `forgot-password` before heavy public use.
- Celery-based drain worker for true horizontal scale-out.
- Transactional provider (SES/SendGrid) above Gmail's daily caps.
- Outbox dead-letter replay endpoint / admin UI (manual `UPDATE` for now).

---

## 2026-06-01 — Plan 047: Move the edge reverse proxy from Docker to host-level nginx; each app owns its own vhost

### What
Retired the dockerized edge proxy that lived in the **frontend** repo (`web-testoria/proxy/nginx.conf` + an `nginx-proxy`/`certbot` pair that also *created* the shared `testoria-proxy` docker network). The public edge is now **host-level nginx + system certbot**. This repo gained `deploy/api.vhost.conf` (server blocks for `api.*` → `127.0.0.1:8000` and `s3.*` → `127.0.0.1:9000`), `deploy/nginx-maps.conf` (the `$connection_upgrade` map), and `deploy/README.md` (host runbook). `docker-compose.prod.yml` now publishes `api` and `minio` on `127.0.0.1` only and dropped the external `proxy` network — the stack runs entirely on the private `internal` network. `cd.yml` no longer reaches into a `~/testoria` checkout to restart a proxy container; it installs this repo's vhost/maps to `/etc/nginx` and `sudo systemctl reload nginx`.

### Key choices
- **Host nginx, not containerized.** Every pain point of the old setup — the `testoria-proxy` network ownership/bootstrap deadlock, the `resolver 127.0.0.11 valid=10s` hack, and the webroot-certbot `--expand` dance — existed *only because the proxy was a container*. Host nginx talks to `127.0.0.1:<port>` directly and never blocks on a restarting container.
- **Keep apps + infra in Docker.** Only the edge moved to the host; Postgres/Redis/MinIO/Centrifugo/api stay containerized (versioned, healthchecked, reproducible).
- **Loopback-only exposure.** `api` and `minio` publish on `127.0.0.1` so host nginx is the sole public entry point; nothing else is reachable on the public interface.
- **Per-app vhosts + per-app certs.** `api.*`/`s3.*` config and their TLS cert live in this repo; `testoria.*` lives in `web-testoria`. Ownership now matches the repo boundary; api comes up independently of web.
- **`certonly`, repo owns the full vhost.** certbot only issues/renews (system timer + `systemctl reload nginx` deploy hook); the `ssl_certificate` directives live in the repo vhost so redeploys never clobber TLS.
- **MinIO `Host` passthrough preserved** (SigV4 `403` guard); WebSocket upgrade headers added to the api vhost proactively. Exposing Centrifugo through the edge remains a follow-up (no host port yet).

### Tech debt resolved
- Dockerized-proxy coupling: the cross-repo `testoria-proxy` network ownership and the `resolver` startup hack are gone.

### Tech debt added
- **Host config drift / passwordless sudo surface.** Deploys now `sudo install` + `sudo systemctl reload nginx` as the deploy user; the host is the source of truth for what's actually loaded. Mitigated by a scoped `sudoers.d` entry and `nginx -t` before every reload (see `deploy/README.md`).
- **Centrifugo realtime not exposed through the host edge** (carried over): no host port / public server block yet.

---

## 2026-05-11 — Plan 046: `display_order` on test_cases + parent-cycle check on suite re-parent (TES-69)

### What
Added a nullable `display_order: Integer` column to `test_cases` (migration `f0a1b2c3d4e5`), surfaced it on Create / Update / Response Pydantic schemas, and rewired `list_test_cases` to sort by `(display_order NULLS LAST, created_at, id)` via a new `apply_case_order` helper that mirrors `apply_suite_order`. Hardened `update_suite` to reject a re-parent that would make the suite a child of one of its own descendants — reuses the `_descendant_suite_ids` recursive CTE introduced by plan-045 for cascade soft-delete. Unblocks web plan-093 (drag-and-drop reorder).

### Key choices
- **Mirror suites, don't invent a new ordering primitive.** `Integer | None` with `NULLS LAST` matches the suite contract already shipped (plan e9f0a1b2c3d5). Frontend gap-based math (`prev + next) / 2`) only needs monotonic integers; making `NULL` sort last preserves the create / bulk-import flows without a backfill step.
- **No backfill.** Cases that pre-date the migration keep `display_order = NULL` and sort by `(created_at, id)` ascending — the same secondary key both suites and cases share. The first reorder action on a sibling group materialises explicit values for the cases touched by the drop. A blanket backfill would touch every row in a large project for no current benefit.
- **Single-PUT, gap-based reorder, no bulk endpoint.** A bulk `POST /test-cases/reorder` is a perf optimisation, not a correctness gap. Track as tech debt if the FE ends up firing many PUTs in tight succession.
- **Reuse `_descendant_suite_ids` for the cycle check.** Plan-045 already shipped that helper for cascade soft-delete; reusing it keeps the "is this suite under that one" answer in one place — Postgres recursion stays the canonical implementation.
- **Reject cyclic re-parent, don't normalise.** Returning `400` is the right contract; silently dropping the change would leave the client UI inconsistent with the server.
- **`display_order` on `TestCaseResponse`** so the frontend can read the server's authoritative value back after a PUT and stay aligned with the DB.

### Tech debt resolved
None.

### Tech debt added
- **Display-order rebalance helper.** Integer gap-based ordering collapses to `prev + 1` after many bisects on the same gap. Add a service-level routine (`renumber_siblings(parent_suite_id)`) that walks a sibling group and reassigns `display_order` at `REORDER_GAP * index`. Only worth shipping if a real workload starts hitting collisions.

---

## 2026-05-11 — Plan 045: cascade soft-delete across the suite subtree (TES-70)

### What
`DELETE /test-suites/{id}` now soft-deletes the suite **and every descendant suite, plus every TestCase under any suite in that subtree**, in a single transaction. Previously only the suite itself and its direct cases were marked `deleted_at`; child suites and their cases stayed active, invisible in the UI tree but still counted by `GET /projects/{id}/stats.total_test_cases` — the inflate-counter symptom in TES-70.

### Key choices
- **Recursive Postgres CTE** to compute the descendant set in a single round-trip, then two bulk `UPDATE`s (suites, cases). Cleaner than Python-level recursion (no N+1 per tree level) and keeps the transaction snapshot consistent across the whole cascade.
- **Soft-delete only — no FK schema change.** The earlier soft-delete migration (`a1b2c3d4e5f6`) explicitly moved `parent_suite_id` from `ON DELETE CASCADE` to `ON DELETE SET NULL` because hard cascades bypass soft-delete semantics + audit trail. Keeping that intact; the new cascade is service-level only.
- **Skip already-soft-deleted descendants** via `where deleted_at is null` on both bulk updates. A descendant deleted independently earlier keeps its original `deleted_at` timestamp — important for forensic reasoning ("when was this case orphaned?"). Verified by a unit test that compares the preserved timestamp to the original.
- **`restore_suite` intentionally NOT changed.** Symmetry would require a UX call (does restoring the parent restore descendants that were independently deleted before the cascade?). Filed as tech-debt entry rather than guessed.
- **Audit logging deferred.** The original plan called for one `DELETE` audit entry per cascade with cascaded ids in metadata. Implementing it required threading `current_user.id` from the router through `delete_suite` (the existing endpoint passes nothing). Out-of-scope for the bug fix; current behaviour is consistent with the existing endpoint (no audit on suite delete today).
- **No frontend change required.** Verified: `web-testoria/src/views/test-cases/TestCaseListView.vue:259-260` already calls `fetchTestCases()` after a successful delete; the in-memory "Contains X cases" counter is derived from the API response, so once the backend stops returning the orphans the frontend display becomes correct on the next render.

### Tech debt resolved
None.

### Tech debt added
"Restore subtree after suite delete-cascade" — restore is now asymmetric with delete; needs a UX call before implementing.

---

## 2026-05-08 — Plan 044: round all pass-rate ratios at the response boundary

### What
Every `pass_rate` value the API returns is now rounded to 3 decimal places (= 1 decimal of percent) at the response boundary. Pairs with web plan-083 (front-end consolidation onto `formatPassRate` / `toPercentRounded`).

### Key choices
- **Rounding at the response boundary, not at the helper.** The original plan suggested wrapping `stats.pass_rate(passed, total)` to round on return — implemented and immediately exposed the bug it was trying to prevent: `mean([round(1/3), 1.0]) = 0.666` vs `round(mean([1/3, 1.0])) = 0.667`. Reverted; `stats.pass_rate` returns the raw ratio for aggregation, callers wrap in `stats.round_ratio()` when populating a response field.
- **Pydantic `field_serializer` on `TestRunProgress.pass_rate`.** That field is exposed directly on `GET /test-runs/{id}/progress` and inline on the run-list endpoint, but is also reused by `report_service` to compute `overall_pass_rate` (mean of per-completed-run rates). The serializer rounds at JSON output time so the in-memory value stays raw for aggregation. No external caller observes the unrounded form.
- **3 decimals, not 4.** Front-end target is 1 decimal everywhere; storing extra precision on the wire just creates rounding ambiguity in tooltips that re-render at higher precision.
- **Constant lives at one site** — `stats.PASS_RATE_DECIMALS = 3` and `stats.round_ratio()` are the single source of truth. Future precision changes are a one-line edit.
- **Existing tests adjusted** rather than expanded with new `pytest.approx(unrounded, abs=5e-4)` everywhere — direct `== 0.333` / `== 0.667` assertions read better and pin the precision contract explicitly.

### Tech debt
None added. Web plan-083 follows up on the front-end side.

---

## 2026-05-08 — Plan 043: cross-project Reports analytics endpoint

### What
Added `GET /api/v1/reports/analytics` mirroring `/projects/{id}/report-analytics` but aggregated across all (or a caller-supplied subset of) projects, with a `per_project[]` breakdown row list. Pairs with web plan-082 ("All projects" mode for the Reports page).

### Key choices
- **Separate endpoint, not an overload.** Kept the per-project endpoint typed to a single project (no `project_id: int | None` on its response). Mirrors the precedent set by `GET /projects/stats` vs `GET /projects/{id}/stats`.
- **`RunAnalyticsItem.project_id` is now always populated**; `project_name` is populated only by the cross-project endpoint to avoid an extra join in the per-project case (it already knows the project from the URL). Additive change — no consumer breakage.
- **Mean-of-run-rates rule preserved.** `summary.overall_pass_rate` is the arithmetic mean of every completed run's own `pass_rate` across the whole scope (not the mean of per-project means). Per-project breakdown rows separately apply the same rule per project so each row agrees with `/projects/stats`.
- **`include_archived=false` default** matches `/projects/stats`. Unknown ids in `project_ids` are silently dropped (also matches `/projects/stats`).
- **Empty scope returns the documented empty payload** rather than 400 — consistent UI rendering for "no data".
- **Reuses `_aggregate_run_status_counts` and `test_run_service.batch_run_progress`** so per-run rate definitions stay in sync with the run-list / Dashboard surfaces.

### Tech debt
None added. Per-project trend overlay (separate coloured lines per project on one chart) was deferred — the endpoint ships a single aggregated trend; overlay is a future enhancement when product asks.

---

## 2026-04-27 — MinIO added to prod stack; dual-URL pattern for presigned attachments

### Bug context
`POST /test-results/{id}/attachments/bulk` returned every file in `failed` on prod with `Could not connect to the endpoint URL: "http://localhost:9000/..."`. Plan 042 (2026-04-22) introduced object storage but only wired MinIO into `docker-compose.yml` (dev). `docker-compose.prod.yml` had no MinIO service and `.env.prod` set no `S3_*` variables, so the API container hit the `app/config.py` defaults (`http://localhost:9000`) and the boto3 connection failed inside the container.

### Self-host MinIO in the prod compose stack
Chosen over external S3 (AWS / R2 / Scaleway) per user preference. Added a `minio` service to `docker-compose.prod.yml` on both `internal` (so the API can reach it via `http://minio:9000`) and `proxy` (so the external `testoria-proxy` reverse proxy can route a public hostname to it). Named volume `minio_data` mirrors the `postgres_data` / `redis_data` pattern. `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` are required (`:?` operator) so a missing secret fails fast at compose-up rather than silently defaulting to `minioadmin`. The API now `depends_on: minio { condition: service_healthy }`.

### `S3_PUBLIC_ENDPOINT_URL` — dual-URL pattern
Presigned URLs are returned to browsers (`app/schemas/test_result.py:_populate_url`), so they must reference a publicly-reachable hostname — but the API itself should talk to MinIO over the docker network for latency / reliability / not needing public DNS to function. Added `S3_PUBLIC_ENDPOINT_URL` (optional, falls back to `S3_ENDPOINT_URL`). Internal calls (`put_object`, `get_object`, `delete_object`, `ensure_bucket`) use `S3_ENDPOINT_URL`; both `generate_presigned_url` and `generate_presigned_url_sync` swap to `S3_PUBLIC_ENDPOINT_URL`. The sync client is now a per-endpoint `dict[str, BaseClient]` cache rather than a single global, since each endpoint URL needs its own boto3 client.

### `S3_USE_SSL` removed
Was redundant — boto3 infers SSL from the endpoint URL scheme. Keeping a separate flag would have required two settings to express what one URL already says, and become a footgun once `S3_PUBLIC_ENDPOINT_URL` (https) and `S3_ENDPOINT_URL` (http) point at different schemes. Removed from `app/config.py` and `app/core/storage.py:_client_kwargs`. No callers outside `storage.py` referenced it.

### Deployment requirement (left to ops, not a code change)
The reverse proxy on the `testoria-proxy` network must route the `S3_PUBLIC_ENDPOINT_URL` hostname (default placeholder `s3.testoria.gammait.net`) to `minio:9000`. Until that route exists, uploads will succeed (internal path) but presigned URLs returned to browsers won't resolve.

### Tech debt added
- MinIO root credentials are reused as `S3_ACCESS_KEY`/`S3_SECRET_KEY`. Should create a scoped MinIO user limited to the attachments bucket and rotate the API credentials separately.
- No public-route assertion at startup. The API has no way to know whether `S3_PUBLIC_ENDPOINT_URL` is actually reachable from the internet — first browser load is the canary.

### CD pipeline brings up MinIO alongside other infra
`.github/workflows/cd.yml` `up -d postgres redis centrifugo` becomes `up -d postgres redis centrifugo minio`. Without the explicit MinIO start, the API's `depends_on: minio { condition: service_healthy }` would still pull MinIO up at the `up -d api` step — but waiting for the healthcheck would race with the API container start and slow the deploy. Bringing MinIO up in the same batch as the other infra services lets it warm up while migrations run.

---

## 2026-04-22 — MinIO object storage for result attachments + bulk upload + report embedding (plan 042)

### `result_attachments.file_path` renamed to `object_key`; new `storage_backend` discriminator
Prior: attachments were written to local disk under `uploads/{result_id}/{filename}`; the column held a filesystem path. This blocked horizontal scaling, didn't survive container rebuilds unless bind-mounted, and pinned deployment to a single writer. Switched to S3-compatible object storage (MinIO in dev, any S3 provider in prod). The column was renamed to `object_key` — the semantics change from "filesystem path" to "object key `results/{result_id}/{uuid}-{sanitized_filename}`" inside the bucket named by `S3_BUCKET_ATTACHMENTS`. A new `storage_backend` column (NOT NULL, default `'s3'`, server-default backfilled to `'local'` on the migration so existing rows stay resolvable) drives URL resolution; legacy `'local'` rows keep working via the read-only `GET /api/v1/files/legacy/{id}` shim until a one-shot `scripts/migrate_attachments_to_minio.py` run flips them. Revision `c7f1a2b3d4e8`.

### New `POST /test-results/{id}/attachments/bulk` — multi-file upload
Prior: the only attachment endpoint was one-file-per-request, forcing the web client to fan out N HTTP calls for N pasted screenshots. Added a bulk endpoint accepting up to `MAX_ATTACHMENTS_PER_BULK` (default 10) files in one multipart request. Each file must match the `IMAGE_MIME_WHITELIST` (png/jpeg/webp/gif) and pass PIL image validation; files that fail validation or MinIO put are returned in a `failed` array alongside committed rows, so the client can surface partial success without losing the good uploads. The single-file endpoint is retained unchanged (permissive size-only validation) so pre-plan-042 callers keep working.

### `ResultAttachmentResponse.url` — presigned GET URL
Prior: the attachment response carried filename, size, mime, timestamp — but no URL. The frontend was using `file_path` (a server-side filesystem path) as an `<img>` src; images rendered 404 in the browser. Added `url: str`, populated via `model_validator(mode='after')` that calls `storage.generate_presigned_url_sync` (signature-only, no network round-trip, safe in sync contexts). Live responses use TTL `S3_PRESIGN_TTL_SECONDS` (900s default — short for RBAC safety). Report payloads (`RunReportAttachment.url`) use `S3_REPORT_PRESIGN_TTL_SECONDS` (7 days default) so a generated PDF/Excel keeps the URLs usable for a week after download.

### PDF + Excel report embedding — capped at 3 per result
Prior: `report_service.generate_run_report_pdf` / `_excel` dropped `TestResult.attachments` on the floor. Enriched the codepath: `get_run_report` now eager-loads attachments via `selectinload` and passes object keys + filenames to the generators. PDF uses reportlab's `Image` flowable; Excel adds a dedicated "Screenshots" sheet with openpyxl's `drawing.image.Image`. Hard cap `REPORT_ATTACHMENT_MAX_PER_RESULT` (default 3) keeps PDF/XLSX file sizes sane; overflow becomes a "+N more" line. Pillow downsizes to 800px max width before embed. The generator downloads bytes via `storage.get_object` — no browser round-trip — because Excel cannot reference remote image URLs inline.

### `aioboto3` over `minio-py`
Botocore is the industry standard; swapping endpoint URL from MinIO → AWS S3 → any S3-compatible provider is a config change. `minio-py` would lock us to MinIO client semantics.

### Integration test strategy — in-memory stub via conftest autouse fixture
Tests run against the in-memory SQLite DB without a live MinIO; `tests/conftest.py` installs an autouse `_stub_storage` fixture that monkeypatches `app.core.storage.{put_object, get_object, delete_object, generate_presigned_url, generate_presigned_url_sync, ensure_bucket}` against a session-scoped dict. Matches the "no mocks for real database" stance from project guidance — in production MinIO is part of `docker-compose.yml` with a healthcheck, and the MIME/size validators run against real PIL decoding in unit tests.

### Tech debt resolved
- `Synchronous file I/O in attachments — large uploads block event loop`: `aioboto3.put_object` is async by construction.

### Tech debt added
- Local-disk attachments from before plan 042 must be migrated via `scripts/migrate_attachments_to_minio.py --commit` before the legacy shim `GET /files/legacy/{id}` can be removed.
- Orphan-object GC: a nightly task should list `object_key`s that have no `result_attachments` row (or whose row was hard-deleted) and delete them from the bucket.
- Virus scanning (ClamAV) of image uploads before MinIO put.

---

## April 2026 — Automation coverage keyed on `TestCase.type` instead of `automation_id`

### Report `by_automation` now counts `type == 'automated'`
Prior: `report_service.get_report_analytics` counted a case as automated only when `automation_id IS NOT NULL`, which is the CI-linkage id — not the user-facing classification. Any case marked `type='automated'` but without a CI id was reported as manual, and the Reports page's automation-coverage donut showed 100% manual for every project that hadn't wired CI. Switched the predicate to `TestCase.type == 'automated'`, matching the `by_type` distribution already exposed in the same response and the create/update schemas. Regression test `test_report_analytics_automation_coverage_uses_type_flag` pins it.

---

## April 2026 — Pass rate redefined as mean of per-completed-run rates (plan 041)

### `pass_rate` semantic changed in `get_stats`, `get_bulk_stats`, and report summary
Prior: `sum(passed_results) / sum(total_results)` across all completed runs of the project — a volume-weighted ratio. New: the arithmetic mean of each completed run's own pass rate. Runs with zero results contribute nothing. This makes every completed run count equally toward the KPI; one huge failing run no longer swamps many smaller passing ones. Applies to `ProjectStats.pass_rate`, `ProjectStatsItem.pass_rate`, and `ReportAnalyticsSummary.overall_pass_rate`. Per-run `pass_rate` inside a single run (`TestRun.progress`, per-run report endpoint) is unchanged — within one run, `passed/denominator` is still the definition.

### Per-run rate must match `TestRun.progress.pass_rate`
Initial implementation of plan 041 used `passed / result_rows` as the per-run rate — a simpler formula but it diverges from `TestRun.progress.pass_rate`, which uses `passed / max(cases_in_scope, tested)`. That led to the same run showing e.g. 100% on the Reports page (3 results, all passed) but 33% on the Dashboard (3 results of 9 cases in scope). Corrected: both `project_service` and `report_service` now delegate the per-run rate to `test_run_service.batch_run_progress` (promoted from `_batch_run_progress`). Dashboard, Reports KPI, and the run-list endpoint now share one per-run definition.

### Reverted `passed_results` / `total_results` from `ProjectStatsItem` (plan 040)
Those fields were added so the frontend could compute a weighted cross-project overall. Under the new mean-of-rates rule they're unused and actively misleading (a consumer could divide them and get a number that doesn't match `pass_rate`). Removed.

### Integration test added
`test_bulk_stats_pass_rate_is_mean_of_run_rates` seeds two completed runs at 1/1 (100%) and 0/3 (0%). Weighted would be `1/4 = 0.25`; the endpoint now returns `0.5`, pinning the new semantic.

---

## April 2026 — Raw passed/total counts on project bulk stats (plan 040)

### Added `passed_results` and `total_results` to `ProjectStatsItem`
`GET /projects/stats` previously returned only the derived `pass_rate` per project. The web dashboard needs raw counts to compute a correctly weighted overall pass rate across projects — `sum(passed) / sum(total)` — rather than an equal-weight mean of percentages. Rather than change the `pass_rate` semantic or add a new aggregation endpoint, we expose the two counts that `project_service.get_bulk_stats` already computes internally (`passed_results_by_project` / `total_results_by_project`). Same completed-run filter as `pass_rate`, so `pass_rate == passed_results / total_results` where both sides are defined. No change to the single-project `ProjectStats` — the dashboard-overall concern only exists when aggregating across projects.

---

## April 2026 — Test run lifecycle (`planned → active → completed`) and completed-only statistics (plan 039)

### Renamed `in_progress` → `active`
The status value `in_progress` has been renamed to `active` both in the DB (Alembic revision `a4f9c1d27e53` rewrites existing rows) and in the `TestRunStatus` Literal. `in_progress` is still accepted on `TestRunUpdate.status` input as a compat alias and is normalised to `active` before persist; the alias will be removed after web-testoria plan-070 rolls out (tracked in tech-debt).

### Auto-transition `planned → active` on first meaningful result write
`test_result_service.submit()` and `update_result()` now call `test_run_service.transition_to_active(run_id)` after a write that actually changes `status` or `comment`. The helper is idempotent and guarded by `status='planned'` via a single-row `UPDATE`, so concurrent submissions collapse to one transition and no duplicate Centrifugo event / audit row is emitted.

### Decision: no auto-complete
The lifecycle only auto-transitions into `active`. Reaching `completed` still requires an explicit `POST /test-runs/{id}/close`. Rationale: completion has a semantic meaning ("I confirm this is done"); automating it would conflate "last case marked" with "work signed off". Users also asked for this distinction.

### Pass-rate counts only completed runs
`report_service.get_dashboard()`, `report_service.get_report_analytics()` (summary + trend), `project_service.get_stats()`, and `project_service.get_bulk_stats()` all now filter result aggregates by `TestRun.status == 'completed'`. Before: a project with one in-flight run showing one passed case would display "100% pass rate" — misleading. After: projects with no completed runs show `pass_rate=null` (or `0.0` on dashboard, per the existing `or 0.0` fallback) and `result_distribution={}`. The per-run counts in `DashboardResponse.recent_runs[]` and `ReportAnalyticsResponse.runs[]` remain unfiltered so the "work in flight" view is preserved; `active_runs` still counts `planned + active`.

### Auto-transition lives in the service layer
Alternative considered: a DB trigger. Rejected because triggers hide behaviour from the Python layer, make integration tests harder to write, and create a second source of truth for the lifecycle rule. Keeping the transition inside the service means it runs in the same async transaction as the result write, observers see both atomically, and the logic is testable with the same unit-test style as the rest of the service layer.

---

## April 2026 — `progress` populated by default on list runs

### Reverted plan 036's opt-in
Every consumer of `GET /projects/{id}/test-runs` the web dashboard knows about needs `progress` (pass-rate trend, distribution, list view with per-row counts). Keeping it opt-in meant every call site had to remember `?include=progress` or silently render blank. Flipped to always-on; the batched computation already made it cheap.

### `?include=progress` param removed
One opt-in value wasn't earning its keep. If a future caller actually needs the lighter response, we'll re-introduce a `?include=minimal` or similar; deferring until someone asks.

### Single-item `GET /test-runs/{id}` still omits progress
The single-item read stays cheap. Callers who need it continue to use `GET /test-runs/{id}/progress`.

---

## April 2026 — Case set locked on completed runs

### `PUT /test-runs/{id}/cases` returns 409 when run is `completed`
Once a run is closed (`status == "completed"`), its case set is a historical record. Editing it would retroactively change what the run covered. The service now raises `ConflictError` (HTTP 409) and leaves the junction table untouched. Only `completed` is locked; `planned`, `in_progress`, and `aborted` remain mutable for now.

### No role exemption
Admin / lead can still edit metadata via `PUT /test-runs/{id}` or reopen the run by flipping `status` back to `in_progress` via the same endpoint — then the case set becomes editable again. Keeps the rule simple: terminal state ⇒ frozen cases.

---

## April 2026 — `/test-runs/{id}/progress` scoped to current case-set

### Counts restricted to in-scope cases
`get_progress` and `_batch_run_progress` now filter status counts by the run's current scope (junction for explicit, project/suite-derived for auto). Previously a run could report `passed > total` when a case with a passed result was removed from the run's scope.

### Batch path still ≤ 3 round-trips per mode
For paginated list-runs with `?include=progress`: one junction-count + one scoped status-count for explicit runs, plus two queries per auto run (total count + status count). Still bounded by page_size.

---

## April 2026 — `/test-runs/{id}/results` scoped to current case-set

### Default hides orphan results
`GET /test-runs/{id}/results` now filters to results whose `test_case_id` is in the run's current scope (same rules as `/cases` — junction rows for explicit mode, project/suite-derived for auto). Results for cases that were removed from an explicit selection, soft-deleted, or moved out of the auto suite are no longer returned.

### `?include_orphans=true` escape hatch
Audit tools (and anyone debugging "why does `/results` return more rows than `/cases`") can opt back into the old full-list behaviour. Keeps the write-path intact — nothing is deleted.

### Why filter instead of delete
The `TestResult → TestCase` FK is `ON DELETE RESTRICT`. Cases leave scope by soft-delete or junction-row removal, both of which leave the `TestResult` rows intact. Filtering at read-time matches user expectations without losing the audit trail.

---

## April 2026 — Folded `untested` into `no_run` on run progress

### One bucket for "not yet executed"
`TestRunProgress` and `RunReportResponse` used to carry both `no_run` (explicit result with that status) and `untested` (no result row at all). Two fields for what the UI always treats as one concept. Collapsed both into `no_run`; dropped the `untested` field from the schemas and the service computations.

### Denominator unchanged
`pass_rate` still uses `max(total, tested)` so orphan results can't push the ratio above 1. The numeric result is identical; only the field breakdown changed.

### Breaking shape change
Response JSON no longer includes `untested`. Clients that read it see a KeyError. Web/CLI plans consuming these endpoints need to swap to `no_run`.

---

## April 2026 — Plan 038: `ResultHistory` recorded only on meaningful change

### Aligned `submit()` and `update_result()`
Both paths now gate `_record_history` via `_should_record_history(created, old_status, old_comment, new_status, new_comment)`. A repeated submit with the same verdict + same comment adds no new history row. Previously `submit()` wrote unconditionally.

### Comment changes are history events
`update_result()` used to record only on `status` change; now also records on `comment` change. A tester correcting a comment after a verdict is reviewer-relevant; the audit trail should show it.

### `None` and `""` comments compared as equal
`(old or None) != (new or None)` avoids spurious rows when the client toggles between `null` and an empty string.

### No backfill of existing redundant rows
Legacy rows stay. Write-path is fixed going forward. Web plan 063 handles timeline rendering for both fresh and legacy data.

### `step_results` diff deferred
Recording `step_results` changes as history events needs a concise serialisation (and diff semantics). Deferred to a follow-up plan; logged in tech debt.

---

## April 2026 — Plan 037: Stable suite sort order

### Added `display_order` (nullable) on `test_suites`
Single nullable int. Existing rows stay `NULL` — no backfill. Explicit values from a future drag-and-drop UI win over the implicit created-time fallback.

### Unified sort key: `(display_order NULLS LAST, created_at, id)`
One helper, `apply_suite_order`, appends the three-key order clause. Every suite query uses it — `list_suites` today, and any future tree projection. Postgres-only (`.nulls_last()`); dependency documented.

### No backfill, no default
`display_order=0` is treated as explicit (sorts before nulls). Default remains `NULL` so unordered rows keep today's created-time behaviour.

### Reorder UI deferred
No `PATCH /test-suites/{id}/reorder` endpoint yet. Schema is ready for it — logged as tech debt.

---

## April 2026 — Plan 036: Opt-in progress on `GET /test-runs`

### `?include=progress` is the only opt-in form
`include` accepts exactly `"progress"` (Literal, rejected as 422 otherwise). Keeps the shape signal clear, leaves room for `include=cases` later without renaming. Default response is unchanged — `progress` is `null`.

### Batched computation, not N+1
Progress is computed with three grouped queries regardless of page size: status counts across all runs, junction-row counts for explicit runs, and a per-auto-run case-set count loop bounded by page size. Avoids issuing one `/progress`-style query per run.

### Shared helper with `/progress`
Per-run `pass_rate` comes from `app/utils/stats.pass_rate` (plan 035); denominator includes every status. Embedded progress and `/test-runs/{id}/progress` always agree.

### No dedicated dashboard aggregation endpoint
Web plan 060 uses this list response directly to compute the "Pass Rate Trend" and "Test Results Distribution" charts client-side. If latency later matters, a dedicated trend endpoint is a separate plan.

---

## April 2026 — Plan 035: Unified `pass_rate` (0..1 ratio over all statuses)

### Ratio on the wire, percent in the UI
Every `pass_rate` the API returns is now a `float | None` in `[0, 1]`. Endpoints that previously returned `0..100` (`/dashboard`, CI badge internals, report-analytics overall) now return the ratio. Pydantic validators (`ge=0, le=1`) enforce the range on every schema field.

### Denominator includes every status
`pass_rate = passed / total`, where `total` counts rows in every status (passed, failed, blocked, no_run). Previously `TestRunProgress.pass_rate` divided by `tested` (excluding `no_run`); a 1-passed / 9-no_run run now reads as 10%, not 100% — closer to what users actually want.

### One helper, zero duplicate math
`app/utils/stats.py::pass_rate` is the only place the division lives. Every service calls it. Grep `pass_rate =` after the refactor — every remaining line is a helper call.

### Behavioural break, no compat field
The field name and type don't change, but the *value* does for existing runs. No `pass_rate_ratio` alongside `pass_rate` — compat fields ossify mess. Web plan 058 ships alongside; CLI consumers and external dashboards should re-render immediately.

### CI badge keeps the same visual thresholds
`ci_service.generate_badge` computes the ratio internally, then multiplies by 100 *only* for the label ("82%") and the color threshold comparison. Badge SVG is byte-identical to before.

---

## April 2026 — Plan 034: Suite-tree grouping for run cases

### One endpoint, two projections via `?group_by=suite`
Rather than introducing a `/test-runs/{id}/tree` URL, the existing `GET /test-runs/{id}/cases` endpoint returns the nested `TestRunSuiteTree` when `group_by=suite`. Same data, different shape. Keeps the URL space clean and signals the relationship.

### Per-node progress, no recursive rollup
Each node's `progress` counts *only its own cases*; the backend does not sum across descendants. Recursive rollup is a UI concern (collapse state, filters change denominators), so letting the frontend compute it avoids two sources of truth.

### Suites with zero run-cases are omitted
The run defines the case-set. A suite contributing no cases to the run is not in the tree. Makes the web rendering logic straightforward (no empty branches) and matches web plan 056's expectations.

### Cases with no result show `result: null`, not a synthetic `no_run` row
Matches plan 033's rationale. The service does not invent `TestResult` rows for unexecuted cases; the frontend synthesises the `no_run` badge.

### Response-model union on the cases endpoint
The router declares `TestRunWithCases | TestRunSuiteTree` as the response model. OpenAPI describes both; clients read `group_by` to decide which shape to expect.

---

## April 2026 — Plan 034: Empty test run + `cases_mode` enum

### Enum column, not boolean
`test_runs.cases_mode` is `VARCHAR(20)` constrained to `auto` \| `explicit` with a DB-level check constraint. A boolean would work today; the enum leaves room for future modes (`tag_filter`, `saved_query`) without another migration.

### Mode inferred from `include_test_cases`, not a new request field
Create with `include_test_cases=None` → `cases_mode="auto"`. Create with `include_test_cases=[]` (or a non-empty list) → `cases_mode="explicit"`. Adding `cases_mode` to the request payload would be redundant — the list already declares intent.

### `PUT /test-runs/{id}/cases` always flips to `explicit`
Calling the endpoint is a user declaration "I'm choosing cases manually", regardless of whether the list is empty. An auto run becomes explicit after one PUT; no way back today (logged as tech debt — "reset to auto" affordance).

### Service branches on the column, not on junction-row count
Dropped `_has_explicit_cases`. Both `get_progress` and `get_with_cases` now read `run.cases_mode` once. This makes "explicit empty" (new state) distinguishable from "auto, zero derived cases".

### Migration backfills from the junction table
Existing runs with ≥1 row in `test_run_test_cases` become `explicit`; the rest stay `auto`. Matches historical behaviour exactly — no user-visible change on existing data.

### Additive response field
`TestRunResponse.cases_mode` is additive; clients ignoring unknown fields are unaffected. Flagged here so anyone pinning strict schemas knows to update.

---

## April 2026 — Plan 033: `GET /test-runs/{id}/cases` completeness

### Offset-based pagination, explicit `total`
Replaced the silent `.limit(500)` truncation with explicit `limit` (default 500, max 2000) and `offset` query params, plus a `total` field in the response. The UI now knows whether it's showing the full set.

### Default sort `(suite_id, id)`; allow-list on `?sort=`
Interleaving suites by `TestCase.id` made multi-suite runs render noisily. Default is now `(suite_id, id)`; callers can choose from `id` \| `title` \| `priority` \| `suite`. Invalid values return 422 (FastAPI Literal rejection) rather than 400 because the validation happens at the router layer.

### `case_status` emitted alongside `status` (deprecation window)
`TestCaseWithResult.status` is the case's *own* workflow state, but the same payload also carries `result.status` — the collision was a repeated source of confusion. Added `case_status` with the same value; kept `status` for one release. Web plan 055 reads `case_status`; both fields are populated on every response during the window. Removal is tracked in tech debt.

### `automation_id` surfaced on every case row
The case model gained `automation_id` in plan 029, but the run-cases endpoint wasn't serialising it. Added the field to `TestCaseWithResult` so the detail page can link manual cases to their automation without a second round-trip.

### No backend synthesis of `no_run` rows for unexecuted cases
`cases[i].result` stays `null` when a case has never been run. The web plan 055 rationale is to let the frontend synthesise the `no_run` badge client-side — inventing placeholder `TestResult` rows in the service would be the backend lying about what exists in the database.

---

## April 2026 — Plan 032: Rename `skipped` status → `no_run`

### Renamed for clarity vs. `untested`
`TestResult.status = "skipped"` was ambiguous against the derived `untested` bucket (case with no result row). The rename makes intent explicit: `no_run` means "the tester chose not to run this case". `untested` keeps its distinct meaning (no result exists at all).

### Compat window, not a hard break
The Pydantic Literal still accepts `"skipped"` alongside `"no_run"`. The `test_result_service._normalise_status` helper normalises `"skipped"` to `"no_run"` before persist, so every row stored post-migration uses the new value regardless of what was submitted. Follow-up plan will remove the compat shim once stale clients are confirmed gone.

### `no_run` is the default when status is omitted
`TestResultCreate.status` defaulted from required to `"no_run"`. Submitting a result with `{test_case_id: N}` (no status) now lands as `no_run` instead of 422.

### JUnit `<skipped>` → `no_run`
The JUnit XML tag stays `<skipped>` (industry convention outside our control); our import maps it to `status="no_run"`.

### Breaking: JSON-shape change on report responses
`RunProgress.skipped`, `RunSummary.skipped`, `RunReportResponse.skipped`, `MetricsDataPoint.skipped`, `RunAnalyticsItem.skipped`, `TrendPoint.skipped` all renamed to `no_run`. Web frontend plan 054 lands in the same release window; CLI consumers (if any) must update.

### DB column stays `String`, not an enum
`test_results.status` is a plain `String` column, so no PG enum alteration needed — only a data-value rewrite. Converting to a real enum type is tracked as tech debt.

### `step_results` JSON migrated row-by-row in Python
The JSON migration (`c5d7e9f1a2b3`) iterates each row, rewrites any `{"status": "skipped"}` entry to `{"status": "no_run"}`, and writes it back. Avoids brittle JSON-path SQL; fast enough given current data volume.

### CI bulk-import response field `skipped` kept as-is
`POST /ci/results/bulk` still returns `{submitted, skipped}` where `skipped` is the count of *unmatched* test cases — not a result status. Renaming it would break external CI consumers for no semantic gain.

---

## April 2026 — Plan 028: Bulk Project Stats Endpoint

### One query per dimension, not per project
`GET /projects/stats` returns per-project counts (cases, suites, runs, active runs) and pass rate for every active project in the workspace using four grouped SQL queries — suite count, case count, run-status distribution, and result-status distribution — keyed by `project_id`. Total queries stay constant regardless of how many projects exist, replacing the frontend's fan-out that used to fetch every run and every case just to compute a headline pass rate.

### Route declared before `/{project_id}` to avoid path shadowing
FastAPI uses first-match routing. `GET /projects/stats` is registered before `GET /projects/{project_id}` in `app/api/v1/projects.py`; otherwise "stats" would be parsed as a project id and the caller would get a 422.

### Same pass-rate formula as `get_stats()`
`passed_results / total_results`, with `pass_rate = None` when `total_results == 0`. Bulk and single-project outputs must be byte-identical per project — verified by an explicit parity regression test.

### Query params mirror existing conventions
`include_archived` (bool) matches `GET /projects` list semantics. `project_ids` is an optional repeated-value query param, serialized by the frontend with Axios `paramsSerializer: { indexes: null }` (same pattern used by the test case tag filter).

---

## April 2026 — Plan 027: Reports & Analytics Aggregated Endpoint

### One endpoint, not parallel small ones
`GET /projects/{id}/report-analytics` returns everything the frontend Reports & Analytics page needs — summary, runs with status counts, test-case distribution, and daily trend — in one round-trip. The previous UI looped over each run and called `/test-runs/{id}/results` once per run; the new endpoint is bounded in response size and issues 3–4 grouped SQL queries end-to-end.

### Grouped SQL over ORM loops
Run-level counts come from a single `SELECT test_run_id, status, COUNT(*) ... GROUP BY test_run_id, status` keyed by run id. Trend is a single `GROUP BY date(tested_at)`. Extracted as `_aggregate_run_status_counts(db, run_ids)` and reused by `get_dashboard()`, which had its own N+1 (one query per recent run).

### Summary counts are project-wide; runs/trend honor the window
`date_from`/`date_to` filter which runs appear in `runs` and which days contribute to `trend`, but `summary.total_runs`, `overall_pass_rate`, and `result_distribution` stay project-wide so the totals widget is stable regardless of the chosen window.

### No stored aggregate columns on `test_runs`
Computing counts at read time is cheap with the existing `test_results.test_run_id` index. Denormalized aggregates would drift on every result insert/update and add write-path complexity.

### Zero-fill trend when both bounds are set
If the caller supplies both `date_from` and `date_to`, the trend array emits one point per day in the window, with zeros for days without activity, so charts render a continuous X axis without client-side padding.

### `completed_at` with `created_at` fallback for run filtering
Matches the existing frontend logic: planned runs that are not yet completed fall back to `created_at` so they still appear in the window.

---

## April 2026 — Plan 026: Per-Step Status on Test Results

### JSON column, not a relational table
`test_case.steps` is already stored as JSON. Steps are intrinsically ordered and limited (~20 per case). A relational table would add two FKs and migration cost for no query-time benefit.

### Index-based identification, not step_id
The test case `steps` JSON has no stable ids — steps are a list, identified positionally. Matching the existing shape avoids a parallel id scheme.

### Partial coverage allowed
A tester might mark only the failing step. The schema accepts fewer `step_results` than the case has steps. Missing indices mean "not reported" (distinct from `skipped`).

### Overall status stays manually set
We do not auto-derive `TestResult.status` from `step_results`. The tester is the authority on overall outcome. Auto-derivation would create surprising writes.

### Null vs empty list
`None` = tester didn't use per-step mode. `[]` = tester opened per-step UI but marked nothing. Different semantics, both honored. Default is `None`.

### Out-of-range and duplicate indices rejected
A client submitting `index: 99` on a 5-step case is a bug, not a no-op. Duplicate indices in the same payload are also rejected with 400.

---

## April 2026 — Plan 025: Test Run Explicit Case Selection

### Many-to-many over a run_id column on test_case
A test case participates in many runs over its lifetime — this is intrinsically M2M. A FK column would force one-run-per-case which contradicts the domain.

### Fallback semantics on read
If a run has no rows in `test_run_test_cases`, reads (progress, cases) fall back to the legacy `suite_id` scoping. This keeps every existing run working without a data backfill.

### Missing vs empty `include_test_cases`
`null` (omitted) means "legacy mode, scope by suite_id"; `[]` (empty list) means "explicit empty selection — no cases". Different semantics, both honored.

### Project-scoped validation
Every case id in `include_test_cases` must belong to a suite in the run's project. Cross-project ids are rejected with 400 to prevent data leakage.

### PUT not PATCH for /cases
The operation replaces the entire case set — PUT is the honest verb. Incremental add/remove can come later.

---

## April 2026 — Plan 024: Test Case `automation_id` Field

### Nullable, not unique
`automation_id` is nullable and non-unique. Multiple test cases may legitimately reference the same automation id during refactors or migration periods. The column is indexed for fast lookups from CI reports.

### Empty string coerced to null
A Pydantic `field_validator` on `TestCaseCreate` and `TestCaseUpdate` converts `""` → `None`. This lets the frontend clear the field by sending an empty string, matching how other optional text fields work.

### Decoupled from `type` field
Setting `automation_id` does not auto-change `type` to `'automated'`. The fields are orthogonal: a `manual` test case can track its in-progress automation id, and an `automated` case can exist without one.

### Exact-match filter only
`GET /test-cases?automation_id=...` is exact match, not substring. The use case is "find the case matching this CI report id", which is a precise lookup. Substring search can be added later if needed.

---

## April 2026 — Plan 023: Tags CRUD + Test Case Tag Filter

### Idempotent POST /tags
`POST /tags` returns 200 with the existing tag if a normalized-equal name already exists, rather than 409 or 500. This avoids race conditions from the frontend autocomplete UI submitting the same name concurrently.

### Prefix search, not substring
`GET /tags?q=foo` does `ILIKE 'foo%'` (prefix match), not `'%foo%'` (substring). Prefix is faster on btree indexes and matches the autocomplete UX. Documented — frontend should not expect substring.

### tag_ids filter uses OR semantics
`GET /test-cases?tag_ids=1&tag_ids=2` returns cases that have *any* of the given tags. AND semantics ("has all") adds query complexity and is uncommon in test management UIs. If needed later, a new `tag_mode=all` parameter can be added.

### Tags remain global (not project-scoped)
The existing `tags` table has no `project_id` column. Adding project-scoped tags would require a migration and breaking changes to the tag resolution logic. Deferred — logged as tech debt if product confirms interest.

### Tag names normalized to lowercase on write
Instead of adding a functional `lower(name)` index (which would require an Alembic migration), tag names are normalized to lowercase and trimmed in `tag_service` on every write. The existing unique index on `tags.name` handles uniqueness enforcement.

### Extracted tag_service from test_case_service
The inline `_resolve_tags` helper in `test_case_service` was replaced with a delegation to `tag_service.get_or_create_many`. This makes tag operations available to the new tags router while keeping `test_case_service` focused on test case logic.

---

## April 2026 — Plan 012: Advanced Features (RBAC, Audit Log, Import/Export)

### Permission enum maps to existing UserRole values
The plan described roles `admin/project_manager/tester/viewer`; the codebase uses `admin/lead/tester/read_only/no_access`. ROLE_PERMISSIONS was defined using the actual UserRole enum to avoid breaking changes.

### Audit log uses VARCHAR(45) for ip_address, not INET
INET is PostgreSQL-specific. Using `VARCHAR(45)` keeps the model SQLite-compatible (required for in-process integration test runs without a PostgreSQL server).

### Audit log called from service layer, not middleware
Audit entries are written inside service methods (project/test_case/test_run create/update/delete). Login/logout audit is in the auth router because auth has no service layer. This avoids complexity of middleware-based audit while keeping the logic close to the business operation.

### user_id added as optional parameter to write service methods
`create/update/delete` on Project, TestCase, and TestRun now accept `user_id: int | None = None`. This keeps import_service (which creates test cases without a user context) working without changes, while enabling audit attribution for router-initiated mutations.

---

## January 2026

### Chose FastAPI as backend framework
See ADR-001. Async-first, auto-generates OpenAPI, Pydantic-native. Key factor: async I/O for PostgreSQL + Redis + future WebSocket support.

### Chose SQLAlchemy 2.0 async + asyncpg
See ADR-002. Industry standard for async Python ORM. asyncpg is the fastest PostgreSQL async driver. Alembic for migrations.

### Thin router / service layer separation
Business logic lives exclusively in `app/services/`. Routers are 3–5 line HTTP handlers. This separation makes service logic testable without HTTP context and prevents business rules from leaking into HTTP handling.

### Pydantic response schemas — never expose ORM models
SQLAlchemy models are never returned from routers. Pydantic schemas control exactly what fields are serialized. This prevents accidental exposure of `hashed_password` and other sensitive fields.

### expire_on_commit=False on AsyncSessionLocal
Prevents greenlet errors when accessing model attributes after a session commit in async context. Trade-off: attributes may be stale if the DB is modified concurrently — acceptable for this use case.

### get_db auto-commit pattern
The `get_db` FastAPI dependency commits on successful request completion and rolls back on any exception. Services never call `db.commit()` directly — they call `db.flush()` to make IDs available within the transaction. This ensures commits happen exactly once per request.

### Upsert semantics for TestResult
`(test_run_id, test_case_id)` has a UNIQUE constraint. Submitting a result for the same (run, case) pair updates the existing row rather than creating a new one. This allows testers to re-submit results without managing result IDs.

### JSONB for steps, config, defects
Test case steps, test run config, and result defects are stored as JSONB. This avoids a join table for steps (which are ordered and always fetched with the case) and allows flexible config keys without schema changes.

### Celery + Redis for async tasks
Long-running operations (PDF/Excel report generation, bulk imports) are offloaded to Celery workers. This keeps API response times fast. Redis is the broker (same Redis instance as caching, separate DB index).

---

### bcrypt directly instead of passlib (Plan 001)
`passlib 1.7.4` is incompatible with `bcrypt >= 4.0` (removed `__about__` attribute, changed password-length validation). Replaced `passlib.context.CryptContext` with direct `bcrypt.hashpw` / `bcrypt.checkpw` calls in `app/core/security.py`. Requirements updated: `passlib[bcrypt]` removed, `bcrypt==5.0.0` added.

---

## 2026-03-24

### API contract gap correction (Phase 3 Amendment)
Four gaps found between backend implementation and frontend API schema: missing `message`/`stack_trace` fields on TestResult, missing `result_history` table/endpoint, missing `GET /test-runs/{id}/cases` endpoint, missing `DELETE /test-results/{id}/attachments/{id}` endpoint. Documented in `docs/04-execution/exec-plans/active/plan-phase3-amendment.md`. Must be closed before frontend integration testing.

### Docker Compose dev environment (Plan 001-1)
`docker-compose.yml` (dev) and `docker-compose.test.yml` (test) added. Dev stack runs postgres:16-alpine on 5432 and redis:7-alpine on 6379 with named volumes. Test stack runs postgres_test on 5433 and redis_test on 6380 without volumes so each test run starts clean. `TEST_DATABASE_URL` in `.env.example` updated from port 5432 to 5433. `scripts/wait-for-db.sh` added for CI. No app `Dockerfile` — FastAPI runs directly in the local venv for dev, keeping the dev loop fast. Alembic `upgrade head` and `downgrade -1` verified against the Dockerised Postgres on port 5433. Integration tests connect to Postgres correctly but fail due to a pre-existing asyncpg greenlet conflict in the `test_user` session fixture — tracked as tech debt, separate from this plan.

## 2026-04-01

### Defect Tracking Integration implemented (Plan 011)

Phase 7: Jira, GitHub Issues, and GitLab Issues creation directly from a failed TestResult.

**Key decisions:**
- Credentials are passed per-request in the request body — no server-side token storage or OAuth flow. This keeps the implementation stateless and avoids secrets management complexity.
- `unittest.mock.patch` with `AsyncMock` used to mock httpx calls in integration tests — no `respx` dependency added (not in requirements).
- External tracker HTTP errors are translated at the service layer: 401 → 422 "Invalid tracker credentials", 404 → 422 "Project/repo not found in tracker", network errors → 502 Bad Gateway.
- Defect references are appended to `test_results.defects` (existing JSONB column) — no new DB table or Alembic migration needed.
- `DefectService` uses module-level helper functions (`_get_result`, `_append_defect`, `_handle_tracker_error`) to avoid code duplication across three tracker methods.

**Files added:** `app/schemas/defect.py`, `app/services/defect_service.py`, `app/api/v1/defects.py`, `tests/integration/test_defects_api.py`
**Files modified:** `app/main.py`, `docs/06-generated/endpoints.md`, `docs/08-decisions/changelog.md`

---

## 2026-03-26

### CI/CD Integration implemented (Plan 010)

Phase 6: GitHub Actions CI/CD pipelines and CI-facing API endpoints.

**Key decisions:**
- CI workflow mirrors the frontend structure: `check` job (lint + type-check + unit tests) followed by `integration` job (real Postgres + Redis via Docker services).
- CD workflow triggers on CI success on `main`. Builds Docker image → pushes to GHCR → deploys to EC2 via SSH → runs Alembic migrations in a separate container before restarting the API container.
- Dockerfile uses `python:3.11-slim` with uvicorn entrypoint.
- JUnit XML bulk import reuses existing `TestResultService.submit` for upsert logic — no new DB models or migrations needed.
- Badge endpoint is public (no auth) so it can be embedded in README files. Returns `Cache-Control: no-cache`.
- Webhook endpoint is a minimal extensibility hook — logs the payload and returns 200. Signature verification deferred.
- `BadRequestError` (400) used for malformed XML instead of 422 to distinguish from Pydantic validation errors.

**Files added:** `.github/workflows/ci.yml`, `.github/workflows/cd.yml`, `Dockerfile`, `app/services/ci_service.py`, `app/api/v1/ci_integration.py`, `tests/integration/test_ci_api.py`, `docs/01-product/features/009-ci-cd-integration.md`
**Files modified:** `app/main.py`, `docs/06-generated/endpoints.md`, `docs/08-decisions/changelog.md`

---

## 2026-03-25

### Reporting & Analytics implemented (Plan 009)

Phase 5 reporting layer: dashboard metrics, run-level reports with PDF/Excel export, time-series metrics, and custom filtered reports.

**Key decisions:**
- ReportLab chosen over WeasyPrint for PDF generation — pure Python, no system-level dependencies (libpango, libcairo). Added `reportlab==4.2.5` to `requirements.txt`.
- PDF/Excel generation is synchronous (in-request) for now. Celery async generation deferred — `app/tasks/report_tasks.py` is a stub with TODO comments.
- `pass_rate` in dashboard is a percentage (0-100), while `pass_rate` in run report is a ratio (0-1) — matches the existing patterns in `ProjectStats` and `TestRunProgress` respectively.
- `GET /test-runs/{id}/report` uses `response_model=None` because the return type is a union (`RunReportResponse | Response`) depending on the `format` query param.
- Metrics endpoint uses `func.date()` for day-level grouping, which works on both PostgreSQL and SQLite.
- Custom report endpoint uses POST (not GET) because the filter payload is complex (nested lists, optional date ranges).

**Files added:** `app/schemas/report.py`, `app/services/report_service.py`, `app/api/v1/reports.py`, `app/tasks/report_tasks.py`, `tests/integration/test_reports_api.py`
**Files modified:** `app/main.py`, `requirements.txt`, `docs/06-generated/endpoints.md`

---

### Centrifugo WebSocket integration implemented (Plan 008)

Centrifugo v5 integrated as a Docker sidecar for real-time updates. Backend publishes events after state mutations (result submission, run status changes, test case updates) to `project:{id}` and `testrun:{id}` channels via Centrifugo's HTTP API.

**Key decisions:**
- Separate JWT secret (`CENTRIFUGO_TOKEN_SECRET`) for Centrifugo tokens — distinct from API auth tokens.
- Connection and subscription tokens have 5-minute TTL.
- All publish calls are fire-and-forget — Centrifugo outage does not cause API errors. Events are dropped and logged at WARNING level.
- Centrifugo uses Redis DB index 1 (separate from app cache on index 0) to avoid connection saturation.
- Token endpoints require `read_only` or higher role (same as GET endpoints).

**Files added:** `app/core/centrifugo.py`, `app/services/realtime_service.py`, `app/api/v1/websocket.py`, `app/schemas/websocket.py`, `centrifugo/config.json`, `tests/integration/test_websocket_api.py`

**Files modified:** `app/config.py`, `app/main.py`, `.env.example`, `docker-compose.yml`, `app/services/test_result_service.py`, `app/services/test_run_service.py`, `app/services/test_case_service.py`

---

### Initial admin seed script (`scripts/seed.py`)
`scripts/seed.py` added to bootstrap the first admin user after `alembic upgrade head`. Reads credentials from `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars (configured in `.env`). Idempotent — skips creation if any admin user already exists. `ADMIN_PASSWORD` has no default; the script exits with a clear error if unset to prevent silent insecure defaults. `sys.path` patching at the top of the script makes it runnable directly (`python scripts/seed.py`) without needing `PYTHONPATH` to be set.

---

### Execution domain implemented: milestones, test runs, results, attachments, history (Plans 003-007)

All five Phase 3 plans implemented together in one pass:

**Models added:** `Milestone`, `TestRun`, `TestResult` (UNIQUE run+case), `ResultAttachment`, `ResultHistory`

**Schema decisions:**
- `TestResult` status values are lowercase (`passed/failed/blocked/skipped`) for consistency with test_case fields.
- `TestRun.status` values: `planned/in_progress/completed/aborted`.
- `message` and `stack_trace` included from the start (plan 004) to avoid a follow-up migration.
- `result_history` table uses append-only semantics: initial submit always writes, updates write only on status change.
- `TestRun.config` uses `JSON` (same as steps) for SQLite test compatibility.
- `defects` stored as `JSON` list; structure is open — no FK to a defects table (future concern).
- `TestRun.suite_id` uses `ondelete="SET NULL"` (run remains if suite is deleted, scoping just becomes project-wide).
- Attachment file path: `{UPLOAD_DIR}/{result_id}/{filename}`. Missing files on delete are logged and skipped (not a 500).

**Upsert semantics:** `POST /test-runs/{id}/results` creates on first call, updates on second (same `test_case_id`). UNIQUE constraint `uq_result_run_case` enforces this at DB level.

**`GET /test-runs/{id}/cases`:** returns flat list (max 500) with outer-joined current results. No recursive CTE — suite subtree traversal is out of scope.

**`project_service.get_stats`** updated to return real `total_test_runs` and `pass_rate` (passed / total results).

---

### Core test management domain implemented (Plan 002)

**Models added:** `Project`, `TestSuite` (self-referential hierarchy), `TestCase` (steps as JSON/JSONB), `Tag` + `test_case_tags` join table.

**Schema decisions:**
- Tags are global (not per-project) — simplest design that supports the use case; tags are auto-created on write.
- `steps` column uses SQLAlchemy `JSON` type, which maps to JSONB on PostgreSQL and JSON on SQLite (used in tests). The alembic migration creates it as `JSON` natively; the column is functionally JSONB in production.
- `TestCase.priority` / `type` / `status` stored as VARCHAR with Pydantic `Literal` validation at the API layer instead of DB CHECK constraints, keeping the schema simple.
- `tags` relationship uses `lazy="selectin"` on `TestCase` so tags are always loaded without N+1 queries.
- `GET /projects/{id}/test-suites` returns a flat list with `parent_suite_id`; tree construction is left to the client.
- `DELETE /projects/{id}` requires `admin` role (not `lead`) to prevent accidental project deletion.
- `db.refresh(tc)` called after flush in create/update to reload all columns (including `updated_at`) and avoid greenlet errors during Pydantic serialization.
- Import/export are synchronous (in-request) for now; Celery offload is tracked as tech debt for large datasets.

---

### User roles formalised and user management implemented (Plan 001-2)

`UserRole` StrEnum introduced in `app/core/roles.py` with 5 values: `no_access`, `read_only`, `tester`, `lead`, `admin`. Role hierarchy enforced via `ROLE_HIERARCHY` dict. `Lead` is the default role for new users and cannot be deleted (service-layer guard).

**Role rename:** `viewer` → `read_only`, `project_manager` → `lead`. Migration `f7a3b2c1d9e0` runs UPDATE before adding the CHECK constraint to avoid constraint violations on existing rows.

**`no_access` blocking:** enforced in `get_current_user` rather than per-route, so any `no_access` user is blocked at every protected route after login.

**Bulk create:** best-effort (partial failures collected and returned) using SQLAlchemy savepoints (`begin_nested()`) so a failure in one row doesn't corrupt the session.

**Export:** CSV via stdlib `csv` module with `StreamingResponse`; Excel via `openpyxl` returning bytes. Columns: id, username, email, full_name, role, is_active, created_at.

**Register endpoint:** open (`REGISTRATION_OPEN=true` default). Assigns `lead` if no role provided.

---

### Backend docs structure established
`api/docs/` created as a parallel structure to `web-testoria/docs/`, following the same `00-meta/` through `08-decisions/` numbering. Same five-phase work cycle applies. Backend-specific topics (service layer, data layer, async patterns, migrations) added under `02-architecture/backend/` and `03-engineering/patterns/`.
