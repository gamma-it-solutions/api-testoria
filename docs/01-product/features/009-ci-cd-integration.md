# Feature 009 — CI/CD Integration

## Overview

CI/CD integration enables external CI pipelines (GitHub Actions, GitLab CI, Jenkins) to push test results into Testoria and display live pass/fail badges — without using the web UI.

## API Surface

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/ci/webhooks` | Receive CI webhook payloads (extensibility hook) |
| `POST /api/v1/test-runs/{id}/results/import` | **Preferred** — import JUnit XML or JSON, matched on `automation_id` |
| `POST /api/v1/ci/results/bulk` | Legacy bulk JUnit import (title matching, counts only) |
| `POST/GET/DELETE /api/v1/api-keys` | Mint, list and revoke CI credentials |
| `GET /api/v1/ci/runs/{id}/badge` | Public SVG badge showing pass rate for a test run |

## Result Import (plan 050)

`POST /api/v1/test-runs/{run_id}/results/import` is the path new integrations
use. It accepts a JUnit XML or JSON report as multipart `file`, authenticates
with either a Bearer token or an API key, and **always returns 200 with a
report** — an unmatched test is information, not an error.

### Matching

Resolved server-side in order; `matched_by` in the response says which rule hit:

| # | Rule | Matches `classname.name` against |
|---|------|----------------------------------|
| 1 | `automation_id` | `TestCase.automation_id` |
| 2 | `automation_id_dotted` | `dotted(automation_id)` — **pytest node IDs** |
| 3 | `automation_id_name` | `automation_id` == the bare test name |
| 4 | `title` | `TestCase.title` (legacy behaviour) |
| 5 | `title_dotted` | `dotted(title)` |

`dotted()` rewrites `a/b.py::C::test_x` → `a.b.C.test_x`. Rule 2 is the one that
carries a real pytest suite: pytest's JUnit output contains **no node ID**
(`junit_family=xunit2` emits neither `file` nor `line`), so `classname.name` is
the dotted form while `automation_id` is typically the raw node ID. Normalisation
is applied to the stored value, never to the XML — the reverse direction is
ambiguous.

A parametrized test carries its param in `name` (`test_roles[TESTER]`), so one
Testoria case per variant reports independently.

Duplicates are reported as `reason="ambiguous"`, never resolved first-wins.

### Report

`{ run_id, total, matched, submitted, unmatched, unmatched_cases[], matched_by{}, status_counts{} }`
— `unmatched_cases` is capped at 100 entries while `unmatched` keeps the true
count. Each entry names the test and why it missed (`no_match` / `ambiguous` /
`out_of_scope`).

### Idempotence and cost

Results upsert on `(run, case)` and no-op resubmits write no history, so a
re-run after a network blip is correct. Import uses `submit_many`: the run is
validated once, cases are fetched in one query, the run transitions at most
once, and **one** aggregate `test_result_bulk` realtime event is published
instead of one per result.

### Finding unmapped cases

`GET /projects/{id}/test-cases?has_automation_id=false` lists the cases no
automated run can link to — what `testoria case list --unmapped` calls.

---

## JUnit XML Bulk Import (legacy)

- Accepts a JUnit XML file via multipart upload with a `test_run_id` query parameter.
- Each `<testcase>` element is matched against `TestCase.title` using the pattern `classname.name`.
- Unmatched test cases are counted as `skipped` (not errors).
- Matched test cases are submitted as results via the existing `TestResultService.submit` upsert logic.
- Returns `{ submitted: int, skipped: int }`.

### Status mapping

| JUnit element | Testoria status |
|---------------|-----------------|
| `<failure>` | `failed` |
| `<error>` | `failed` |
| `<skipped>` | `skipped` |
| (none) | `passed` |

## Badge

- SVG badge with "tests" label and pass-rate percentage.
- Colour thresholds: ≥ 90% green (`#4c1`), ≥ 70% yellow (`#dfb317`), < 70% red (`#e05d44`).
- Public endpoint (no auth required) for embedding in README files.
- Returns `Cache-Control: no-cache` to avoid stale badges.

## CI/CD Pipelines

### CI (`.github/workflows/ci.yml`)

- Triggers on push to `main`, pull requests, and manual dispatch.
- **check** job: lint (`ruff`), type-check (`mypy`), unit tests.
- **integration** job: runs against real PostgreSQL 16 + Redis 7 via GitHub Actions services. Runs Alembic migrations before tests.

### CD (`.github/workflows/cd.yml`)

- Triggers after CI completes on `main` (or manual dispatch).
- Builds Docker image, pushes to GHCR.
- Deploys to EC2 via SSH: pulls latest code, runs Alembic migrations in a separate container, restarts API container, health check.

## Constraints

- `POST /ci/results/bulk` is retained unchanged for existing callers; its
  retirement is tracked in `docs/04-execution/tech-debt.md`.
- Only JUnit XML and a simple JSON list are supported (not TestNG or NUnit).
- Webhook signature verification is not implemented (security hardening, future).
- `classname.name` must exactly match a `TestCase.title` for the result to be imported.
