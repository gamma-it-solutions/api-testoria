# Feature 009 — CI/CD Integration

## Overview

CI/CD integration enables external CI pipelines (GitHub Actions, GitLab CI, Jenkins) to push test results into Testoria and display live pass/fail badges — without using the web UI.

## API Surface

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/ci/webhooks` | Receive CI webhook payloads (extensibility hook) |
| `POST /api/v1/ci/results/bulk` | Bulk import JUnit XML test results into a test run |
| `GET /api/v1/ci/runs/{id}/badge` | Public SVG badge showing pass rate for a test run |

## JUnit XML Bulk Import

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

- Only JUnit XML format is supported (not TestNG or NUnit).
- Webhook signature verification is not implemented (security hardening, future).
- `classname.name` must exactly match a `TestCase.title` for the result to be imported.
