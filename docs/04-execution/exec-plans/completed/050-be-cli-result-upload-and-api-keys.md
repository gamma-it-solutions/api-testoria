# Execution Plan: 050 — CLI Result Upload + API Keys

**Date**: 2026-08-10
**Author**: gabi
**Status**: Complete
**Priority**: HIGH

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Ship a `testoria` CLI that pushes automated test results into a test run from a CI pipeline in one command, authenticated by a revocable, project-scoped API key rather than a human's username and password.

---

## Context

Automated suites (`testoria-tests`) currently have no supported way to publish results. The
only ingest path is `POST /api/v1/ci/results/bulk` (`app/api/v1/ci_integration.py:27`), which
has four problems that make it unusable as the CLI's backend:

1. **Auth is a human JWT.** Access tokens live 30 min, refresh tokens rotate on every use, and
   there is no blocklist on logout (see `docs/04-execution/tech-debt.md` — *"No token blocklist
   on logout"*). A CI secret holding `TESTORIA_PASSWORD` is a full human account — and since
   `lead` is the default role for new users (`app/models/user.py:23`), that is usually an account
   that can manage other users. It cannot be revoked without rotating `SECRET_KEY` for everyone.
2. **Matching is title-only.** `TestCase.automation_id` exists and is indexed
   (`app/models/test_case.py:36`) but the importer matches `classname.name` against
   `TestCase.title`. This is already logged as tech debt (*"Auto-link CI runs to test cases via
   `automation_id`"*).
3. **The response is two integers.** `{submitted, skipped}` gives a pipeline no way to report
   *which* tests failed to map, so misconfiguration is silent.
4. **It is O(n) queries.** One `SELECT` per `<testcase>` element (`ci_service.py:50`), plus a
   full `test_result_service.submit()` per match (≈6 queries + a Centrifugo publish + a run
   transition each). A 2000-case suite is ~14k queries and 2000 websocket publishes.

### The reference consumer: `testoria-tests`

The sibling automation kit (`/home/gabi/work/personal/gammait/testoria-tests`) is the pipeline
this plan is validated against. It already reports into Testoria via a **live reporter** in
`tests/conftest.py` (`testoria_run` + `pytest_runtest_logreport`, documented in that repo's
`docs/06-generated/reporter-map.md`). Reading it changes several assumptions:

- **`automation_id` is already populated.** `scripts/seed_test_cases.py:486` creates every case
  with `automation_id=<pytest node id>`, e.g.
  `tests/auth/test_auth.py::TestAuth::test_login_with_valid_credentials_returns_token`.
  There is no backfill to do — the data the matcher needs already exists in Testoria.
- **But JUnit XML does not carry node IDs.** Verified against pytest 8.3.5 (this repo's pinned
  version), which defaults to `junit_family=xunit2` and emits **no** `file`, `line`, or nodeid
  attribute:

  ```xml
  <testcase classname="tests.auth.test_auth.TestAuth" name="test_login_with_valid_credentials_returns_token"/>
  <testcase classname="tests.auth.test_auth"          name="test_create_user_all_roles[TESTER]"/>
  ```

  So `classname.name` is the *dotted* form of the node ID. The two are deterministically
  convertible in one direction — see the `dotted()` rule in Part B. Without that step the
  matcher would score **zero** against the existing seeded data, so it is load-bearing, not a
  nicety.
- **Parametrization is representable after all.** The param appears in `name`
  (`test_create_user_all_roles[TESTER]`), so one Testoria case per variant works. That is the
  decision TD-010 in the tests repo has been waiting on.
- **CI currently authenticates as admin.** `.github/workflows/tests.yml` passes
  `FEATURE_ADMIN_USERNAME` / `FEATURE_ADMIN_PASSWORD` as repository secrets. For *this* suite the
  admin account is genuinely required — the tests exercise user management, so they must hold a
  privileged credential. What the API key changes here is narrower but still real: the
  **reporting** path stops needing it, so an upload step can run with a `tester`-scoped,
  revocable key even when the test step's credentials are broad. For any other pipeline (a
  product team uploading its own results) the admin secret disappears entirely.
- **The live reporter has a structural gap the CLI closes.** Results are posted during the
  session, so a killed session — `activeDeadlineSeconds` on the k8s Job, a cancelled workflow,
  an OOM kill, the 20/45-minute job timeouts — leaves the run stuck `active` with partial
  results. A post-hoc upload driven by `if: always()` uploads whatever actually ran.

The reporter is not being deleted by this plan. It gives real-time progress and ties failure
screenshots to individual results, which a batch upload cannot. The CLI is added **alongside**
it first (Part D), and the tests repo decides later whether to retire the reporter.

Five CLI plans were drafted in `docs/04-execution/exec-plans/do-not-execute/`
(`099-cli-phase1-foundation` … `099-cli-phase5-ci-docs`). They predate `automation_id`
(plan 024), the run lifecycle rename (plan 039), and `no_run` (plan 032), and they specify a
hand-maintained `case_map.json`. **This plan supersedes all five.** They stay in
`do-not-execute/` with a superseded banner for provenance.

### Decisions taken up front (agreed with the author)

| Question | Choice |
|---|---|
| CLI shape | Hybrid — top-level `upload` as the CI hot path, thin noun-verb layer for humans |
| Auth | API keys for CI (`X-API-Key`) + `auth login` (JWT) for humans |
| Case matching | `automation_id` first, `title` fallback, unmatched reported not fatal |
| Package location | `cli/` in this repo, its own `pyproject.toml` |

---

## Scope

### In scope

**Backend — API keys**
- `api_keys` table + model + Alembic revision
- `Principal` abstraction so a request can be authenticated by JWT *or* API key
- `GET`/`POST`/`DELETE /api/v1/api-keys` management endpoints

**Backend — result import**
- `POST /api/v1/test-runs/{run_id}/results/import` — multipart JUnit XML or JSON
- `automation_id` → `title` resolution with a full unmatched report in the response
- Batch submit path (one transaction, one aggregate realtime event, one run transition)
- `has_automation_id` filter on `GET /projects/{id}/test-cases`

**CLI — `cli/` package**
- `testoria upload` (`--run` | `--project` + `--create-run`, `--close-on-finish`, `--strict`, `--attach`)
- `testoria auth login|status|logout`, `testoria whoami`
- `testoria run create|list|show|close`
- `testoria case list --project N --unmapped`
- Exit codes: `0` clean, `1` transport/auth/usage, `2` unmatched cases under `--strict`

**CI/CD**
- Validated end-to-end against real `testoria-tests` JUnit output (Phase F)
- Ready-to-paste GitHub Actions / GitLab / Jenkins / k8s recipes in `cli/README.md`

### Out of scope
- **pytest plugin** (`testoria-pytest`) — deferred; the CLI consumes the JUnit XML pytest
  already emits via `--junitxml`, which covers the same use case with no plugin to maintain.
- **PyPI publishing** — deferred to a follow-up; `pip install` from git ref is enough to dogfood.
- **Auto-creating test cases** from unmatched XML entries — a CI run must not mutate the test
  catalogue.
- **TestNG / NUnit / Allure** parsers.
- **Retiring `POST /ci/results/bulk`** — it stays, unchanged and documented, until logs show no
  traffic. New tech-debt item covers removal.
- **Generic project-scope enforcement** on every endpoint — see Risks.

---

## Technical approach

### Part A — API keys

#### Key format and storage

```
tsk_a1b2c3d4_<43 chars of secrets.token_urlsafe(32)>
    ^^^^^^^^ key_prefix (8 hex, unique, indexed, safe to display)
             ^^^^^^^^^^ secret — shown once at mint, never stored
```

Stored: `key_hash = sha256(secret).hexdigest()`.

**Why SHA-256 and not bcrypt.** bcrypt exists to make *low-entropy human-chosen* passwords
expensive to brute-force. This secret is 256 bits from `secrets.token_urlsafe` — there is no
dictionary, and a slow KDF buys nothing while costing ~100 ms of CPU on *every CI request*.
Lookup is `WHERE key_prefix = ?` (unique index, one row) followed by
`secrets.compare_digest(sha256(secret), row.key_hash)` for constant-time comparison.

#### Model — `app/models/api_key.py`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | String(100) not null | human label, e.g. "github-actions-nightly" |
| `key_prefix` | String(16) unique not null, indexed | lookup handle |
| `key_hash` | String(64) not null | sha256 hex of the secret |
| `user_id` | FK `users.id` ondelete RESTRICT, not null, indexed | owning principal |
| `project_id` | FK `projects.id` ondelete CASCADE, nullable, indexed | `NULL` = unscoped |
| `role` | String(50) not null default `tester` | capped — see below |
| `expires_at` | DateTime(tz) nullable | `NULL` = no expiry |
| `last_used_at` | DateTime(tz) nullable | best-effort, throttled write |
| `revoked_at` | DateTime(tz) nullable | soft revoke, keeps the audit trail |
| `created_at` / `updated_at` | DateTime(tz) | matches every other model |

Not `SoftDeleteMixin` — `revoked_at` *is* the soft-delete for this table and carries clearer
meaning. Revoked rows are never purged.

#### Effective role — how an API key cannot escalate

```python
effective_role = min(key.role, owner.role, API_KEY_MAX_ROLE, key=ROLE_HIERARCHY.get)
```

`ROLE_HIERARCHY` already exists (`app/core/roles.py:12`). Three consequences fall out for free:

- `API_KEY_MAX_ROLE = "tester"` (new setting in `app/config.py`) means **no API key can ever
  satisfy `require_role(LEAD, ADMIN)`**. Every user-management, project-delete and
  suite-delete route is closed to API keys without an allowlist to maintain.
- Demoting a user instantly degrades all their keys — the role is recomputed per request, not
  frozen at mint time.
- Minting is self-service for `tester`+ but can never produce a key stronger than its owner.

#### Principal — `app/dependencies.py`

```python
@dataclass(frozen=True)
class Principal:
    user: User
    role: UserRole            # effective role, see above
    project_id: int | None    # None = unscoped
    via: Literal["jwt", "api_key"]
    api_key_id: int | None
```

`get_principal()` accepts **exactly one** of `Authorization: Bearer <jwt>` or `X-API-Key`.
Both present → 400 (ambiguous, never guess). Neither → 401.

The two existing dependencies become thin wrappers, so **every current route keeps working
verbatim and hard invariant 7 stays literally true**:

```python
async def get_current_user(p: Principal = Depends(get_principal)) -> User:
    return p.user

def require_role(*roles: UserRole) -> Callable[..., object]:
    async def checker(p: Principal = Depends(get_principal)) -> User:
        if p.role not in roles:        # effective role, not p.user.role
            raise ForbiddenError()
        return p.user
    return checker
```

`require_jwt` is a third dependency that rejects `via == "api_key"` with 403. It guards the
`/api-keys` routes only: **an API key must never mint or revoke another API key** (that is the
difference between a leaked credential and a persistent foothold).

API-key rejection reasons all return 401 with a distinct detail so the CLI can print something
useful: unknown prefix, hash mismatch, revoked, expired, owner inactive, owner `no_access`.

`last_used_at` is written at most once per 60 s per key (compare-then-update, no row lock) —
it is an operator convenience, not an audit record, and must not cost a write per request.
The audit record is `audit_service.log_action(..., "API_KEY_USED")` on mint/revoke only.

#### Endpoints — `app/api/v1/api_keys.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/api-keys` | JWT, tester+ | returns the plaintext key **once** |
| GET | `/api/v1/api-keys` | JWT, tester+ | own keys; admin sees all via `?user_id=` |
| DELETE | `/api/v1/api-keys/{id}` | JWT, owner or admin | sets `revoked_at`, 204 |

`ApiKeyCreateResponse` is the only schema that carries `key`; `ApiKeyResponse` (used by list)
carries `key_prefix` and never the secret.

### Part B — result import

#### Endpoint

```
POST /api/v1/test-runs/{run_id}/results/import
  multipart: file=<junit.xml|results.json>
  form:      format=auto|junit|json   (default auto)
             strict=false             (default false)
  → 200 ResultImportReport
```

`run_id` moves into the path (the existing endpoint has it as a query param on a multipart
POST, which is why nobody guesses it right). Role: `_TESTER`. If the principal's key is
project-scoped, the run's `project_id` must match or 403.

```python
class UnmatchedCase(BaseModel):
    identifier: str          # "tests.test_login.test_bad_password"
    classname: str | None
    name: str
    status: str              # what it would have been
    reason: Literal["no_match", "ambiguous", "out_of_scope"]

class ResultImportReport(BaseModel):
    run_id: int
    total: int               # <testcase> elements parsed
    matched: int
    submitted: int
    unmatched: int
    unmatched_cases: list[UnmatchedCase]
    matched_by: dict[str, int]   # {"automation_id": 180, "title": 12}
```

`unmatched_cases` is capped at 100 entries (`unmatched` keeps the true count) so a
fully-misconfigured 5000-test run does not return a 2 MB response.

#### Matching — `app/services/result_import_service.py`

One query builds the indexes for the run's scope, reusing
`test_result_service._run_scope_case_ids_subquery(run)` (promote it to a public helper):

```python
select(TestCase.id, TestCase.automation_id, TestCase.title).where(
    TestCase.id.in_(run_scope_case_ids(run)), not_deleted(TestCase)
)
```

#### The node-ID ↔ JUnit impedance mismatch

`automation_id` in the wild is a **pytest node ID**; JUnit XML gives a **dotted classname**.
They differ only in separators, and the node ID → dotted direction is unambiguous:

```python
def dotted(node_id: str) -> str:
    """tests/auth/test_auth.py::TestAuth::test_x -> tests.auth.test_auth.TestAuth.test_x"""
    return node_id.replace(".py::", ".").replace("::", ".").replace("/", ".")
```

The reverse is *not* recoverable (nothing marks where the module path ends and the class
begins), so normalisation happens on the **stored** side at match time, never on the XML. Each
case therefore contributes up to four index keys: `automation_id`, `dotted(automation_id)`,
`title`, `dotted(title)`. It is a pure function over data already in memory — no migration, and
`GET /test-cases?automation_id=` keeps matching on the raw value.

Resolution order per `<testcase classname="C" name="N">`, with `key = f"{C}.{N}"` (or just `N`
when `classname` is absent):

| # | Match against | Catches |
|---|---|---|
| 1 | `automation_id == key` | automation IDs already stored in dotted form |
| 2 | `dotted(automation_id) == key` | **pytest node IDs — the `testoria-tests` case** |
| 3 | `automation_id == N` | short IDs that name the test function only |
| 4 | `title == key` | today's `/ci/results/bulk` behaviour, preserved |
| 5 | `dotted(title) == key` | titles written as node IDs |

A key that resolves to **more than one** case is `reason="ambiguous"` — never a silent
first-wins pick. A case matched outside the run's scope is `reason="out_of_scope"`.
Anything unresolved is `reason="no_match"`. None of these are errors; `strict` is a *client*
concern (the CLI exits 2), so the endpoint always returns 200 with the report.

`matched_by` in the report counts hits per rule, so a pipeline can see at a glance that it is
matching on `title` (fragile) rather than `automation_id` (stable) and fix its seeding.

**Parametrized tests.** `name` carries the param — `test_create_user_all_roles[TESTER]` — so
rule 2 matches a case seeded with
`automation_id="tests/users/test_users.py::TestUsers::test_create_user_all_roles[TESTER]"`.
One case per variant, each with its own independent status. No special handling in the matcher;
it is purely a seeding decision, and it unblocks TD-010 in the tests repo.

Status mapping is unchanged from `ci_service` and stays consistent with feature 009:
`<failure>`/`<error>` → `failed`, `<skipped>` → `no_run`, otherwise `passed`.

`execution_time` uses `round(float(time))` rather than the current `int(float(time))`, which
truncates every sub-second test to `0`. The column is integer seconds so sub-500ms tests still
round to 0 — logged as tech debt, not fixed here (changing the unit is a migration + a
frontend change).

#### Batch submit — `test_result_service.submit_many`

`submit()` is correct but per-call it re-fetches the run, re-fetches the case, reloads the
result with attachments, calls `transition_to_active`, and publishes to Centrifugo. Importing
N results must not do that N times.

`submit_many(db, run_id, items, user_id)`:
- validates the run **once**
- fetches all target cases in one `IN` query
- upserts each result and records history using the *existing*
  `_should_record_history` predicate — semantics stay identical to `submit()`
- calls `transition_to_active` **once**, if any row meaningfully changed
- publishes **one** aggregate `test_result_bulk` event via `realtime_service` carrying
  `{run_id, project_id, submitted, status_counts}` instead of N per-result events

The web client's per-result subscription keeps working; the new event is additive. Web will
need a follow-up to refresh on it — noted as a cross-repo hand-off, not a blocker.

#### Unmapped-case discovery

`GET /projects/{id}/test-cases` gains `has_automation_id: bool | None = None`, translating to
`TestCase.automation_id.is_(None)` / `.is_not(None)` in `test_case_service.list_test_cases`.
Purely additive — `None` preserves today's behaviour. This backs `testoria case list --unmapped`,
which is how a user closes the gap the import report tells them about.

### Part C — the CLI package

```
cli/
├── pyproject.toml            # name=testoria-cli, requires-python>=3.11
├── README.md
├── testoria_cli/
│   ├── __init__.py           # __version__
│   ├── main.py               # typer.Typer(name="testoria")
│   ├── config.py             # ~/.testoria/config.yaml + env resolution
│   ├── client.py             # httpx.Client, auth injection, error mapping
│   ├── errors.py             # CLIError / AuthError / APIError / UnmatchedError
│   ├── output.py             # rich table + --output json
│   ├── parsers/
│   │   ├── junit.py
│   │   └── json_results.py
│   └── commands/
│       ├── upload.py
│       ├── auth.py
│       ├── runs.py
│       └── cases.py
└── tests/unit/
```

Dependencies: `typer`, `httpx`, `rich`, `pyyaml`. Deliberately no `pydantic` — the CLI reads
JSON the API already validated; adding a second schema definition to keep in sync is how
clients drift.

Its own `pyproject.toml`, its own `ruff`/`mypy`/`pytest` invocation. It is **not** importable
from `app/` and must never import from it — the CLI talks HTTP like any other client.

#### Credential resolution

| Source | `url` | `api_key` | `access_token` |
|---|---|---|---|
| CLI flag | `--url` | `--api-key` | — |
| Env | `TESTORIA_URL` | `TESTORIA_API_KEY` | — |
| `~/.testoria/config.yaml` | ✓ | **never** | ✓ (written by `auth login`) |

Precedence: flag > env > config file. **The API key is never persisted to disk** — it comes
from the environment or a flag only, so a CI secret cannot leak into a mounted home directory
or a committed dotfile. `auth login` writes JWTs (which expire) and the config file is created
`0600`.

If both an API key and a stored JWT resolve, the API key wins and the client sends only
`X-API-Key` (the server rejects both headers at once by design).

#### `testoria upload`

```bash
testoria upload junit.xml --run 42
testoria upload junit.xml --project 3 --create-run "CI #$BUILD_NUMBER" --close-on-finish
testoria upload junit.xml --run 42 --strict
testoria upload junit.xml --run 42 --attach 'screenshots/*.png'
```

- `--run` and `--project`/`--create-run` are mutually exclusive; exactly one is required.
- `--create-run NAME` → `POST /projects/{id}/test-runs`, prints the new run id first so a
  failure later is still traceable.
- `--close-on-finish` → `POST /test-runs/{id}/close` after a successful import. Skipped if the
  import produced unmatched cases under `--strict`.
- `--attach GLOB`: each file's **stem must equal the `automation_id`** of a case in this
  upload; matching files go to `POST /test-results/{result_id}/attachments`. Non-matching files
  are listed and skipped, never silently dropped. Deterministic and easy to satisfy from a
  pytest fixture that names artefacts after the nodeid.
- `--output json` emits the raw `ResultImportReport` for pipelines that want to parse it.

Human output:

```
✓ 192 results submitted to run #42 (Sprint 42 — nightly)
  matched: 180 by automation_id, 12 by title
  passed 171  failed 18  no_run 3

⚠ 8 test cases had no match in Testoria:
    tests.test_billing.test_refund_partial      (failed)
    tests.test_billing.test_refund_full         (passed)
    ... 6 more — rerun with --output json for the full list

  Fix with:  testoria case list --project 3 --unmapped
```

Exit codes: `0` clean · `1` transport, auth, or usage error · `2` unmatched cases **and**
`--strict`. `2` is what a pipeline gates on to catch a rename that silently stopped reporting.

#### Reliability

`client.py` retries idempotent `GET`s and connection-level failures 3× with exponential
backoff. The import `POST` is **not** blindly retried — but it is safe to re-run, because
`test_result_service.submit` upserts on `(test_run_id, test_case_id)`. This is stated in the
CLI README so users know a re-run after a network blip is correct, not a double-submit.

---

### Part D — CI/CD integration, worked against `testoria-tests`

This is the acceptance surface for the whole plan: if the CLI cannot be dropped into that
repo's existing `.github/workflows/tests.yml` and its k8s Jobs, it is not done.

#### Step 0 — mint the key (once, by hand)

```bash
curl -sX POST https://api.testoria.gammait.net/api/v1/api-keys \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"name":"testoria-tests-ci","project_id":7,"role":"tester","expires_at":null}'
# -> {"key":"tsk_a1b2c3d4_…"}   shown once
```

Stored as the `TESTORIA_API_KEY` repository secret. Scoped to the automation project, capped at
`tester`, revocable from the UI without touching anyone's password.

#### Step 1 — additive backstop (no behaviour change)

The reporter already writes `reports/testoria_run_id.txt` (`<id>\n<url>`) and
`scripts/ci_summary.py` already reads it. The upload step targets **that same run**, so the two
paths converge instead of creating a second run. Safe because
`test_result_service.submit` upserts on `(test_run_id, test_case_id)` — re-submitting a result
the reporter already posted writes the same values and, thanks to `_should_record_history`,
records **no** history row.

Inserted into both the `api-tests` and `ui-tests` jobs, between "Run … tests" and "Summarise":

```yaml
      - name: Upload results to Testoria
        if: always()
        env:
          TESTORIA_URL: ${{ secrets.FEATURE_API_LINK }}
          TESTORIA_API_KEY: ${{ secrets.TESTORIA_API_KEY }}
        run: |
          pip install 'testoria-cli @ git+https://github.com/gamma-it-solutions/api-testoria@main#subdirectory=cli'
          run_id=$(head -1 reports/testoria_run_id.txt 2>/dev/null || true)
          if [ -z "$run_id" ]; then
            echo "::warning::live reporter recorded no run — creating one from the XML"
            testoria upload reports/junit/api.xml --project 7 \
              --create-run "CI #${{ github.run_number }} api (${{ github.ref_name }})" \
              --close-on-finish
          else
            testoria upload reports/junit/api.xml --run "$run_id"
          fi
```

`if: always()` is the point: it runs when the test step fails, which is exactly when results
matter most. This also repairs the two failure modes the reporter documents — dropped results
after a non-2xx (`CI #52 uploaded 3 results for 4 passing tests`) and runs left `active` by a
killed session — because the XML is written by pytest regardless.

Note `--close-on-finish` is only passed on the fallback path; on the normal path the reporter
still owns closing the run, and closing it twice would race.

#### Step 2 — retire the case map (the real payoff)

Once step 1 shows `matched_by.automation_id` covering the suite, the tests repo can delete:

- `automation_test_cases` from `data/StaticData_{SITE}.json` — the hand-maintained node-ID→case-ID
  map, and the reason a DB reset silently drops every result (their TD-011)
- the `case_map` lookup in `pytest_runtest_logreport`
- the map-writing half of `scripts/seed_test_cases.py` (it keeps setting `automation_id`, which
  is now the *only* linkage)

The workflow collapses to one upload step per job:

```yaml
      - name: Upload results to Testoria
        if: always()
        env:
          TESTORIA_URL: ${{ secrets.FEATURE_API_LINK }}
          TESTORIA_API_KEY: ${{ secrets.TESTORIA_API_KEY }}
        run: |
          testoria upload reports/junit/api.xml \
            --project 7 \
            --create-run "CI #${{ github.run_number }} api (${{ github.ref_name }})" \
            --close-on-finish \
            --attach 'reports/screenshots/*.png' \
            --output json > reports/testoria_upload.json
```

That is the same `RUN_LABEL` naming convention the reporter uses, so run names stay consistent
across the migration. `--strict` is deliberately **not** set here: an unmatched case should not
fail a regression build. It belongs on a dedicated drift-check job (below).

#### Step 3 — a job that catches mapping drift

Unmatched cases are invisible if nothing asserts on them. One extra job turns a silent
regression into a red build:

```yaml
  mapping-drift:
    name: Testoria mapping drift
    needs: [api-tests]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: reports-api, path: reports }
      - name: Fail if any test is unmapped
        env:
          TESTORIA_URL: ${{ secrets.FEATURE_API_LINK }}
          TESTORIA_API_KEY: ${{ secrets.TESTORIA_API_KEY }}
        run: testoria upload reports/junit/api.xml --run "$(head -1 reports/testoria_run_id.txt)" --strict
```

Exit `2` = a test was renamed or added without a matching Testoria case. This is the check that
makes `automation_id` trustworthy as the single linkage.

#### Kubernetes Jobs

`k8s/job-api.yaml` runs the image with `--junitxml=reports/junit/api.xml` onto a PVC, so the
upload is a **sidecar-free second container step**. The Job's `args` cannot express "then run
another command", so this needs a small entrypoint change in the tests repo — either a wrapper
script or a second container in the same Pod sharing the `reports` volume:

```yaml
      containers:
        - name: pytest
          # … unchanged …
        - name: testoria-upload      # runs after pytest exits, shares the PVC
          image: testoria-tests:0.1.0
          command: ["/bin/sh","-c"]
          args:
            - |
              until [ -f /app/reports/junit/api.xml ]; do sleep 5; done
              testoria upload /app/reports/junit/api.xml --run "$(head -1 /app/reports/testoria_run_id.txt)"
          envFrom: [{ secretRef: { name: testoria-credentials } }]
          volumeMounts: [{ name: reports, mountPath: /app/reports }]
```

Flagged as a **cross-repo hand-off**, not work in this plan — the manifest lives in
`testoria-tests`. The API-side requirement it imposes is only that the CLI be installable in
that image and that upload be idempotent, both of which this plan already delivers.

#### GitLab CI / Jenkins

The same two lines, for the CLI README:

```yaml
# .gitlab-ci.yml
test:
  script: [pytest --junitxml=reports/junit.xml]
  after_script:
    - pip install testoria-cli
    - testoria upload reports/junit.xml --project $TESTORIA_PROJECT --create-run "$CI_PIPELINE_ID" --close-on-finish
  variables: { TESTORIA_URL: $TESTORIA_URL }
```

`after_script` in GitLab and `post { always { … } }` in a Jenkins declarative pipeline are the
equivalents of `if: always()` — the README documents that mapping explicitly, because uploading
only on success is the single most common way to lose the results you most wanted.

---

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| config | `app/config.py` | `API_KEY_MAX_ROLE`, `API_KEY_DEFAULT_TTL_DAYS`, `API_KEY_LAST_USED_THROTTLE_SECONDS` |
| models | `app/models/api_key.py` **(new)** | `ApiKey` model |
| models | `app/models/__init__.py` | export `ApiKey` |
| migration | `alembic/versions/` **(new)** | `create table api_keys`; `down_revision = "a1c2e3f40576"` |
| schemas | `app/schemas/api_key.py` **(new)** | `ApiKeyCreate`, `ApiKeyResponse`, `ApiKeyCreateResponse` |
| schemas | `app/schemas/test_result.py` | `UnmatchedCase`, `ResultImportReport` |
| core | `app/core/security.py` | `generate_api_key()`, `hash_api_key()`, `verify_api_key()` |
| deps | `app/dependencies.py` | `Principal`, `get_principal`, `require_jwt`; rewire `get_current_user` + `require_role` |
| services | `app/services/api_key_service.py` **(new)** | mint / list / revoke / `resolve(raw_key)` |
| services | `app/services/result_import_service.py` **(new)** | parse + resolve + report |
| services | `app/services/test_result_service.py` | `submit_many`, public `run_scope_case_ids` |
| services | `app/services/test_case_service.py` | `has_automation_id` filter |
| services | `app/services/realtime_service.py` | `publish_result_bulk` |
| router | `app/api/v1/api_keys.py` **(new)** | key management endpoints |
| router | `app/api/v1/test_results.py` | `POST /test-runs/{run_id}/results/import` |
| router | `app/api/v1/test_cases.py` | `has_automation_id` query param |
| main | `app/main.py` | register `api_keys.router` |
| cli | `cli/**` **(new)** | the package above |
| ci | `.github/workflows/ci.yml` | `cli-check` job — ruff, mypy, pytest in `cli/` |
| tests | `tests/unit/`, `tests/integration/`, `cli/tests/unit/` | see Tasks |

### Key decisions

- **SHA-256 over bcrypt for key hashing.** 256-bit CSPRNG secrets have no dictionary to
  defend against; bcrypt would add ~100 ms to every CI request and prevent an indexed lookup.
  Constant-time comparison via `secrets.compare_digest` still applies.
- **Effective role capped at `tester` by config.** Closes every lead/admin route to API keys
  through the *existing* `require_role` gates rather than a per-route allowlist that will rot.
- **`get_current_user` becomes a wrapper over `get_principal`.** Zero changes at ~60 existing
  call sites, and hard invariant 7 remains literally satisfied.
- **API keys cannot mint API keys.** `require_jwt` on `/api-keys` turns a leaked key from a
  persistent foothold back into a revocable credential.
- **The import endpoint always returns 200 with a report.** `--strict` is a client policy;
  baking it into the API would force every consumer into the same failure taste.
- **Ambiguous matches are reported, never resolved first-wins.** A duplicated `automation_id`
  is a data bug the user must see, not one the importer should paper over.
- **Node-ID normalisation happens on the stored value, not the XML.** `dotted()` is
  unambiguous in that direction only. Verified against pytest 8.3.5 output; without it the
  matcher scores zero against `testoria-tests`, whose `automation_id`s are pytest node IDs.
- **The CLI uploads into the live reporter's existing run** rather than creating a second one,
  reading the `reports/testoria_run_id.txt` the reporter already writes. Upsert semantics make
  the overlap a no-op, so the two paths can coexist during migration instead of double-reporting.
- **`--strict` belongs on a separate drift-check job, not the regression job.** An unmapped test
  is a cataloguing problem; failing the regression build on it trains people to ignore the signal.
- **New endpoint alongside `/ci/results/bulk`, not a rewrite of it.** Feature 009 documents the
  old contract publicly (it is embedded in badges and pipelines); breaking it to save one route
  is not worth it.
- **JUnit XML over a pytest plugin.** pytest already emits JUnit XML with `--junitxml`; a plugin
  is a second integration surface to version and support for the same outcome.
- **The API key never touches disk.** Env/flag only, so `~/.testoria/config.yaml` on a shared
  runner is not a credential store.

---

## Tasks

### Phase A — backend API keys
- [x] Add `API_KEY_MAX_ROLE`, `API_KEY_DEFAULT_TTL_DAYS`, `API_KEY_LAST_USED_THROTTLE_SECONDS` to `app/config.py`
- [x] Write `app/models/api_key.py`; export from `app/models/__init__.py`
- [x] `alembic revision --autogenerate -m "add api_keys"`; review it (unique index on `key_prefix`, FK ondelete rules); `alembic upgrade head`
- [x] Add `generate_api_key` / `hash_api_key` / `verify_api_key` to `app/core/security.py`
- [x] Write `app/schemas/api_key.py`
- [x] Write `app/services/api_key_service.py` — `mint`, `list_for_user`, `revoke`, `resolve`
- [x] Add `Principal`, `get_principal`, `require_jwt` to `app/dependencies.py`; rewire `get_current_user` and `require_role`
- [x] Write `app/api/v1/api_keys.py`; register in `app/main.py`
- [x] Confirm every existing router still compiles and `require_role` call sites are untouched

### Phase B — backend result import
- [x] Promote `_run_scope_case_ids_subquery` to public `run_scope_case_ids` in `test_result_service`
- [x] Add `submit_many` to `app/services/test_result_service.py`
- [x] Add `publish_result_bulk` to `app/services/realtime_service.py`
- [x] Add `UnmatchedCase` + `ResultImportReport` to `app/schemas/test_result.py`
- [x] Write `app/services/result_import_service.py` — JUnit + JSON parsing, 3-step resolution, ambiguity detection
- [x] Add `POST /test-runs/{run_id}/results/import` to `app/api/v1/test_results.py`, with project-scope check for scoped keys
- [x] Add `has_automation_id` filter to `test_case_service.list_test_cases` + `app/api/v1/test_cases.py`

### Phase C — CLI foundation and `upload`
- [x] Create `cli/` with `pyproject.toml` (typer, httpx, rich, pyyaml; `testoria` console script)
- [x] Write `testoria_cli/errors.py`, `config.py` (precedence + `0600` config file), `client.py`, `output.py`
- [x] Write `testoria_cli/parsers/junit.py` and `parsers/json_results.py`
- [x] Write `testoria_cli/commands/upload.py` — `--run`/`--project`+`--create-run`, `--close-on-finish`, `--strict`, `--output`
- [x] Write `testoria_cli/main.py`, wire the app
- [x] Verify `pip install -e cli/` then `testoria --help` and `testoria upload --help`

### Phase D — CLI management commands
- [x] `commands/auth.py` — `login`, `status`, `logout`
- [x] `commands/runs.py` — `create`, `list`, `show`, `close`
- [x] `commands/cases.py` — `list --unmapped`
- [x] `whoami` on the root app (`GET /auth/me`, prints principal + `via` + scope)
- [x] `--attach GLOB` on `upload` (stem == `automation_id`, unmatched files reported)

### Phase E — CI and packaging
- [x] Add a `cli-check` job to `.github/workflows/ci.yml` — `ruff check`, `mypy`, `pytest` inside `cli/`
- [x] Verify `pip install 'testoria-cli @ git+…#subdirectory=cli'` works from a clean venv (this is how CI installs it before PyPI)
- [x] Write `cli/README.md` — install, API key setup, GitHub Actions / GitLab / Jenkins snippets, the `if: always()` rule, exit codes, re-run safety
- [x] Add a superseded banner to the five `do-not-execute/099-cli-*.md` plans pointing here

### Phase F — CI/CD validation against `testoria-tests` (Part D)
- [x] Mint a `tester`-scoped API key for the automation project; store as `TESTORIA_API_KEY`
- [x] Dry-run against a **real** `reports/junit/api.xml` from that suite; confirm `matched_by.automation_id` covers the mapped tests and `matched_by.title` is 0
- [x] Confirm rule 2 (`dotted(automation_id)`) is what matches — if rule 1 or 4 is carrying the suite, the normalisation is wrong
- [x] Verify upload into the reporter's existing run is a true no-op: same result values, **zero** new `result_history` rows
- [x] Verify a killed session (SIGKILL mid-suite) still uploads every completed test from the XML
- [x] Seed one case per variant for a parametrized test; confirm each variant reports independently (unblocks tests-repo TD-010)
- [x] Hand off to `testoria-tests`: done as `plan-008` in that repo. The k8s pattern was implemented as a `command:` wrapper rather than the sidecar sketched here — a second container cannot see when pytest exits, making the wait unbounded. **The k8s Jobs have not been run on a cluster.**

**Cross-repo boundary.** Every file under `testoria-tests` (`tests.yml`, `k8s/*.yaml`,
`conftest.py`, `StaticData_*.json`, `seed_test_cases.py`) is out of scope for this plan and
changes there need their own plan in that repo. Part D is the contract this plan must satisfy,
and the validation above is done from this side using that repo's real artefacts.

### Tests
- [x] `tests/unit/test_api_key_service.py` — mint returns plaintext once; hash is not the plaintext; role capped at owner and at `API_KEY_MAX_ROLE`; resolve rejects revoked / expired / inactive-owner / `no_access`-owner / unknown prefix / wrong secret
- [x] `tests/unit/test_result_import_service.py` — `automation_id` beats `title`; `automation_id` on bare `name`; ambiguous duplicate → `reason="ambiguous"`; out-of-scope case; `<error>` → `failed`; `<skipped>` → `no_run`; no children → `passed`; malformed XML → 400; `unmatched_cases` capped at 100 with a true `unmatched` count
- [x] `tests/unit/test_test_result_service_submit_many.py` — history recorded only on meaningful change (parity with `submit`); `transition_to_active` called once; one bulk publish, not N
- [x] `tests/integration/test_api_keys_api.py` — mint/list/revoke happy path + 401/403/404; **API key cannot call `POST /api-keys`** (403); revoked key → 401
- [x] `tests/integration/test_principal_auth.py` — same route via JWT and via API key; both headers → 400; neither → 401; API key hitting a `require_role(LEAD, ADMIN)` route → 403
- [x] `tests/integration/test_result_import_api.py` — import via JWT and via API key; project-scoped key against a foreign run → 403; report shape; re-import is idempotent (upsert, no duplicate results)
- [x] `tests/integration/test_test_cases_api.py` — `has_automation_id=false` returns only unmapped cases; omitted param preserves current behaviour
- [x] `cli/tests/unit/test_config.py` — flag > env > file precedence; API key never written to disk; config file mode `0600`
- [x] `cli/tests/unit/test_client.py` — `X-API-Key` sent when key present; `Authorization` when only a JWT; never both; 401 → `AuthError`
- [x] `cli/tests/unit/test_parsers.py` — JUnit and JSON fixtures, nested `<testsuites>`, missing `classname`
- [x] `cli/tests/unit/test_upload_command.py` — exit 0 clean; exit 2 on unmatched + `--strict`; exit 0 on unmatched without `--strict`; `--run` and `--create-run` together → usage error; `--close-on-finish` skipped when strict-failed

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `cd cli && ruff check . && mypy testoria_cli && pytest` — clean
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed
- [x] Manual end-to-end: mint a key in the UI → `TESTORIA_API_KEY=… testoria upload junit.xml --run N` → results visible in web

### Docs update
- [x] `docs/06-generated/endpoints.md` — add `/api-keys` ×3, `/test-runs/{id}/results/import`, `has_automation_id` param; verify every row still matches `app/api/v1/*.py`
- [x] `docs/06-generated/db-schema.md` — add `api_keys`
- [x] `docs/02-architecture/ARCHITECTURE.md` — Codemap (`cli/`, new services), "Where is the thing that does X?", Key types (`Principal`)
- [x] `docs/02-architecture/backend/auth.md` — API key section, `Principal`, effective-role rule, `require_jwt`
- [x] `docs/02-architecture/backend/api-layer.md` + `service-layer.md` + `data-layer.md` — new router/services/table
- [x] `docs/01-product/features/009-ci-cd-integration.md` — new import endpoint, `automation_id` matching, CLI
- [x] `docs/01-product/features/011-cli.md` **(new)** — CLI feature doc
- [x] `docs/01-product/index.md` — update the "Testoria CLI (Python tool)" row
- [x] `docs/08-decisions/changelog.md` — record every Key decision above
- [x] `docs/04-execution/tech-debt.md` — resolve *"Auto-link CI runs to test cases via `automation_id`"*; add the four new items in Risks below
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Rewiring `get_current_user`/`require_role` regresses auth on ~60 existing routes | Medium | Wrappers preserve the exact return type and signature; `test_principal_auth.py` asserts JWT parity; the full existing integration suite is the regression net and must pass unchanged |
| Project-scoped keys are only enforced on CLI-facing routes, so a scoped key can still read other projects via `GET /projects` | Medium | Documented explicitly in `auth.md` — scope is a *write* guard on import/run routes, not a read ACL. New tech-debt item: generic scope enforcement. Until then, treat a scoped key as "tester everywhere, writes only here" |
| `submit_many` diverges from `submit` history semantics | Medium | Both call the same `_should_record_history` predicate; a unit test asserts parity on the same input |
| One aggregate realtime event breaks the web live-update UX for CI imports | Medium | The new event is additive; per-result events still fire for UI-driven submits. Cross-repo hand-off to web-testoria to subscribe to `test_result_bulk` — tracked as tech debt, not a blocker |
| Sub-second durations round to 0 because `execution_time` is integer seconds | High | Out of scope to change the unit (migration + frontend). `round()` instead of truncation is strictly better than today. New tech-debt item to move to milliseconds |
| Users mint keys with no expiry and never rotate | Medium | `API_KEY_DEFAULT_TTL_DAYS` defaults to 90 and the mint endpoint requires an explicit `expires_at: null` to opt out; `last_used_at` surfaces stale keys in the list response |
| CLI and API contract drift once the CLI ships separately | Low | CLI lives in this repo; the `cli-check` CI job runs on every backend PR |
| `dotted()` normalisation is pytest/JUnit-specific and will not fit another framework's IDs | Medium | It is one of five rules, not the only one; raw `automation_id` (rule 1) still matches whatever a non-pytest suite stores. `matched_by` makes it visible which rule is carrying a given suite |
| Uploading into the reporter's run double-writes results and inflates `result_history` | Medium | `submit` upserts on `(run_id, case_id)` and `_should_record_history` suppresses no-op writes; Phase F asserts **zero** new history rows explicitly rather than assuming it |
| A dotted `automation_id` and a dotted `title` on different cases collide on the same key | Low | Rules are ordered and `automation_id` wins; a genuine collision within one rule is reported as `ambiguous`, never silently picked |
| `testoria-tests` runs two pytest sessions per workflow (api, ui), so one build makes two runs | High (already true) | Unchanged by this plan — it is existing documented behaviour. Worth pairing with that repo's TD-014 (nothing prunes automation runs) before adding more scheduled uploads |
| The k8s second-container pattern polls for the XML and could hang if pytest dies before writing one | Medium | Cross-repo, but the pattern needs a bounded `until` loop plus the Job's existing `activeDeadlineSeconds`; called out in the hand-off rather than left for whoever implements it |

---

## Definition of done

- [x] `POST /api/v1/api-keys` returns a `tsk_…` key exactly once; it is never retrievable again
- [x] An API key authenticates `POST /test-runs/{id}/results/import` and is rejected (403) by every `require_role(LEAD, ADMIN)` route
- [x] An API key is rejected (403) by `POST /api/v1/api-keys`
- [x] `DELETE /api/v1/api-keys/{id}` makes the key fail with 401 on the next request
- [x] `POST /test-runs/{run_id}/results/import` matches by `automation_id` first, falls back to `title`, and returns an accurate `ResultImportReport` with every unmatched case named
- [x] Importing a 500-case JUnit file issues a bounded number of queries (not O(n) `SELECT`s) and publishes one realtime event
- [x] Re-importing the same file produces the same result rows (upsert, no duplicates) and no spurious history entries
- [x] `GET /projects/{id}/test-cases?has_automation_id=false` returns only unmapped cases; omitting the param is unchanged
- [x] `pip install -e cli/` exposes `testoria`; `testoria --help` lists `upload`, `auth`, `run`, `case`, `whoami`
- [x] `TESTORIA_API_KEY=… testoria upload junit.xml --run 42` submits results and prints the matched/unmatched summary
- [x] `testoria upload junit.xml --project 3 --create-run "CI #12" --close-on-finish` creates the run, imports, and closes it
- [x] `--strict` exits 2 on unmatched cases; without it the same input exits 0
- [x] `testoria case list --project 3 --unmapped` lists cases with no `automation_id`
- [x] The API key is never written to `~/.testoria/config.yaml`; the config file is mode `0600`
- [x] A real `reports/junit/api.xml` from `testoria-tests` uploads with every mapped test matched via `dotted(automation_id)` and `matched_by.title == 0`
- [x] Uploading that XML into the run the live reporter already populated adds **zero** `result_history` rows and changes no result values
- [x] ~~A pytest session killed mid-suite still uploads every test that completed~~ — **disproved**: pytest writes the JUnit XML only at `sessionfinish`, so a SIGKILL leaves nothing to upload. Docs corrected; the live reporter is what covers that case.
- [x] Two variants of one parametrized test report independent statuses against two Testoria cases
- [x] `testoria upload … --strict` exits 2 when a test has been renamed without updating its case
- [x] `pip install 'testoria-cli @ git+…#subdirectory=cli'` succeeds in a clean venv and exposes `testoria`
- [x] Auth and role enforcement tested for both JWT and API-key principals
- [x] Unit test coverage ≥ 85% for `api_key_service` and `result_import_service`
- [x] Integration tests cover happy path + 400/401/403/404 for every new endpoint
- [x] Migration applies cleanly and `alembic downgrade -1` reverses it
- [x] `pytest`, `ruff`, `mypy` clean in both the repo root and `cli/`
- [x] Docs updated per the list above and verified against the implementation
