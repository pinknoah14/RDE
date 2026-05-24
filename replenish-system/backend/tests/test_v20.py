"""v2.0 신규 기능 회귀 테스트."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import seed_system_config
from app.core.dependencies import get_session
from app.main import app
from app.models.task import (
    ReplenishCandidate,
    ReplenishConfirmedTask,
    ReplenishTaskLocation,
)
from app.models.wave import Wave
from app.models.worker import Worker
from app.models.zone import ZoneConfig
from app.services.slack_service import build_wave_message_v2
from app.services.wave_builder import (
    assign_batch_tags,
    calculate_prestock_cutoff,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_system_config(s)
        s.add(ZoneConfig(
            zone_prefix="RA", zone_name="RA존",
            slack_channel="R존", slack_channel_id="C_RA",
            access_type="FORKLIFT", list_section="MAIN",
        ))
        s.commit()
        yield s


@pytest.fixture()
def client(api_session):
    def _override():
        yield api_session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. 배치 태그 알고리즘
# ---------------------------------------------------------------------------

def test_assign_batch_tags_groups_same_bin():
    """동일 1순위 보충지번을 공유하는 SKU 3개에 batch_tag 부여."""
    candidates = [
        {"candidate_id": 1, "risk_score": 90,
         "matched_bins": [{"replenish_bin": "15RA0801001", "deadline_days": 30}]},
        {"candidate_id": 2, "risk_score": 70,
         "matched_bins": [{"replenish_bin": "15RA0801001", "deadline_days": 45}]},
        {"candidate_id": 3, "risk_score": 50,
         "matched_bins": [{"replenish_bin": "15RA0801001", "deadline_days": 60}]},
    ]
    result = assign_batch_tags(candidates, min_group=2)
    tagged = [c for c in result if c.get("batch_tag")]
    assert len(tagged) == 3
    assert all(c["batch_tag"] == "15RA0801001" for c in tagged)
    seqs = sorted(c["batch_seq"] for c in tagged)
    assert seqs == [1, 2, 3]


def test_assign_batch_tags_fefo_preserved():
    """배치 태그 후처리가 matched_bins(FEFO 정렬)를 변경하지 않음."""
    bins = [
        {"replenish_bin": "15RA0801001", "deadline_days": 30},
        {"replenish_bin": "15RA0802001", "deadline_days": 45},
    ]
    candidates = [
        {"candidate_id": 1, "risk_score": 90, "matched_bins": list(bins)},
        {"candidate_id": 2, "risk_score": 70, "matched_bins": list(bins)},
    ]
    result = assign_batch_tags(candidates, min_group=2)
    for c in result:
        assert c["matched_bins"][0]["deadline_days"] == 30  # FEFO 변경 없음
        assert c["matched_bins"][1]["deadline_days"] == 45


def test_assign_batch_tags_single_no_tag():
    """단독 보충지번은 batch_tag NULL."""
    candidates = [
        {"candidate_id": 1, "risk_score": 90,
         "matched_bins": [{"replenish_bin": "15RA0801001"}]},
        {"candidate_id": 2, "risk_score": 70,
         "matched_bins": [{"replenish_bin": "15RB0501001"}]},
    ]
    result = assign_batch_tags(candidates, min_group=2)
    assert result[0].get("batch_tag") is None
    assert result[1].get("batch_tag") is None


# ---------------------------------------------------------------------------
# 2. 선보충 컷오프
# ---------------------------------------------------------------------------

def test_prestock_cutoff_calculation(api_session):
    """3명 × 12 UPH × (100/60h) = 60개"""
    for name in ("작업자1", "작업자2", "작업자3"):
        api_session.add(Worker(
            worker_name=name, worker_type="FORKLIFT",
            zone_access="[\"RA\"]", max_tasks=6,
            is_active=True, work_type="FORKLIFT",
        ))
    api_session.commit()

    result = calculate_prestock_cutoff(api_session)
    assert result["active_workers"] == 3
    assert result["uph"] == 12
    assert result["minutes"] == 100
    expected = int(3 * 12 * (100 / 60))
    assert result["max_sku"] == expected


def test_prestock_cutoff_no_workers(api_session):
    """활성 작업자 0명 → 기본값 40."""
    result = calculate_prestock_cutoff(api_session)
    assert result["active_workers"] == 0
    assert result["max_sku"] == 40


# ---------------------------------------------------------------------------
# 3. Slack 메시지 v2 형식
# ---------------------------------------------------------------------------

class _MockLoc:
    def __init__(self, replenish_bin):
        self.replenish_bin = replenish_bin


def test_build_wave_message_v2_forklift_format():
    """지게차: (순번). (피킹지번)  (SKU)  (상품명)  (보충지번)"""
    tasks = [
        {"task_id": 1, "picking_bin": "15RA1402201",
         "sku_id": "SKU000123", "sku_name": "마켓오 그래놀라",
         "batch_tag": None, "batch_seq": None},
    ]
    locs = {1: [_MockLoc("15PW0101001")]}
    msgs = build_wave_message_v2(
        tasks, locs, wave_name="W1", channel_label="R존",
        worker_type="FORKLIFT", items_per_msg=6,
    )
    full = "\n".join(msgs)
    assert "15RA1402201" in full
    assert "SKU000123" in full
    assert "마켓오 그래놀라" in full
    assert "15PW0101001" in full
    assert "최대한 보충" in full
    assert "<!here>" in full


def test_build_wave_message_v2_batch_header():
    """배치 태그 있으면 [📦 보충지번] 헤더 포함."""
    tasks = [
        {"task_id": 1, "picking_bin": "15RA1402201",
         "sku_id": "SKU001", "sku_name": "상품A",
         "batch_tag": "15RA0801001", "batch_seq": 1},
        {"task_id": 2, "picking_bin": "15RA0501001",
         "sku_id": "SKU002", "sku_name": "상품B",
         "batch_tag": "15RA0801001", "batch_seq": 2},
    ]
    locs = {1: [_MockLoc("15RA0801001")], 2: [_MockLoc("15RA0801001")]}
    msgs = build_wave_message_v2(
        tasks, locs, wave_name="W1", channel_label="R존",
        worker_type="FORKLIFT", items_per_msg=6,
    )
    full = "\n".join(msgs)
    assert "[📦 15RA0801001]" in full


def test_build_wave_message_v2_split_by_items():
    """6개 초과 시 메시지 분할."""
    tasks = [
        {"task_id": i, "picking_bin": f"15RA{i:04d}001",
         "sku_id": f"SKU{i:05d}", "sku_name": f"상품{i}",
         "batch_tag": None, "batch_seq": None}
        for i in range(1, 10)
    ]
    locs = {t["task_id"]: [_MockLoc("15PW0101001")] for t in tasks}
    msgs = build_wave_message_v2(
        tasks, locs, wave_name="W1", channel_label="R존",
        worker_type="WALKING", items_per_msg=6,
    )
    assert len(msgs) >= 2


def test_build_wave_message_v2_walking_sorted_by_bin():
    """도보: 보충지번(1순위) 기준 정렬."""
    tasks = [
        {"task_id": 1, "picking_bin": "15RA0101001",
         "sku_id": "SKU_A", "sku_name": "A",
         "batch_tag": None, "batch_seq": None},
        {"task_id": 2, "picking_bin": "15RA0201001",
         "sku_id": "SKU_B", "sku_name": "B",
         "batch_tag": None, "batch_seq": None},
    ]
    locs = {1: [_MockLoc("15RB9000001")], 2: [_MockLoc("15RA1000001")]}
    msgs = build_wave_message_v2(
        tasks, locs, wave_name="W1", channel_label="R존",
        worker_type="WALKING", items_per_msg=6,
    )
    full = msgs[0]
    pos_a = full.find("SKU_A")
    pos_b = full.find("SKU_B")
    # SKU_B 보충지번이 알파벳순 앞 → SKU_B가 먼저
    assert pos_b < pos_a


# ---------------------------------------------------------------------------
# 4. PRESTOCK 웨이브
# ---------------------------------------------------------------------------

def test_wave_create_prestock_auto_cutoff(client, api_session):
    """PRESTOCK + max_candidates=None → 동적 컷오프 자동 적용."""
    for name in ("W1", "W2"):
        api_session.add(Worker(
            worker_name=name, worker_type="FORKLIFT",
            zone_access="[\"RA\"]", max_tasks=6,
            is_active=True, work_type="FORKLIFT",
        ))
    api_session.commit()

    res = client.post("/api/v1/waves", json={"wave_type": "PRESTOCK"})
    assert res.status_code == 200, res.text
    body = res.json()
    expected_cutoff = int(2 * 12 * (100 / 60))
    assert body["max_candidates"] == expected_cutoff
    assert body["prestock_cutoff"]["active_workers"] == 2


def test_prestock_cutoff_endpoint(client, api_session):
    """GET /waves/cutoff/prestock 동작."""
    api_session.add(Worker(
        worker_name="W1", worker_type="FORKLIFT",
        zone_access="[\"RA\"]", max_tasks=6,
        is_active=True, work_type="FORKLIFT",
    ))
    api_session.commit()

    res = client.get("/api/v1/waves/cutoff/prestock")
    assert res.status_code == 200
    body = res.json()
    assert body["active_workers"] == 1
    assert body["uph"] == 12


# ---------------------------------------------------------------------------
# 5. Worker work_type / skill_level
# ---------------------------------------------------------------------------

def test_worker_work_type_update(client, api_session):
    """PATCH /workers/{id}/work-type 동작."""
    w = Worker(
        worker_name="홍길동", worker_type="FORKLIFT",
        zone_access="[\"RA\"]", max_tasks=6,
        work_type="FORKLIFT",
    )
    api_session.add(w)
    api_session.commit()
    api_session.refresh(w)

    res = client.patch(
        f"/api/v1/workers/{w.worker_id}/work-type",
        json={"work_type": "WALKING"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["work_type"] == "WALKING"


def test_worker_skill_level_update(client, api_session):
    """PATCH /workers/{id} 로 skill_level 변경."""
    w = Worker(
        worker_name="홍길동", worker_type="FORKLIFT",
        zone_access="[\"RA\"]", max_tasks=6,
    )
    api_session.add(w)
    api_session.commit()
    api_session.refresh(w)

    res = client.patch(
        f"/api/v1/workers/{w.worker_id}",
        json={"skill_level": "JUNIOR"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["skill_level"] == "JUNIOR"
