"""waves.py 미검증 경로 보강 — distribute_wave_tasks (GAP-07) 중심.

핸들러 함수를 직접 호출(세션 주입)하여 검증:
- distribute: 웨이브 없음 404, 태스크 없음, 작업자 수 기준 분할,
  section_size 기준 분할, 작업자 없음 기본 6개, JUNIOR 소량 우선 배정
- print_wave 라우트 → HTML 반환
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.models  # noqa: F401
from app.api.waves import (
    DistributeRequest,
    UrgentWaveRequest,
    create_urgent_wave_from_dashboard,
    distribute_wave_tasks,
    print_wave,
)
from app.core.config import invalidate_cache
from app.core.database import seed_system_config
from app.core.exceptions import RDEException
from app.models.task import ReplenishConfirmedTask
from app.models.wave import Wave
from app.models.worker import Worker


@pytest.fixture
def db():
    invalidate_cache()
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        s.commit()
        yield s
    invalidate_cache()


def _make_wave(db, status="CONFIRMED"):
    wave = Wave(
        wave_name="배분테스트", wave_type="REGULAR", wave_status=status,
        target_sku_count=5, created_by="테스트",
    )
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


def _add_task(db, wave_id, *, sku, qty=10, channel="R존", wtype="FORKLIFT"):
    task = ReplenishConfirmedTask(
        wave_id=wave_id, sku_id=sku, sku_name=f"상품_{sku}",
        picking_bin="RA-01-01", zone="RA", slack_channel=channel,
        worker_type=wtype, total_qty=qty, confirm_type="AUTO",
        confirmed_by="테스트", task_status="READY",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _add_worker(db, name, *, skill="NORMAL", work_type="FORKLIFT", active=True):
    w = Worker(
        worker_name=name, worker_type=work_type, zone_access='["RA"]',
        skill_level=skill, work_type=work_type, is_active=active,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ────────────────────────────────────────────────────────────────
# distribute — 경계 케이스
# ────────────────────────────────────────────────────────────────

def test_distribute_wave_not_found(db):
    with pytest.raises(RDEException) as exc:
        distribute_wave_tasks(wave_id=99999, body=DistributeRequest(), session=db)
    assert exc.value.status_code == 404


def test_distribute_no_tasks(db):
    wave = _make_wave(db)
    result = distribute_wave_tasks(wave_id=wave.wave_id, body=DistributeRequest(), session=db)
    assert result == {"assigned": 0, "sections": {}}


def test_distribute_by_worker_count(db):
    wave = _make_wave(db)
    for i in range(6):
        _add_task(db, wave.wave_id, sku=f"S{i}", qty=10 + i)
    _add_worker(db, "작업자1")
    _add_worker(db, "작업자2")

    result = distribute_wave_tasks(wave_id=wave.wave_id, body=DistributeRequest(), session=db)
    assert result["assigned"] == 6
    # 작업자 2명 → 2섹션
    assert result["sections"]["R존_FORKLIFT"] == 2

    tasks = db.exec(select(ReplenishConfirmedTask)).all()
    section_seqs = {t.section_seq for t in tasks}
    assert section_seqs == {1, 2}
    # 각 섹션에 worker_id 배정됨
    assert all(t.worker_id is not None for t in tasks)


def test_distribute_by_section_size(db):
    wave = _make_wave(db)
    for i in range(10):
        _add_task(db, wave.wave_id, sku=f"S{i}")
    # 작업자 없이 section_size=3 → ceil(10/3)=4 섹션
    result = distribute_wave_tasks(
        wave_id=wave.wave_id, body=DistributeRequest(section_size=3), session=db
    )
    assert result["assigned"] == 10
    assert result["sections"]["R존_FORKLIFT"] == 4


def test_distribute_no_workers_default_block_of_6(db):
    wave = _make_wave(db)
    for i in range(13):
        _add_task(db, wave.wave_id, sku=f"S{i}")
    # 작업자 없음, section_size 미지정 → 6개씩 → ceil(13/6)=3 섹션
    result = distribute_wave_tasks(wave_id=wave.wave_id, body=DistributeRequest(), session=db)
    assert result["assigned"] == 13
    assert result["sections"]["R존_FORKLIFT"] == 3
    # 작업자 없으니 worker_id 미배정
    tasks = db.exec(select(ReplenishConfirmedTask)).all()
    assert all(t.worker_id is None for t in tasks)


def test_distribute_junior_gets_small_qty_first(db):
    """GAP-07: JUNIOR 작업자가 소량 태스크가 모인 섹션 1을 받는다."""
    wave = _make_wave(db)
    # 큰 수량과 작은 수량 섞어서 생성
    _add_task(db, wave.wave_id, sku="BIG1", qty=100)
    _add_task(db, wave.wave_id, sku="BIG2", qty=90)
    _add_task(db, wave.wave_id, sku="SMALL1", qty=5)
    _add_task(db, wave.wave_id, sku="SMALL2", qty=3)

    junior = _add_worker(db, "신입", skill="JUNIOR")
    expert = _add_worker(db, "숙련", skill="EXPERT")

    distribute_wave_tasks(wave_id=wave.wave_id, body=DistributeRequest(), session=db)

    # 작업자 순서 [junior, expert] → 섹션1=JUNIOR(소량), 섹션2=EXPERT(대량)
    tasks = {t.sku_id: t for t in db.exec(select(ReplenishConfirmedTask)).all()}
    # 소량 태스크는 JUNIOR(섹션1)에 배정
    assert tasks["SMALL1"].worker_id == junior.worker_id
    assert tasks["SMALL2"].worker_id == junior.worker_id
    # 대량 태스크는 EXPERT(섹션2)에 배정
    assert tasks["BIG1"].worker_id == expert.worker_id
    assert tasks["BIG2"].worker_id == expert.worker_id


def test_distribute_separates_by_channel_and_type(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", channel="R존", wtype="FORKLIFT")
    _add_task(db, wave.wave_id, sku="B", channel="P존", wtype="WALKING")
    result = distribute_wave_tasks(wave_id=wave.wave_id, body=DistributeRequest(), session=db)
    assert result["assigned"] == 2
    # 채널·타입별로 별도 섹션 그룹
    assert "R존_FORKLIFT" in result["sections"]
    assert "P존_WALKING" in result["sections"]


# ────────────────────────────────────────────────────────────────
# print_wave 라우트
# ────────────────────────────────────────────────────────────────

def test_print_wave_route_returns_html(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A")
    html = print_wave(wave_id=wave.wave_id, session=db)
    assert "<!DOCTYPE html>" in html
    assert "배분테스트" in html


def test_print_wave_route_missing_wave(db):
    html = print_wave(wave_id=99999, session=db)
    assert "웨이브를 찾을 수 없습니다" in html


# ────────────────────────────────────────────────────────────────
# create_urgent_wave_from_dashboard
# ────────────────────────────────────────────────────────────────

def test_urgent_wave_no_candidates(db):
    """재고/판매 데이터 없음 → 후보 0건 → 미확정 웨이브 반환."""
    result = create_urgent_wave_from_dashboard(
        body=UrgentWaveRequest(center_cd="GGH1", min_risk_level="CRITICAL"),
        session=db,
    )
    assert result["candidates"] == 0
    assert result["confirmed"] is False
    assert result["tasks_created"] == 0
    # URGENT 웨이브가 DRAFT로 생성됨
    wave = db.get(Wave, result["wave_id"])
    assert wave is not None
    assert wave.wave_type == "URGENT"


def test_urgent_wave_high_risk_level_filter(db):
    """min_risk_level=HIGH → CRITICAL+HIGH 후보 유지 대상 (데이터 없으면 0건)."""
    result = create_urgent_wave_from_dashboard(
        body=UrgentWaveRequest(center_cd="GGH1", min_risk_level="HIGH"),
        session=db,
    )
    assert result["candidates"] == 0
    assert "algorithm" in result
    assert result["algorithm"]["total"] == 0
