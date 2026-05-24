"""v2.1 신규 기능 회귀 테스트.

- 배치 그룹 절단 방지
- weight_unassigned description API 노출
- gen_snapshot force_shortage_count 주입
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.database import seed_system_config
from app.core.dependencies import get_session
from app.main import app
from app.services.slack_service import (
    _chunk_preserving_batches,
    _count_real_items,
    build_wave_message_v2,
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
# 1. 배치 그룹 절단 방지
# ---------------------------------------------------------------------------

def test_batch_group_not_split_across_messages():
    """동일 batch_tag 그룹이 다른 메시지로 분리되지 않음."""
    # 4건 단독 + 배치 3건 → 6 items_per_msg 기준 4 / 3 분할
    groups = [
        ["1. b1  S1  상품1  loc1"],
        ["2. b2  S2  상품2  loc2"],
        ["3. b3  S3  상품3  loc3"],
        ["4. b4  S4  상품4  loc4"],
        [
            "[📦 15RA0801001]",
            "  5. b5  S5  상품5  15RA0801001",
            "  6. b6  S6  상품6  15RA0801001",
            "  7. b7  S7  상품7  15RA0801001",
        ],
    ]
    chunks = _chunk_preserving_batches(groups, items_per_msg=6)
    assert len(chunks) == 2
    # 1번 메시지: 4건, 2번 메시지: 배치 3건 + 헤더
    assert _count_real_items(chunks[0]) == 4
    assert _count_real_items(chunks[1]) == 3
    assert "[📦 15RA0801001]" in chunks[1]


def test_batch_group_fits_exactly():
    """현재 3 + 다음 그룹 3 = 정확히 6 → 한 메시지에 들어감."""
    groups = [
        ["1. b1  S1  상품1  loc1"],
        ["2. b2  S2  상품2  loc2"],
        ["3. b3  S3  상품3  loc3"],
        [
            "[📦 15RA0801001]",
            "  4. b4  S4  상품4  15RA0801001",
            "  5. b5  S5  상품5  15RA0801001",
            "  6. b6  S6  상품6  15RA0801001",
        ],
    ]
    chunks = _chunk_preserving_batches(groups, items_per_msg=6)
    # 3 + 3 = 6 ≤ 6 → 한 메시지
    assert len(chunks) == 1
    assert _count_real_items(chunks[0]) == 6


def test_batch_group_oversized_splits():
    """단일 그룹이 items_per_msg × 2 초과 → 예외적 분할 허용."""
    # 헤더 + 14개 항목 (items_per_msg=6 → 12개 초과)
    big_group = [["[📦 15RA0801001]"] + [f"  {i}. b{i} S{i} 상품{i} loc" for i in range(1, 15)]]
    chunks = _chunk_preserving_batches(big_group, items_per_msg=6)
    # 예외 분할되어 1개 이상의 청크 발생
    assert len(chunks) >= 1
    total_items = sum(_count_real_items(c) for c in chunks)
    assert total_items == 14


def test_build_wave_message_v2_no_split_within_batch():
    """build_wave_message_v2의 메시지 분할이 배치를 찢지 않음."""
    tasks = [
        {"task_id": 1, "picking_bin": "15RA0101001", "sku_id": "S1", "sku_name": "상품1",
         "batch_tag": None, "batch_seq": None},
        {"task_id": 2, "picking_bin": "15RA0102001", "sku_id": "S2", "sku_name": "상품2",
         "batch_tag": None, "batch_seq": None},
        {"task_id": 3, "picking_bin": "15RA0103001", "sku_id": "S3", "sku_name": "상품3",
         "batch_tag": None, "batch_seq": None},
        {"task_id": 4, "picking_bin": "15RA0104001", "sku_id": "S4", "sku_name": "상품4",
         "batch_tag": None, "batch_seq": None},
        {"task_id": 5, "picking_bin": "15RA0105001", "sku_id": "S5", "sku_name": "상품5",
         "batch_tag": "15RA0801001", "batch_seq": 1},
        {"task_id": 6, "picking_bin": "15RA0106001", "sku_id": "S6", "sku_name": "상품6",
         "batch_tag": "15RA0801001", "batch_seq": 2},
        {"task_id": 7, "picking_bin": "15RA0107001", "sku_id": "S7", "sku_name": "상품7",
         "batch_tag": "15RA0801001", "batch_seq": 3},
    ]
    locs = {i: [{"replenish_bin": "15RA0801001"}] for i in range(1, 8)}
    msgs = build_wave_message_v2(
        tasks=tasks, locations_map=locs,
        wave_name="웨이브1", channel_label="R존",
        worker_type="FORKLIFT", items_per_msg=6,
    )
    # 배치 그룹이 통째로 한 메시지 안에 있어야 함
    batch_msg_count = sum(1 for m in msgs if "[📦 15RA0801001]" in m)
    assert batch_msg_count == 1, f"배치 헤더가 {batch_msg_count}개 메시지에 출현 (1 기대)"


# ---------------------------------------------------------------------------
# 2. weight_unassigned description
# ---------------------------------------------------------------------------

def test_weight_unassigned_description_in_api(client):
    """GET /system-config 응답에 weight_unassigned description 포함."""
    r = client.get("/api/v1/system-config")
    assert r.status_code == 200
    configs = r.json()
    wa = next((c for c in configs if c["config_key"] == "weight_unassigned"), None)
    assert wa, "weight_unassigned config 없음"
    assert wa.get("description"), "description 비어있음"
    assert "+25" in wa["description"], "조정 가이드(+25) 미포함"
    assert "현장 조정 가이드" in wa["description"]


# ---------------------------------------------------------------------------
# 3. force_shortage_count 주입
# ---------------------------------------------------------------------------

def test_force_shortage_injection(tmp_path, monkeypatch):
    """gen_snapshot force_shortage_count=104 시 상위 104개 SKU의 피킹존 행 미포함."""
    import sys
    from pathlib import Path

    # OUTDIR을 tmp_path로 변경
    sim_path = Path(__file__).parent / "oneday"
    sys.path.insert(0, str(sim_path.parent.parent))

    from tests.oneday import generate_oneday_data as g
    monkeypatch.setattr(g, "OUTDIR", tmp_path)

    # force_shortage=0 (정상)
    path0, rows0 = g.gen_snapshot("test_normal", 0.0, force_shortage_count=0)
    # force_shortage=104
    path1, rows1 = g.gen_snapshot("test_shortage", 0.0, force_shortage_count=104)

    import polars as pl
    df0 = pl.read_csv(str(path0))
    df1 = pl.read_csv(str(path1))

    # 상위 104개 SKU의 피킹가능 행이 사라져야 함
    top_skus = [f"SKU{i:05d}" for i in range(104)]
    pick0 = df0.filter(
        (pl.col("피킹가능") == "피킹가능") & (pl.col("상품코드").is_in(top_skus))
    ).height
    pick1 = df1.filter(
        (pl.col("피킹가능") == "피킹가능") & (pl.col("상품코드").is_in(top_skus))
    ).height
    # force_shortage 적용 후 상위 104개 피킹 행이 정상 대비 크게 감소
    assert pick1 < pick0, f"force_shortage 적용 안됨 (normal={pick0}, forced={pick1})"
    assert pick1 == 0, f"104개 강제 품절 후에도 피킹 행 {pick1}개 남음"
