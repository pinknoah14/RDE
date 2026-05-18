from datetime import datetime
from sqlmodel import Session

from app.models.task import ReplenishConfirmedTask, ReplenishCandidate
from app.services.audit_service import write_audit_log


VALID_TASK_TRANSITIONS: dict[str, list[str]] = {
    "READY":     ["QUEUED", "CANCELLED"],
    "QUEUED":    ["SENT", "CANCELLED"],
    "SENT":      ["DONE", "BLOCKED", "CANCELLED"],
    "BLOCKED":   ["SENT", "CANCELLED"],
    "DONE":      [],
    "CANCELLED": [],
}

VALID_CANDIDATE_TRANSITIONS: dict[str, list[str]] = {
    "PENDING":  ["APPROVED", "REJECTED", "MODIFIED"],
    "MODIFIED": ["APPROVED", "REJECTED"],
    "APPROVED": [],
    "REJECTED": [],
}


class InvalidTransitionError(ValueError):
    pass


def transition_task(
    task_id: int,
    target_status: str,
    actor: str,
    block_reason: str | None = None,
    shortage_qty: int | None = None,
    cancel_reason: str | None = None,
    session: Session | None = None,
) -> ReplenishConfirmedTask:
    if session is None:
        raise ValueError("session은 필수입니다")

    task = session.get(ReplenishConfirmedTask, task_id)
    if task is None:
        raise ValueError(f"Task 없음: {task_id}")

    allowed = VALID_TASK_TRANSITIONS.get(task.task_status, [])
    if target_status not in allowed:
        raise InvalidTransitionError(
            f"전이 불가: {task.task_status} → {target_status}"
        )

    before = _task_snapshot(task)

    task.task_status = target_status

    if target_status == "BLOCKED":
        task.block_reason = block_reason
        task.shortage_qty = shortage_qty if shortage_qty is not None else task.total_qty
    elif target_status == "SENT":
        task.sent_at = datetime.utcnow()
    elif target_status == "DONE":
        task.done_at = datetime.utcnow()
    elif target_status == "CANCELLED":
        task.cancelled_at = datetime.utcnow()
        task.cancel_reason = cancel_reason

    write_audit_log(
        entity_type="task",
        entity_id=task_id,
        action="status_change",
        actor=actor,
        before=before,
        after=_task_snapshot(task),
        session=session,
    )
    session.commit()
    session.refresh(task)
    return task


def transition_candidate(
    candidate_id: int,
    target_status: str,
    actor: str,
    modified_qty: int | None = None,
    rejected_reason: str | None = None,
    session: Session | None = None,
) -> ReplenishCandidate:
    if session is None:
        raise ValueError("session은 필수입니다")

    candidate = session.get(ReplenishCandidate, candidate_id)
    if candidate is None:
        raise ValueError(f"Candidate 없음: {candidate_id}")

    allowed = VALID_CANDIDATE_TRANSITIONS.get(candidate.candidate_status, [])
    if target_status not in allowed:
        raise InvalidTransitionError(
            f"전이 불가: {candidate.candidate_status} → {target_status}"
        )

    before = _candidate_snapshot(candidate)

    candidate.candidate_status = target_status
    candidate.updated_at = datetime.utcnow()

    if target_status == "MODIFIED" and modified_qty is not None:
        candidate.modified_qty = modified_qty
    if target_status == "REJECTED" and rejected_reason is not None:
        candidate.rejected_reason = rejected_reason

    write_audit_log(
        entity_type="candidate",
        entity_id=candidate_id,
        action="status_change",
        actor=actor,
        before=before,
        after=_candidate_snapshot(candidate),
        session=session,
    )
    session.commit()
    session.refresh(candidate)
    return candidate


def _task_snapshot(task: ReplenishConfirmedTask) -> dict:
    return {
        "task_id": task.task_id,
        "task_status": task.task_status,
        "block_reason": task.block_reason,
        "shortage_qty": task.shortage_qty,
        "sent_at": str(task.sent_at) if task.sent_at else None,
        "done_at": str(task.done_at) if task.done_at else None,
        "cancelled_at": str(task.cancelled_at) if task.cancelled_at else None,
        "cancel_reason": task.cancel_reason,
    }


def _candidate_snapshot(candidate: ReplenishCandidate) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_status": candidate.candidate_status,
        "modified_qty": candidate.modified_qty,
        "rejected_reason": candidate.rejected_reason,
        "updated_at": str(candidate.updated_at) if candidate.updated_at else None,
    }
