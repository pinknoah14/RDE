"""GAP-04b 완료 이중검증 (verification_service.py) 테스트.

DONE 처리된 태스크 중 피킹재고가 여전히 0인 불일치 건 탐지를 검증한다.
- 재고 충분(가용>0) → 불일치 아님
- 재고 0 또는 피킹이력 없음 → 불일치
- wave_id 필터, 비-DONE 태스크 제외
- verify_done_tasks 라우트 (404 포함)
"""
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401
from app.api.waves import verify_done_tasks
from app.core.exceptions import RDEException
from app.models.sku import SkuPickingHistory
from app.models.task import ReplenishConfirmedTask
from app.models.wave import Wave
from app.services.verification_service import detect_done_mismatches


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_wave(db):
    wave = Wave(
        wave_name="검증웨이브", wave_type="REGULAR", wave_status="COMPLETED",
        target_sku_count=5, created_by="테스트",
    )
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


def _add_task(db, wave_id, *, sku, status="DONE"):
    task = ReplenishConfirmedTask(
        wave_id=wave_id, sku_id=sku, sku_name=f"상품_{sku}",
        picking_bin="RA-01-01", zone="RA", slack_channel="R존",
        worker_type="FORKLIFT", total_qty=10, confirm_type="AUTO",
        confirmed_by="테스트", task_status=status,
        done_at=datetime.utcnow() if status == "DONE" else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _set_picking(db, sku, qty, center_cd="GGH1"):
    db.add(SkuPickingHistory(
        sku_id=sku, center_cd=center_cd, picking_bin="RA-01-01",
        zone="RA", last_seen_qty=qty,
    ))
    db.commit()


# ────────────────────────────────────────────────────────────────
# detect_done_mismatches
# ────────────────────────────────────────────────────────────────

def test_no_done_tasks_returns_empty(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", status="READY")
    assert detect_done_mismatches("GGH1", db) == []


def test_done_with_stock_is_not_mismatch(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", status="DONE")
    _set_picking(db, "A", qty=50)  # 재고 충분 → 정상
    assert detect_done_mismatches("GGH1", db) == []


def test_done_with_zero_stock_is_mismatch(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", status="DONE")
    _set_picking(db, "A", qty=0)  # 완료했는데 재고 0 → 불일치
    result = detect_done_mismatches("GGH1", db)
    assert len(result) == 1
    assert result[0]["sku_id"] == "A"
    assert result[0]["current_picking_qty"] == 0
    assert result[0]["done_at"] is not None


def test_done_with_no_picking_history_is_mismatch(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="GHOST", status="DONE")
    # 피킹 이력 자체가 없음 → 불일치
    result = detect_done_mismatches("GGH1", db)
    assert len(result) == 1
    assert result[0]["sku_id"] == "GHOST"
    assert result[0]["current_picking_qty"] == 0


def test_only_done_tasks_considered(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="DONE_ZERO", status="DONE")
    _add_task(db, wave.wave_id, sku="READY_ZERO", status="READY")
    _set_picking(db, "DONE_ZERO", qty=0)
    _set_picking(db, "READY_ZERO", qty=0)
    result = detect_done_mismatches("GGH1", db)
    # READY는 검증 대상 아님
    skus = {m["sku_id"] for m in result}
    assert skus == {"DONE_ZERO"}


def test_wave_id_filter(db):
    wave1 = _make_wave(db)
    wave2 = _make_wave(db)
    _add_task(db, wave1.wave_id, sku="W1", status="DONE")
    _add_task(db, wave2.wave_id, sku="W2", status="DONE")
    _set_picking(db, "W1", qty=0)
    _set_picking(db, "W2", qty=0)

    only_w1 = detect_done_mismatches("GGH1", db, wave_id=wave1.wave_id)
    assert {m["sku_id"] for m in only_w1} == {"W1"}

    all_waves = detect_done_mismatches("GGH1", db)
    assert {m["sku_id"] for m in all_waves} == {"W1", "W2"}


def test_mixed_stock_levels(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="OK", status="DONE")
    _add_task(db, wave.wave_id, sku="BAD", status="DONE")
    _set_picking(db, "OK", qty=100)
    _set_picking(db, "BAD", qty=0)
    result = detect_done_mismatches("GGH1", db)
    assert {m["sku_id"] for m in result} == {"BAD"}


# ────────────────────────────────────────────────────────────────
# verify_done_tasks 라우트
# ────────────────────────────────────────────────────────────────

def test_verify_route_returns_mismatches(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", status="DONE")
    _set_picking(db, "A", qty=0)
    result = verify_done_tasks(wave_id=wave.wave_id, center_cd="GGH1", session=db)
    assert result["wave_id"] == wave.wave_id
    assert result["mismatch_count"] == 1
    assert result["mismatches"][0]["sku_id"] == "A"


def test_verify_route_404_on_missing_wave(db):
    with pytest.raises(RDEException) as exc:
        verify_done_tasks(wave_id=99999, center_cd="GGH1", session=db)
    assert exc.value.status_code == 404
