"""GAP-05 인쇄용 웨이브 HTML 생성 (print_service.py) 회귀 테스트.

서버 다운 시 현장 fallback 수단이므로 다음 경로를 검증한다:
- 웨이브 없음 → 안내 HTML
- 태스크 없음 → 헤더만, 섹션 없음
- 단일/다중 채널, 섹션 분할, 작업자 라벨
- 보충지번(locations) 렌더링 / 미존재 시 "-"
- CANCELLED 태스크 제외
- wave_type / wave_status 한글 매핑
"""
import app.models  # noqa: F401 — 모델 등록
from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.wave import Wave
from app.models.worker import Worker
from app.services.print_service import generate_print_html

from sqlmodel import Session, SQLModel, create_engine

import pytest


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_wave(db, name="테스트웨이브", wave_type="REGULAR", status="CONFIRMED"):
    wave = Wave(
        wave_name=name,
        wave_type=wave_type,
        wave_status=status,
        target_sku_count=5,
        created_by="테스트",
    )
    db.add(wave)
    db.commit()
    db.refresh(wave)
    return wave


def _add_task(
    db, wave_id, *, sku="SKU0", channel="R존", section_seq=0,
    status="READY", worker_id=None, qty=10, list_section="MAIN",
):
    task = ReplenishConfirmedTask(
        wave_id=wave_id,
        sku_id=sku,
        sku_name=f"상품_{sku}",
        picking_bin="RA-01-01",
        zone="RA",
        slack_channel=channel,
        list_section=list_section,
        section_seq=section_seq,
        worker_type="FORKLIFT",
        total_qty=qty,
        confirm_type="AUTO",
        confirmed_by="테스트",
        task_status=status,
        worker_id=worker_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _add_location(db, task_id, *, seq=1, bin_id="RB-09-01", qty=20):
    loc = ReplenishTaskLocation(
        task_id=task_id,
        seq=seq,
        replenish_bin=bin_id,
        allocated_qty=qty,
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


def _add_worker(db, name="홍길동", skill="NORMAL"):
    w = Worker(
        worker_name=name,
        worker_type="FORKLIFT",
        zone_access='["RA"]',
        skill_level=skill,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ────────────────────────────────────────────────────────────────
# 예외/경계 케이스
# ────────────────────────────────────────────────────────────────

def test_missing_wave_returns_notice(db):
    html = generate_print_html(99999, db)
    assert "웨이브를 찾을 수 없습니다" in html
    assert "<!DOCTYPE html>" in html


def test_empty_wave_renders_header_only(db):
    wave = _make_wave(db, name="빈웨이브")
    html = generate_print_html(wave.wave_id, db)
    assert "빈웨이브" in html
    assert "총 0건" in html
    # 섹션(<h2>)이 없어야 함
    assert "<h2>" not in html


# ────────────────────────────────────────────────────────────────
# 정상 렌더링
# ────────────────────────────────────────────────────────────────

def test_single_channel_renders_table(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="SKU1", channel="R존")
    html = generate_print_html(wave.wave_id, db)
    assert "<h2>R존</h2>" in html
    assert "SKU1" in html
    assert "상품_SKU1" in html
    assert "총 1건" in html
    # 인쇄 버튼 + 완료 체크박스
    assert "window.print()" in html
    assert "☐" in html


def test_multiple_channels_sorted(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", channel="P존")
    _add_task(db, wave.wave_id, sku="B", channel="R존")
    html = generate_print_html(wave.wave_id, db)
    # 채널은 정렬되어 P존이 R존보다 먼저 등장
    assert html.index("<h2>P존</h2>") < html.index("<h2>R존</h2>")


def test_section_label_with_worker(db):
    wave = _make_wave(db)
    worker = _add_worker(db, name="김작업")
    _add_task(db, wave.wave_id, sku="A", channel="R존",
              section_seq=1, worker_id=worker.worker_id)
    html = generate_print_html(wave.wave_id, db)
    assert "섹션 1" in html
    assert "김작업" in html


def test_section_zero_has_no_h3_label(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A", channel="R존", section_seq=0)
    html = generate_print_html(wave.wave_id, db)
    # section_seq=0은 섹션 라벨(<h3>) 미생성
    assert "<h3>" not in html


def test_locations_rendered(db):
    wave = _make_wave(db)
    task = _add_task(db, wave.wave_id, sku="A")
    _add_location(db, task.task_id, seq=1, bin_id="RB-09-01", qty=20)
    _add_location(db, task.task_id, seq=2, bin_id="RB-09-02", qty=5)
    html = generate_print_html(wave.wave_id, db)
    assert "RB-09-01" in html
    assert "(20개)" in html
    assert "RB-09-02" in html
    assert "(5개)" in html


def test_task_without_location_shows_dash(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="A")
    html = generate_print_html(wave.wave_id, db)
    # 보충지번 셀이 "-"로 표기 (td.nb 안)
    assert "<td class='nb'>-</td>" in html


def test_cancelled_task_excluded(db):
    wave = _make_wave(db)
    _add_task(db, wave.wave_id, sku="ALIVE", status="READY")
    _add_task(db, wave.wave_id, sku="DEAD", status="CANCELLED")
    html = generate_print_html(wave.wave_id, db)
    assert "ALIVE" in html
    assert "DEAD" not in html
    assert "총 1건" in html


# ────────────────────────────────────────────────────────────────
# 한글 매핑
# ────────────────────────────────────────────────────────────────

def test_wave_type_korean_mapping(db):
    wave = _make_wave(db, wave_type="URGENT", status="SENT")
    _add_task(db, wave.wave_id, sku="A")
    html = generate_print_html(wave.wave_id, db)
    assert "유형: 긴급" in html
    assert "상태: 전송됨" in html


def test_prestock_type_mapping(db):
    wave = _make_wave(db, wave_type="PRESTOCK", status="COMPLETED")
    _add_task(db, wave.wave_id, sku="A")
    html = generate_print_html(wave.wave_id, db)
    assert "유형: 선보충" in html
    assert "상태: 완료" in html


def test_unknown_type_falls_back_to_raw(db):
    wave = _make_wave(db, wave_type="CUSTOM", status="WEIRD")
    _add_task(db, wave.wave_id, sku="A")
    html = generate_print_html(wave.wave_id, db)
    # 매핑에 없으면 원문 그대로 노출
    assert "유형: CUSTOM" in html
    assert "상태: WEIRD" in html
