from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.milestone import Milestone
from app.models.mixins import not_deleted
from app.models.project import Project
from app.schemas.milestone import MilestoneCreate, MilestoneUpdate


async def _get_project(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, not_deleted(Project))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def get_milestone(
    db: AsyncSession, milestone_id: int, allow_deleted: bool = False
) -> Milestone:
    query = select(Milestone).where(Milestone.id == milestone_id)
    if not allow_deleted:
        query = query.where(not_deleted(Milestone))
    result = await db.execute(query)
    ms = result.scalar_one_or_none()
    if ms is None:
        raise NotFoundError(f"Milestone {milestone_id} not found")
    return ms


async def list_milestones(
    db: AsyncSession, project_id: int, include_deleted: bool = False
) -> list[Milestone]:
    await _get_project(db, project_id)
    query = select(Milestone).where(Milestone.project_id == project_id)
    if not include_deleted:
        query = query.where(not_deleted(Milestone))
    result = await db.execute(
        query.order_by(
            Milestone.target_date.asc().nulls_last(), Milestone.created_at.asc()
        )
    )
    return list(result.scalars().all())


async def create_milestone(
    db: AsyncSession, project_id: int, data: MilestoneCreate
) -> Milestone:
    await _get_project(db, project_id)
    ms = Milestone(
        project_id=project_id,
        name=data.name,
        description=data.description,
        target_date=data.target_date,
    )
    db.add(ms)
    await db.flush()
    await db.refresh(ms)
    return ms


async def update_milestone(
    db: AsyncSession, milestone_id: int, data: MilestoneUpdate
) -> Milestone:
    ms = await get_milestone(db, milestone_id)
    if data.name is not None:
        ms.name = data.name
    if data.description is not None:
        ms.description = data.description
    if "target_date" in data.model_fields_set:
        ms.target_date = data.target_date
    if data.is_completed is not None:
        ms.is_completed = data.is_completed
    await db.flush()
    await db.refresh(ms)
    return ms


async def delete_milestone(db: AsyncSession, milestone_id: int) -> None:
    ms = await get_milestone(db, milestone_id)
    ms.deleted_at = func.now()
    await db.flush()


async def restore_milestone(db: AsyncSession, milestone_id: int) -> Milestone:
    ms = await get_milestone(db, milestone_id, allow_deleted=True)
    if ms.deleted_at is None:
        raise BadRequestError(f"Milestone {milestone_id} is not deleted")

    project_result = await db.execute(
        select(Project).where(Project.id == ms.project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise BadRequestError("Cannot restore milestone: parent project is deleted")

    ms.deleted_at = None
    await db.flush()
    await db.refresh(ms)
    return ms
