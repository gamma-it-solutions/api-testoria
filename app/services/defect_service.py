import logging

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.test_result import TestResult
from app.schemas.defect import (
    DefectResponse,
    GitHubDefectCreate,
    GitLabDefectCreate,
    JiraDefectCreate,
)

logger = logging.getLogger(__name__)


async def _get_result(db: AsyncSession, result_id: int) -> TestResult:
    row = await db.execute(select(TestResult).where(TestResult.id == result_id))
    tr = row.scalar_one_or_none()
    if tr is None:
        raise NotFoundError(f"TestResult {result_id} not found")
    return tr


async def _append_defect(db: AsyncSession, result_id: int, ref: dict[str, str]) -> None:
    tr = await _get_result(db, result_id)
    defects: list[dict[str, str]] = list(tr.defects or [])
    defects.append(ref)
    tr.defects = defects
    await db.flush()
    await db.refresh(tr)


def _handle_tracker_error(exc: httpx.HTTPStatusError) -> None:
    """Translate tracker HTTP errors to Testoria HTTP exceptions."""
    if exc.response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid tracker credentials",
        )
    if exc.response.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Project/repo not found in tracker",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Tracker returned unexpected status {exc.response.status_code}",
    )


class DefectService:
    """Service for creating defects in external trackers."""

    @staticmethod
    async def create_jira(db: AsyncSession, data: JiraDefectCreate) -> DefectResponse:
        """Create a Jira issue and link it to a TestResult.

        Args:
            db: Active database session.
            data: Validated Jira defect creation schema.

        Returns:
            DefectResponse with tracker reference details.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{data.jira_url}/rest/api/2/issue",
                    json={
                        "fields": {
                            "project": {"key": data.project_key},
                            "summary": data.summary,
                            "description": data.description,
                            "issuetype": {"name": "Bug"},
                        }
                    },
                    auth=(data.jira_username, data.jira_api_token),
                )
                response.raise_for_status()
                issue = response.json()
        except httpx.HTTPStatusError as exc:
            _handle_tracker_error(exc)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error contacting Jira: {exc}",
            ) from exc

        ref: dict[str, str] = {
            "tracker": "jira",
            "key": issue["key"],
            "url": issue["self"],
            "summary": data.summary,
        }
        await _append_defect(db, data.test_result_id, ref)
        return DefectResponse(**ref)

    @staticmethod
    async def create_github(
        db: AsyncSession, data: GitHubDefectCreate
    ) -> DefectResponse:
        """Create a GitHub issue and link it to a TestResult.

        Args:
            db: Active database session.
            data: Validated GitHub defect creation schema.

        Returns:
            DefectResponse with tracker reference details.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://api.github.com/repos/{data.repo_owner}/{data.repo_name}/issues",
                    json={"title": data.title, "body": data.body},
                    headers={
                        "Authorization": f"token {data.token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                response.raise_for_status()
                issue = response.json()
        except httpx.HTTPStatusError as exc:
            _handle_tracker_error(exc)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error contacting GitHub: {exc}",
            ) from exc

        ref = {
            "tracker": "github",
            "key": str(issue["number"]),
            "url": issue["html_url"],
            "summary": data.title,
        }
        await _append_defect(db, data.test_result_id, ref)
        return DefectResponse(**ref)

    @staticmethod
    async def create_gitlab(
        db: AsyncSession, data: GitLabDefectCreate
    ) -> DefectResponse:
        """Create a GitLab issue and link it to a TestResult.

        Args:
            db: Active database session.
            data: Validated GitLab defect creation schema.

        Returns:
            DefectResponse with tracker reference details.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{data.gitlab_url}/api/v4/projects/{data.project_id}/issues",
                    json={"title": data.title, "description": data.description},
                    headers={"PRIVATE-TOKEN": data.token},
                )
                response.raise_for_status()
                issue = response.json()
        except httpx.HTTPStatusError as exc:
            _handle_tracker_error(exc)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Network error contacting GitLab: {exc}",
            ) from exc

        ref = {
            "tracker": "gitlab",
            "key": str(issue["iid"]),
            "url": issue["web_url"],
            "summary": data.title,
        }
        await _append_defect(db, data.test_result_id, ref)
        return DefectResponse(**ref)
