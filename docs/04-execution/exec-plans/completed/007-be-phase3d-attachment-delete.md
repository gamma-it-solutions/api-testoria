# Execution Plan: 007 — Attachment Delete Endpoint

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: HIGH

---

## Goal

Add `DELETE /test-results/{id}/attachments/{attach_id}` so testers can remove incorrectly uploaded files from a test result.

---

## Context

The frontend `deleteAttachment()` call (`DELETE /test-results/:id/attachments/:attachId`) has no matching backend route. The upload endpoint (`POST /test-results/{id}/attachments`) exists but there is no way to remove an uploaded file. This leaves users unable to correct mistakes without admin DB intervention.

---

## Scope

### In scope
- `DELETE /test-results/{result_id}/attachments/{attach_id}` endpoint
- Remove the file from the filesystem
- Delete the `ResultAttachment` row from the DB
- 404 if `attach_id` does not exist or does not belong to `result_id`

### Out of scope
- Bulk delete of all attachments on a result
- Soft delete / recycle bin

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| router | `app/api/v1/test_results.py` | `DELETE /{result_id}/attachments/{attach_id}` |
| tests | `tests/integration/test_test_results_api.py` | Delete endpoint tests |
| docs | `docs/06-generated/endpoints.md` | Add endpoint row |

No schema or model changes needed — `ResultAttachment` model already exists.

### Endpoint implementation

```python
@router.delete("/{result_id}/attachments/{attach_id}", status_code=204)
async def delete_attachment(
    result_id: int,
    attach_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("tester")),
):
    attachment = await db.get(ResultAttachment, attach_id)
    if not attachment or attachment.test_result_id != result_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Remove file from disk
    import os
    if os.path.exists(attachment.file_path):
        os.remove(attachment.file_path)

    await db.delete(attachment)
    # get_db commits on request completion
```

### Key decisions

- Ownership check: verify `attachment.test_result_id == result_id` — prevents deleting another result's attachments by guessing IDs
- File removal is best-effort: if the file is already missing from disk, continue and delete the DB row (log a warning)
- Min role: `tester` (same as upload)

---

## Tasks

### Implementation
- [ ] Add `DELETE /{result_id}/attachments/{attach_id}` route to `app/api/v1/test_results.py`
- [ ] Handle missing file gracefully (log warning, do not raise)
- [ ] Write integration test: upload attachment, delete it, verify 204; try delete again → 404

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `docs/06-generated/endpoints.md` — add endpoint row
- [ ] `docs/04-execution/tech-debt.md` — mark resolved
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `DELETE /test-results/{id}/attachments/{attach_id}` returns 204 on success
- [ ] File is removed from the filesystem
- [ ] DB row is deleted
- [ ] Returns 404 if `attach_id` does not belong to `result_id`
- [ ] Returns 401 without auth
- [ ] Missing file on disk does not cause a 500 error
