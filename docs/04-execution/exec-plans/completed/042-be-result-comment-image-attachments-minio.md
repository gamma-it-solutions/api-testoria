# Execution Plan: MinIO object storage for result comment screenshots + multi-upload + report embedding

**Date**: 2026-04-22
**Author**: gabi
**Status**: Draft

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Back attachment storage with MinIO (S3-compatible) instead of local disk, accept multiple screenshots in a single multipart request keyed to a `TestResult`, serve them via presigned URLs in `TestResultResponse`, and embed the images in PDF/Excel report exports.

---

## Context

Today:

- `result_attachments` table exists with `file_path: String(1024)`. The column holds a local filesystem path (`uploads/{result_id}/{filename}`) written by `test_result_service.upload_attachment` (`app/services/test_result_service.py:293-313`).
- `POST /test-results/{result_id}/attachments` accepts **one file per request** (`app/api/v1/test_results.py:88-108`). The frontend has to loop and fire N requests to persist N pasted screenshots.
- `ResultAttachmentResponse` only exposes `id`, `filename`, `file_size`, `mime_type`, `uploaded_at`, `uploaded_by` — **no download URL**. Consumers have nothing to render; the file is stranded on the server.
- Report generation (`app/services/report_service.py`, `app/services/export_service.py`) never touches `attachments` — PDF/Excel exports drop screenshots on the floor.
- Web plan-065 ("execute save button — upload images") wires the Save click to the existing one-file-at-a-time endpoint, but the frontend user complaint is exactly the gap this plan closes: _"in WEB I can Ctrl+V image/screenshot but is not saved"_. The frontend-side plan (web plan-100) depends on the new bulk endpoint and URL exposure landing here first.

Local disk is already the wrong answer:

- `uploads/` doesn't survive container rebuilds unless bind-mounted (fragile across dev / CI / prod).
- Horizontal scaling is impossible — any second API replica can't see files written by the first.
- No lifecycle / retention policy; tech-debt already tracks that soft-deleted rows accumulate forever.

MinIO is the right size of dependency: S3-compatible API, runs in the same `docker-compose.yml` as Postgres and Centrifugo, and swaps cleanly to AWS S3 in prod by changing endpoint + credentials.

Related:

- Web plan-065 (completed) — save-button wiring, assumed one-file-per-request
- Web plan-073 (completed) — detail view comment save
- Tech debt: _"Synchronous file I/O in attachments — large uploads block event loop. Fix: use aiofiles."_ — MinIO upload is async out of the box, closes this item
- Tech debt: _"No purge pathway for soft-deleted rows"_ — MinIO lifecycle rules open a path (separate plan)

---

## Scope

### In scope

- New MinIO service in `docker-compose.yml` and `docker-compose.test.yml` (dev + integration tests use the same image)
- New settings in `app/config.py`: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_ATTACHMENTS`, `S3_REGION`, `S3_USE_SSL`, `S3_PRESIGN_TTL_SECONDS` (default 900)
- New `app/core/storage.py` — thin async wrapper over `aioboto3` with `put_object`, `delete_object`, `generate_presigned_url`, `ensure_bucket`. Exposed as a module-level client initialized at app startup (`app/main.py` lifespan).
- Alembic revision:
  - Rename `result_attachments.file_path` → `object_key` (semantic change; stores the MinIO object key, not a filesystem path). Column type stays `String(1024)`.
  - Keep the column name `file_path` as a read-only alias via a SQLAlchemy `column_property` if and only if a consumer still reads it — prefer a clean rename and fix the two internal call sites.
  - Add `storage_backend: String(16) NOT NULL DEFAULT 's3'` to make the migration-from-disk path explicit and reversible.
- Service-layer rewrite of `test_result_service.upload_attachment`:
  - Write bytes to MinIO under object key `results/{result_id}/{uuid4}-{sanitized_filename}`
  - Persist `object_key` (not a local path), `storage_backend='s3'`
  - Return the `ResultAttachment` row unchanged; URL generation happens at the schema layer
- New bulk endpoint: `POST /test-results/{result_id}/attachments/bulk` accepting `files: list[UploadFile] = File(...)`. Returns `list[ResultAttachmentResponse]`. Uploads run via `asyncio.gather(..., return_exceptions=True)` so one bad file doesn't kill the batch; partial success returns the committed rows and a `failed` array.
  - Keep the existing single-file endpoint as-is (backward compat for the existing call sites; see Tech debt).
- Extend `ResultAttachmentResponse` with `url: str` — a presigned GET URL valid for `S3_PRESIGN_TTL_SECONDS`. Generated lazily in a Pydantic `model_validator(mode='after')` by calling the storage client.
- Extend `TestResultResponse` so `attachments` now carries the URL (no separate round-trip needed by the web to render images in comment lists).
- Content validation:
  - Whitelist `image/png`, `image/jpeg`, `image/webp`, `image/gif` for the bulk endpoint (no PDFs, no executables). The single-file endpoint keeps its current permissive behaviour for non-screenshot attachments.
  - Cap per-file size at `MAX_ATTACHMENT_BYTES` (default 10 MB, config-driven). Reject with 413 before reading the full body when `content-length` exceeds the cap.
  - Cap bulk request total at 10 files per call.
- Delete path:
  - `delete_attachment` calls `storage.delete_object(object_key)` before removing the row. On storage failure, log + keep the row (never orphan a row pointing to a deleted object without operator visibility).
  - Soft-delete of `TestResult` does **not** delete attachments from MinIO (matches current disk behaviour; lifecycle / GC is a separate plan).
- Report generation:
  - `report_service.generate_run_report` loads `attachments` for every result in the run and embeds an `image_url` list in the report payload.
  - `export_service.export_run_pdf`: inline each attachment per result (one thumbnail per row; click-through URL under it). Use presigned URLs with TTL bumped to `S3_REPORT_PRESIGN_TTL_SECONDS` (default 7 days) so a PDF remains viewable after download.
  - `export_service.export_run_excel`: insert as image in the result row using `openpyxl.drawing.image` (download the bytes server-side and embed; Excel has no concept of a remote URL for inline images).
  - Hard cap: max 3 images per result embedded in PDF/Excel; extras replaced by "+N more — open run detail in app" line. Configurable via `REPORT_ATTACHMENT_MAX_PER_RESULT` (default 3).
  - Report cache key (if any) must include attachment count + latest `uploaded_at` so an image added after the first export invalidates.
- Integration test suite runs against MinIO in `docker-compose.test.yml`:
  - `conftest.py` fixture starts a per-session bucket, tears down at end.
  - CI: `docker-compose.test.yml up -d minio` step before `pytest`.

### Out of scope

- Migration of existing local files to MinIO — handled by a one-shot script (`scripts/migrate_attachments_to_minio.py`) run out-of-band; **not** auto-run by Alembic. The `storage_backend` column flags which rows are on disk vs MinIO so both can coexist during the transition.
- Thumbnail generation / image resizing — originals only; the PDF embed uses jsPDF client-side scaling on the web plan, and the backend PDF embed uses fixed-width render via Pillow if needed (logged as follow-up)
- CDN / CloudFront fronting — S3 presigned URLs are fine for v1
- Virus scanning of uploads — logged as tech debt (ClamAV via `python-clamd`)
- Inline-image comment bodies (HTML `<img>` tags inside `comment`) — current design stays "comment + separate attachments". The frontend renders images as a gallery below the comment text.
- WebSocket push for new attachment events — attachments ride the existing `result.updated` channel on next result refresh
- Project-level attachment quotas
- MinIO lifecycle rules / TTL policies (will need a dedicated plan once retention is decided)
- Re-keying / rotation of object keys on rename
- Changing the existing single-file endpoint's semantics — left alone to keep web plan-065 green until the frontend migrates to bulk
- Hardening against SSRF if `S3_ENDPOINT_URL` is ever user-controllable (config is operator-only)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| infra | `docker-compose.yml`, `docker-compose.test.yml`, `docker-compose.prod.yml` | Add `minio` service (image `minio/minio:latest`), healthcheck, named volume `minio_data`. Dev default creds documented in `.env.example`. |
| deps | `requirements.txt` | Add `aioboto3>=12` and `botocore`. `Pillow` already present transitively via jsPDF? — no, add explicitly for server-side image handling in Excel export. |
| config | `app/config.py` | Add `S3_*` settings; drop reliance on `UPLOAD_DIR` for the MinIO-backed path (keep the setting for the legacy single-file endpoint until migration). |
| core | `app/core/storage.py` (new) | `async put_object(key, body, content_type)`, `async delete_object(key)`, `generate_presigned_url(key, ttl)`, `ensure_bucket()`. Module-level `s3_client`. |
| lifespan | `app/main.py` | On startup: `await storage.ensure_bucket()`. On shutdown: close the `aioboto3` session. |
| models | `app/models/result_attachment.py` | Rename `file_path` → `object_key`; add `storage_backend`. |
| schemas | `app/schemas/test_result.py` | `ResultAttachmentResponse` gains `url: str`. Added via `model_validator(mode='after')` that consults the storage client; falls back to a legacy disk URL route `/files/legacy/{id}` if `storage_backend == 'local'` (short-lived shim). |
| migration | `alembic/versions/<new>.py` | Rename column, add column, backfill `storage_backend='local'` for existing rows, `server_default='s3'` for new rows. Reversible. |
| services | `app/services/test_result_service.py` | Rewrite `upload_attachment` to use the storage client; add `upload_attachments_bulk` returning `(successes, failures)`. `delete_attachment` deletes the object then the row. |
| router | `app/api/v1/test_results.py` | Add `POST /test-results/{result_id}/attachments/bulk`. Keep single-file endpoint. Add content-type and size guards via a new `app/core/uploads.py` helper. |
| services | `app/services/report_service.py`, `app/services/export_service.py` | Enrich report payload with attachment URLs; PDF & Excel generators embed images (hard cap per result). |
| legacy shim | `app/api/v1/files.py` (new, if needed) | `GET /files/legacy/{attachment_id}` streams from local disk for unmigrated rows. Deleted once `scripts/migrate_attachments_to_minio.py` drains `storage_backend='local'`. |
| tests | `tests/unit/services/test_storage.py`, `tests/unit/services/test_test_result_service.py`, `tests/integration/test_attachments_api.py`, `tests/integration/test_reports_with_attachments.py` | New + extended. MinIO via docker-compose.test.yml. |
| scripts | `scripts/migrate_attachments_to_minio.py` (new) | One-shot: walks `storage_backend='local'` rows, uploads to MinIO, flips column, verifies, optionally deletes local file. Idempotent; dry-run by default. |

### Key decisions

- **`aioboto3` over `minio-py`.** Botocore is the industry standard; swapping endpoint URL from MinIO → AWS S3 → any S3-compatible provider is a config change. `minio-py` would lock us to MinIO's client semantics.
- **Presigned URLs, not proxied streams.** Sending every image through the FastAPI app event loop is a scalability trap. Presigned URLs let the browser go direct to MinIO / S3; the API stays stateless. The TTL (15 min default) is short enough to be safe for RBAC-sensitive content while long enough for a page session.
- **Bulk endpoint is additive, not a replacement.** Keeping `POST /.../attachments` unchanged means web plan-065 (which shipped at one-file-per-request) keeps working during the frontend migration. Both endpoints share the same service helper so there's no semantic drift.
- **Object key = `results/{result_id}/{uuid4}-{sanitized_filename}`.** Result-scoped prefix enables future MinIO lifecycle rules ("delete all under `results/{id}/` on hard-delete"). UUID prefix collision-proofs same-name uploads. Sanitized filename preserves human-readability in MinIO browser / download dialogs.
- **`storage_backend` column instead of a flag file.** An explicit discriminator is queryable (`SELECT COUNT(*) WHERE storage_backend='local'` drives the migration dashboard) and removes any guessing in the URL-resolver about what the `object_key` means.
- **Rename `file_path` → `object_key` now, not later.** The semantics change anyway; leaving the old name invites bugs where someone `Path(attachment.file_path).exists()` on production. One rename migration is cheaper than two.
- **Presigned URL generation in the Pydantic `model_validator`, not in the service.** The service has no opinion about presentation TTL; the schema is the right layer for a read-shape concern. Keeps the service pure and unit-testable with no MinIO dependency.
- **Report-embed uses a longer TTL (7 days).** A generated PDF outlives the request session; a 15-min presigned URL in a PDF expires before the user finishes reading. 7 days matches most "report kept for a week" workflows; configurable.
- **Excel embeds image bytes server-side.** Unlike PDF, Excel cannot reference a remote URL as an inline image — the image must be part of the workbook binary. The Excel export thus downloads the object from MinIO server-side and inserts via `openpyxl.drawing.image.Image`. Acceptable because Excel exports are already slower than PDF and rarely run on hot paths.
- **Max 3 images per result in reports.** Keeps PDF / Excel file size sane. Configurable; revisit if product pushes back. The in-app detail view is unconstrained.
- **MIME whitelist is opinionated.** The bulk endpoint is explicitly for screenshots from `Ctrl+V`; locking it to common image types prevents it becoming a generic file store by accident. The single-file endpoint stays permissive.
- **No MinIO → DB transaction coupling.** Uploads first, DB row second. If the DB insert fails we delete the MinIO object in a cleanup block; if that cleanup fails we log a `warning` with the orphan key. Orphan detection is cheap (nightly `SELECT object_key FROM result_attachments` vs `ListObjects`) and belongs in a GC plan.
- **Size guard uses `content-length`, not post-read.** FastAPI / Starlette exposes the header; reject at 413 before draining the socket. Defence in depth: a second check after full read catches chunked requests that lied about their size.
- **Integration tests hit real MinIO.** Matches the project-wide "integration tests must hit a real database, not mocks" stance — swapping in a `moto` mock for S3 papers over subtle signature / content-type / CORS issues that only appear in the real server.

---

## Tasks

### Implementation
- [ ] Add `minio` service to `docker-compose.yml` (healthcheck on `/minio/health/live`, named volume, default creds)
- [ ] Add same service to `docker-compose.test.yml` with ephemeral volume
- [ ] Document dev creds in `.env.example`; set `S3_ENDPOINT_URL=http://minio:9000` for container-to-container access
- [ ] Add `aioboto3`, `botocore`, `Pillow` to `requirements.txt`; `pip install -r` and `pip freeze` the pins
- [ ] Add `S3_*` and `REPORT_ATTACHMENT_MAX_PER_RESULT`, `MAX_ATTACHMENT_BYTES`, `S3_PRESIGN_TTL_SECONDS`, `S3_REPORT_PRESIGN_TTL_SECONDS` to `app/config.py`
- [ ] Implement `app/core/storage.py`:
  - [ ] Lazy singleton `aioboto3.Session().client("s3", ...)` via `async with` context manager
  - [ ] `put_object(key, body: bytes, content_type: str)`
  - [ ] `delete_object(key)` — idempotent; swallows `NoSuchKey`
  - [ ] `generate_presigned_url(key, ttl_seconds)`
  - [ ] `ensure_bucket()` — create if missing; enable versioning? (no, keeps cost low; reconsider)
  - [ ] `get_object(key) -> bytes` — used by Excel report embed
- [ ] Wire startup / shutdown hooks in `app/main.py` lifespan
- [ ] Alembic migration:
  - [ ] Rename `file_path` → `object_key`
  - [ ] Add `storage_backend VARCHAR(16) NOT NULL DEFAULT 's3' SERVER_DEFAULT 'local'` — backfill existing rows with `'local'`, then switch default to `'s3'` for new rows (two-step default in the migration)
  - [ ] `alembic upgrade head` clean; `alembic downgrade -1` clean
- [ ] Rename column in `app/models/result_attachment.py`; add `storage_backend`
- [ ] Update `ResultAttachmentResponse` schema:
  - [ ] Add `url: str`
  - [ ] `model_validator(mode='after')` generates the URL via the storage client (short TTL); for `storage_backend='local'`, build the legacy shim URL
- [ ] Implement `app/core/uploads.py`:
  - [ ] `validate_image_upload(file: UploadFile, max_bytes: int) -> None` — MIME whitelist + size check
  - [ ] Reject with 413 / 415 via `HTTPException`
- [ ] Rewrite `test_result_service.upload_attachment`:
  - [ ] Build object key, call `storage.put_object`, insert row with `storage_backend='s3'`
  - [ ] On DB failure, `storage.delete_object`; log failure of cleanup
- [ ] Add `test_result_service.upload_attachments_bulk(db, result_id, files)`:
  - [ ] `asyncio.gather(*[...], return_exceptions=True)` over `upload_attachment`
  - [ ] Partition into successes and failures; return both
- [ ] Router:
  - [ ] New `POST /test-results/{result_id}/attachments/bulk` endpoint, `files: list[UploadFile]`, returns `ResultAttachmentBulkResponse{ uploaded: list[...], failed: list[...] }`
  - [ ] Enforce `len(files) <= 10` at the router
  - [ ] Run `validate_image_upload` per file before service call
- [ ] Rewrite `delete_attachment`:
  - [ ] Delete MinIO object first; then delete DB row
  - [ ] If DB delete fails after object is gone, log orphan (object already deleted; row stays pointing nowhere — surface via orphan-detection task)
  - [ ] Legacy rows (`storage_backend='local'`): keep the existing `Path.unlink` path
- [ ] Legacy shim `GET /files/legacy/{attachment_id}`:
  - [ ] Streams from disk for `storage_backend='local'` rows
  - [ ] Requires same role as `GET /test-results/{id}`
  - [ ] 410 Gone once row's `storage_backend='s3'` (catches stale PDFs rendered before migration)
- [ ] Report enrichment:
  - [ ] `report_service.generate_run_report` eager-loads `attachments` for every result
  - [ ] Payload includes `attachment_urls: list[str]` per result (report-TTL presigned URLs)
- [ ] PDF export:
  - [ ] `export_service.export_run_pdf` embeds up to `REPORT_ATTACHMENT_MAX_PER_RESULT` thumbnails per result
  - [ ] Uses Pillow to resize server-side to max 800px wide before embed
  - [ ] "+N more" line when overflow
- [ ] Excel export:
  - [ ] `export_service.export_run_excel` downloads object bytes and inserts via `openpyxl.drawing.image.Image`
  - [ ] Images placed in a dedicated "Attachments" column; cell height adjusted
  - [ ] Same 3-per-result cap
- [ ] Migration script `scripts/migrate_attachments_to_minio.py`:
  - [ ] Dry-run default (logs what it would upload)
  - [ ] `--commit` actually uploads and flips `storage_backend`
  - [ ] `--delete-local` removes the disk file after successful verification
  - [ ] Idempotent: re-runs skip rows already `='s3'`
  - [ ] Progress log + exit code
- [ ] Unit tests:
  - [ ] `test_storage.py`: put / delete / presigned-url round-trip against moto? — no, prefer integration. Keep unit tests only for the presigned-URL builder if we extract it.
  - [ ] `test_test_result_service.py`: bulk upload partial failure (3 files, 1 rejects) returns 2 committed + 1 failed; MIME whitelist; size cap; object cleanup on DB failure
  - [ ] `test_report_service.py`: attachments included in payload; TTL matches report-TTL setting
- [ ] Integration tests (real MinIO):
  - [ ] `test_attachments_api.py`:
    - [ ] POST bulk with 3 valid images → 201, 3 rows, 3 presigned URLs valid (HTTP GET succeeds)
    - [ ] POST bulk with 11 files → 400 (cap)
    - [ ] POST bulk with 2 valid + 1 PDF → 2 committed, 1 in `failed`, 207-equivalent response
    - [ ] GET test-result returns attachments with URL
    - [ ] DELETE attachment removes object from bucket (verified via ListObjects)
    - [ ] Legacy `file_path` row still resolvable via shim
  - [ ] `test_reports_with_attachments.py`:
    - [ ] Run with 2 results × 2 attachments each → PDF contains 4 embedded images
    - [ ] Run with a result having 5 attachments → PDF shows 3 + "+2 more"
    - [ ] Excel export contains the images in the Attachments column
    - [ ] Report generation over a run with 0 attachments is unchanged (no regressions)
- [ ] Local smoke: `docker-compose up`, open the MinIO console, upload via API, confirm object appears under `results/<id>/`

### Quality check (Phase 4)
- [ ] `pytest` — all unit + integration tests pass (MinIO container running)
- [ ] `pytest --cov=app --cov-report=term-missing` — coverage for new service code ≥ 85%
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors (aioboto3 has stubs; verify `botocore-stubs` is pinned)
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed
- [ ] Manual: `alembic upgrade head` + `alembic downgrade -1` + `alembic upgrade head` round-trip clean
- [ ] Manual: migration script dry-run against a dev DB with seeded local-disk attachments; `--commit` succeeds; rows flip to `'s3'`; files appear in MinIO

### Docs update (Phase 5)
- [ ] `docs/06-generated/endpoints.md` — new bulk endpoint, updated `ResultAttachmentResponse` shape with `url` field, updated attachment delete behaviour
- [ ] `docs/06-generated/db-schema.md` — `result_attachments.object_key` rename + `storage_backend` column
- [ ] `docs/01-product/features/004-test-execution.md` — note that screenshots now persist and display inline; multiple per result; included in reports
- [ ] `docs/02-architecture/ARCHITECTURE.md` — add `app/core/storage.py` to the codemap; add MinIO to the infra diagram / infra list; new invariant candidate: _"All binary file storage goes through `app/core/storage.py` — no new `open(path, 'wb')` in services."_
- [ ] `docs/02-architecture/backend/data-layer.md` — document the `storage_backend` discriminator pattern
- [ ] `docs/05-quality/SECURITY.md` — presigned-URL TTL policy, MIME whitelist, size cap
- [ ] `docs/08-decisions/changelog.md` — entry: MinIO adoption, bulk endpoint, URL exposure on attachment response, PDF/Excel embed, report TTL policy
- [ ] `docs/04-execution/tech-debt.md`:
  - [ ] Resolve: _"Synchronous file I/O in attachments"_ (`aiofiles` → aioboto3)
  - [ ] Add: local-disk migration script must be run once in prod; legacy shim `GET /files/legacy/{id}` to be removed after every row is `storage_backend='s3'`
  - [ ] Add: MinIO lifecycle / GC plan for orphaned objects
  - [ ] Add: virus scanning of uploads (ClamAV)
  - [ ] Add: image thumbnailing pipeline (currently downsize-at-export only)
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| MinIO container unavailable during dev / CI causes every test to fail | Medium | Healthcheck in `docker-compose.test.yml`; conftest session fixture waits on `/minio/health/live`; failure surfaces as an explicit error, not a silent hang |
| Presigned URL TTL too short — users open a report in a tab, come back 20 min later, images 403 | Medium | Report-TTL default 7 days; live app TTL 15 min refreshes on every list-results fetch; doc the two TTLs in SECURITY.md |
| Excel embed balloons workbook size (100-case run × 3 screenshots × 2 MB) | Medium | Pillow downsize to max 800px / 85% JPEG quality before embedding; configurable; eventual thumbnail cache is a follow-up |
| Migration script partially flips rows then fails — half local, half s3 | Low | Script is idempotent and row-at-a-time; `storage_backend` discriminator means both types resolve; re-running completes the remaining rows |
| `object_key` collisions if UUID logic regresses | Very low | Key includes `uuid4()`; service rejects empty UUIDs; integration test asserts distinct keys for 100 concurrent uploads |
| Legacy shim forgotten and removed before all rows migrated | Medium | Shim returns 410 Gone only for `'s3'` rows; never removes itself. Removal guarded by `SELECT COUNT(*) WHERE storage_backend='local' = 0` check documented in tech debt |
| Single-file endpoint drifts from bulk endpoint on validation rules | Medium | Both call `validate_image_upload` + the same service helper; unit test asserts they share the validator |
| Large bulk requests tie up the worker loop | Medium | Cap of 10 files per request; per-file 10MB cap; gather runs in parallel but each upload is async |
| `botocore` presigner uses sync I/O under the hood, blocking the event loop | Low | aioboto3 documents `generate_presigned_url` as sync but fast; wrap in `asyncio.to_thread` if profiling shows p99 latency pressure |
| MIME sniffing trusts client content-type and accepts a script named `.png` | Low | Second-line check: call `PIL.Image.open(BytesIO(bytes))` inside `validate_image_upload` to confirm the bytes parse as a real image; reject otherwise |
| SSRF risk if `S3_ENDPOINT_URL` is configurable via env and env is ever user-influenced | Very low | Config is operator-only; document explicitly; no user-input path into endpoint URL |
| Report generation pulls large image bytes server-side and spikes memory | Medium | Excel path streams per image; Pillow resize caps intermediate buffer; PDF path uses presigned URL reference only |
| Web plan-065 (one-file-per-request) breaks because of the `object_key` rename | Low | Rename is internal (column + `file_path` attribute gone); HTTP contract unchanged; integration test asserts single-file endpoint still 201s and returns URL |
| Orphaned objects accumulate when DB inserts fail after object put | Low | Compensating delete in the service; orphan detection GC in a follow-up plan; operator can list via `aws s3 ls` any time |

---

## Definition of done

- [ ] `docker-compose up` brings up MinIO alongside Postgres + Centrifugo; API connects and auto-creates the bucket on first run
- [ ] `POST /test-results/{result_id}/attachments` (single) still works, now writes to MinIO
- [ ] `POST /test-results/{result_id}/attachments/bulk` accepts up to 10 images in one request; returns per-file success / failure
- [ ] `GET /test-runs/{id}/results` returns `attachments[].url` as a presigned GET URL that a browser can open directly
- [ ] `DELETE /test-results/{id}/attachments/{attach_id}` removes the MinIO object and the DB row
- [ ] PDF report embeds up to 3 images per result, or shows "+N more"
- [ ] Excel report embeds the same images inline in the workbook
- [ ] Alembic migration clean in both directions; `storage_backend='local'` for pre-migration rows, `'s3'` for new rows
- [ ] Legacy shim endpoint resolves `storage_backend='local'` rows correctly
- [ ] Migration script idempotent; dry-run report is accurate
- [ ] Integration tests run green against the real MinIO container
- [ ] Unit coverage ≥ 85% on new service code
- [ ] `ruff`, `mypy`, `pytest` all clean
- [ ] Docs updated across `endpoints.md`, `db-schema.md`, feature docs, architecture, security, changelog, tech-debt
- [ ] Web plan-100 unblocked — frontend can call the bulk endpoint, render URLs, and embed images in client-side PDF / Excel exports
