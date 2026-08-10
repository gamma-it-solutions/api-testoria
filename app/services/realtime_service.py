import logging
from typing import Any

from app.core import centrifugo

logger = logging.getLogger(__name__)


async def publish_result_update(
    run_id: int, project_id: int, result_data: dict[str, Any]
) -> None:
    event = {"type": "test_result", "data": result_data}
    await _safe_publish(f"testrun:{run_id}", event)
    await _safe_publish(f"project:{project_id}", event)


async def publish_result_bulk(
    run_id: int, project_id: int, submitted: int, status_counts: dict[str, int]
) -> None:
    """One aggregate event for a batch import.

    A CI import of N results must not publish N events. Additive: per-result
    `test_result` events still fire for UI-driven submits.
    """
    event = {
        "type": "test_result_bulk",
        "data": {
            "run_id": run_id,
            "submitted": submitted,
            "status_counts": status_counts,
        },
    }
    await _safe_publish(f"testrun:{run_id}", event)
    await _safe_publish(f"project:{project_id}", event)


async def publish_run_status(run_id: int, project_id: int, status: str) -> None:
    event = {"type": "test_run_status", "data": {"run_id": run_id, "status": status}}
    await _safe_publish(f"testrun:{run_id}", event)
    await _safe_publish(f"project:{project_id}", event)


async def publish_case_update(project_id: int, case_id: int, action: str) -> None:
    event = {"type": "test_case_update", "data": {"case_id": case_id, "action": action}}
    await _safe_publish(f"project:{project_id}", event)


async def _safe_publish(channel: str, data: dict[str, Any]) -> None:
    try:
        await centrifugo.publish(channel, data)
    except Exception:
        logger.warning("Centrifugo publish failed — event dropped", exc_info=True)
