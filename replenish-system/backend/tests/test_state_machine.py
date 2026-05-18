import pytest
from sqlmodel import select

from app.models.task import ReplenishConfirmedTask, ReplenishCandidate, ReplenishTaskQueue
from app.models.wave import Wave
from app.models.audit import AuditLog
from app.services.state_machine import (
    InvalidTransitionError,
    transition_task,
    transition_candidate,
    VALID_TASK_TRANSITIONS,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def make_wave(session) -> Wave:
    wave = Wave(
        wave_name="테스트웨이브",
        wave_type="REGULAR",
        wave_status="CONFIRMED",
        target_sku_count=10,
        created_by="관리자",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)
    return wave


def make_task(session, wave_id: int, status: str = "READY") -> ReplenishConfirmedTask:
    task = ReplenishConfirmedTask(
        wave_id=wave_id,
        sku_id="SKU001",
        sku_name="테스트상품",
        picking_bin="15RA0010001",
        zone="RA",
        slack_channel="R존",
        worker_type="FORKLIFT",
        total_qty=24,
        confirm_type="AUTO",
        confirmed_by="관리자",
        task_status=status,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def make_candidate(session, wave_id: int, status: str = "PENDING") -> ReplenishCandidate:
    candidate = ReplenishCandidate(
        wave_id=wave_id,
        sku_id="SKU001",
        sku_name="테스트상품",
        picking_bin="15RA0010001",
        zone="RA",
        slack_channel="R존",
        risk_score=75.0,
        risk_level="HIGH",
        recommended_qty=24,
        candidate_status=status,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Task 상태 전이 테스트
# ---------------------------------------------------------------------------

class TestTransitionTask:
    def test_ready_to_queued(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "READY")
        updated = transition_task(task.task_id, "QUEUED", actor="관리자", session=session)
        assert updated.task_status == "QUEUED"

    def test_queued_to_sent(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "QUEUED")
        updated = transition_task(task.task_id, "SENT", actor="관리자", session=session)
        assert updated.task_status == "SENT"
        assert updated.sent_at is not None

    def test_sent_to_done(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "SENT")
        updated = transition_task(task.task_id, "DONE", actor="관리자", session=session)
        assert updated.task_status == "DONE"
        assert updated.done_at is not None

    def test_sent_to_blocked(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "SENT")
        updated = transition_task(
            task.task_id, "BLOCKED",
            actor="관리자",
            block_reason="통로 막힘",
            shortage_qty=12,
            session=session,
        )
        assert updated.task_status == "BLOCKED"
        assert updated.block_reason == "통로 막힘"
        assert updated.shortage_qty == 12

    def test_blocked_shortage_defaults_to_total_qty(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "SENT")
        updated = transition_task(
            task.task_id, "BLOCKED",
            actor="관리자",
            block_reason="없음",
            session=session,
        )
        assert updated.shortage_qty == task.total_qty

    def test_blocked_to_sent_retry(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "BLOCKED")
        updated = transition_task(task.task_id, "SENT", actor="관리자", session=session)
        assert updated.task_status == "SENT"

    def test_cancelled_with_reason(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "SENT")
        updated = transition_task(
            task.task_id, "CANCELLED",
            actor="관리자",
            cancel_reason="관리자 취소",
            session=session,
        )
        assert updated.task_status == "CANCELLED"
        assert updated.cancel_reason == "관리자 취소"
        assert updated.cancelled_at is not None

    def test_invalid_transition_raises(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "DONE")
        with pytest.raises(InvalidTransitionError):
            transition_task(task.task_id, "QUEUED", actor="관리자", session=session)

    def test_done_to_any_raises(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "DONE")
        for target in ["READY", "QUEUED", "SENT", "BLOCKED", "CANCELLED"]:
            with pytest.raises(InvalidTransitionError):
                transition_task(task.task_id, target, actor="관리자", session=session)

    def test_cancelled_to_any_raises(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "CANCELLED")
        with pytest.raises(InvalidTransitionError):
            transition_task(task.task_id, "READY", actor="관리자", session=session)

    def test_audit_log_written(self, session):
        wave = make_wave(session)
        task = make_task(session, wave.wave_id, "READY")
        transition_task(task.task_id, "QUEUED", actor="관리자", session=session)

        logs = session.exec(
            select(AuditLog).where(
                AuditLog.entity_type == "task",
                AuditLog.entity_id == task.task_id,
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].action == "status_change"
        assert logs[0].actor == "관리자"
        assert "READY" in logs[0].before_json
        assert "QUEUED" in logs[0].after_json

    def test_all_valid_transitions_covered(self):
        """VALID_TASK_TRANSITIONS의 모든 전이가 정의되어 있는지 확인"""
        all_statuses = {"READY", "QUEUED", "SENT", "DONE", "BLOCKED", "CANCELLED"}
        assert set(VALID_TASK_TRANSITIONS.keys()) == all_statuses


# ---------------------------------------------------------------------------
# Candidate 상태 전이 테스트
# ---------------------------------------------------------------------------

class TestTransitionCandidate:
    def test_pending_to_approved(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "PENDING")
        updated = transition_candidate(candidate.candidate_id, "APPROVED", actor="관리자", session=session)
        assert updated.candidate_status == "APPROVED"

    def test_pending_to_rejected(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "PENDING")
        updated = transition_candidate(
            candidate.candidate_id, "REJECTED",
            actor="관리자",
            rejected_reason="재고 과잉",
            session=session,
        )
        assert updated.candidate_status == "REJECTED"
        assert updated.rejected_reason == "재고 과잉"

    def test_pending_to_modified(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "PENDING")
        updated = transition_candidate(
            candidate.candidate_id, "MODIFIED",
            actor="관리자",
            modified_qty=12,
            session=session,
        )
        assert updated.candidate_status == "MODIFIED"
        assert updated.modified_qty == 12

    def test_modified_to_approved(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "MODIFIED")
        updated = transition_candidate(candidate.candidate_id, "APPROVED", actor="관리자", session=session)
        assert updated.candidate_status == "APPROVED"

    def test_approved_to_any_raises(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "APPROVED")
        with pytest.raises(InvalidTransitionError):
            transition_candidate(candidate.candidate_id, "PENDING", actor="관리자", session=session)

    def test_audit_log_written_for_candidate(self, session):
        wave = make_wave(session)
        candidate = make_candidate(session, wave.wave_id, "PENDING")
        transition_candidate(candidate.candidate_id, "APPROVED", actor="관리자", session=session)

        logs = session.exec(
            select(AuditLog).where(
                AuditLog.entity_type == "candidate",
                AuditLog.entity_id == candidate.candidate_id,
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].action == "status_change"
