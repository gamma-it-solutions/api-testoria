# Execution Plan: 011 — Backend Phase 7: Defect Tracking Integration

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: MEDIUM
**Dependency**: 014-be-phase3-test-execution must be complete

---

## Goal

Let testers create defects in external trackers (Jira, GitHub Issues, GitLab Issues) directly from a failed test result, with the defect reference stored on the TestResult record.

---

## Context

Phase 7 of `backend-implementation.md`. When a test fails, a tester should be able to click "Create Defect" in the UI and have the backend call the external tracker API. The created issue URL and key are stored in `test_results.defects` (JSONB list).

---

## Scope

### In scope
- `app/services/defect_service.py` — Jira, GitHub, GitLab issue creation via httpx
- `app/schemas/defect.py` — DefectCreate, JiraDefectCreate, GitHubDefectCreate, GitLabDefectCreate, DefectResponse
- `app/api/v1/defects.py` — POST /defects/jira, /defects/github, /defects/gitlab
- `tests/integration/test_defects_api.py` — mocked external calls

### Out of scope
- Bidirectional sync (pulling defect status back from tracker)
- OAuth flow for trackers (credentials supplied per-request in body)
- Defect management UI for configuring tracker credentials per project

---

## Technical approach

### Endpoints

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| POST | `/defects/jira` | tester | Create Jira issue, link to test_result |
| POST | `/defects/github` | tester | Create GitHub issue, link to test_result |
| POST | `/defects/gitlab` | tester | Create GitLab issue, link to test_result |

### Request body (Jira example)

```python
class JiraDefectCreate(BaseModel):
    test_result_id: int
    jira_url: str           # e.g. https://company.atlassian.net
    jira_username: str
    jira_api_token: str
    project_key: str        # e.g. "BUG"
    summary: str
    description: str
```

### After successful creation

Append to `test_result.defects` JSONB:

```python
defect_ref = {
    "tracker": "jira",
    "key": issue["key"],       # e.g. "BUG-123"
    "url": issue["self"],
    "summary": summary,
}
# Load current defects list, append, save
result = await db.get(TestResult, data.test_result_id)
defects = result.defects or []
defects.append(defect_ref)
result.defects = defects
await db.flush()
```

### DefectService structure

```python
class DefectService:
    @staticmethod
    async def create_jira_issue(jira_url, username, api_token, project_key, summary, description) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{jira_url}/rest/api/2/issue",
                json={"fields": {"project": {"key": project_key}, "summary": summary,
                                 "description": description, "issuetype": {"name": "Bug"}}},
                auth=(username, api_token),
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    async def create_github_issue(repo_owner, repo_name, token, title, body) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues",
                json={"title": title, "body": body},
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    async def create_gitlab_issue(gitlab_url, project_id, token, title, description) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{gitlab_url}/api/v4/projects/{project_id}/issues",
                json={"title": title, "description": description},
                headers={"PRIVATE-TOKEN": token},
            )
            response.raise_for_status()
            return response.json()
```

### Error handling

External tracker calls are wrapped in try/except:
- Network error → 502 Bad Gateway with detail
- 401 from tracker → 422 Unprocessable Entity ("Invalid tracker credentials")
- 404 from tracker → 422 ("Project/repo not found in tracker")

---

## Tasks

### Schemas
- [ ] Write `app/schemas/defect.py` — JiraDefectCreate, GitHubDefectCreate, GitLabDefectCreate, DefectResponse

### Service
- [ ] Write `app/services/defect_service.py` — create_jira_issue, create_github_issue, create_gitlab_issue

### Router
- [ ] Write `app/api/v1/defects.py` — all 3 POST endpoints, each calls service + appends defect ref to TestResult
- [ ] Register `defects.router` with prefix `/api/v1/defects` in `app/main.py`

### Tests
- [ ] `tests/integration/test_defects_api.py`:
  - POST `/defects/github` with mocked httpx → 201, defect ref stored on TestResult
  - POST `/defects/jira` with mocked httpx → 201, defect ref stored
  - External tracker returns 401 → endpoint returns 422 with meaningful message
  - 401 without auth

### Quality check
- [ ] `pytest` passes (mock external HTTP with `respx` or `pytest-mock`)
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `api/docs/06-generated/endpoints.md` — verify defect tracking rows
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `POST /defects/jira` creates a Jira issue and stores `{tracker, key, url}` in `test_results.defects`
- [ ] `POST /defects/github` creates a GitHub issue and stores the reference
- [ ] `POST /defects/gitlab` creates a GitLab issue and stores the reference
- [ ] Invalid credentials from external tracker returns 422, not 500
- [ ] Network timeout returns 502
- [ ] 401 without Testoria auth
- [ ] Integration tests pass (external calls mocked)
