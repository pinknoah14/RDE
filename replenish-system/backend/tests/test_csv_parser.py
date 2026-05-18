import io
import json

import polars as pl
import pytest

from app.services.csv_parser import (
    classify_inventory,
    detect_multi_picking_bins,
    detect_unknown_zones,
    load_inventory_csv_from_bytes,
)
from app.models.zone import ZoneConfig


# ---------------------------------------------------------------------------
# 헬퍼: 테스트용 CSV 문자열 생성
# ---------------------------------------------------------------------------

HEADER = "상품코드,센터상품명,센터,지번,존,피킹가능,가용수량,입수,박스수,박스잔량,센터 판매마감일,판매마감일수,유통가능일수,입고일자\n"


def make_csv(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows)).encode("utf-8")


def base_row(
    sku="SKU001",
    name="테스트상품",
    center="TC",
    bin_id="15RA0010001",
    zone="RA",
    picking="피킹가능",
    avail=100,
    unit=12,
    boxes=8,
    remainder=4,
    deadline_dt="2026-06-01",
    deadline_days=14,
    expiry_days=60,
    receipt="2026-01-01",
) -> str:
    return f"{sku},{name},{center},{bin_id},{zone},{picking},{avail},{unit},{boxes},{remainder},{deadline_dt},{deadline_days},{expiry_days},{receipt}"


def seed_zone(session, prefix="RA", access_type="FORKLIFT"):
    zone = ZoneConfig(
        zone_prefix=prefix,
        zone_name=f"{prefix}존",
        slack_channel=f"#{prefix.lower()}",
        access_type=access_type,
        list_section="MAIN",
    )
    session.add(zone)
    session.commit()


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

class TestLoadInventoryCsv:
    def test_normal_utf8(self):
        csv_bytes = make_csv(base_row())
        df = load_inventory_csv_from_bytes(csv_bytes)
        assert len(df) == 1
        assert df["상품코드"][0] == "SKU001"

    def test_cp949_encoding(self):
        row = base_row(name="테스트상품")
        content = (HEADER + row).encode("cp949")
        df = load_inventory_csv_from_bytes(content)
        assert len(df) == 1

    def test_column_order_independence(self):
        # 컬럼 순서가 달라도 올바르게 파싱
        alt_header = "센터상품명,상품코드,센터,지번,존,피킹가능,가용수량,입수,박스수,박스잔량,센터 판매마감일,판매마감일수,유통가능일수,입고일자\n"
        row = "테스트상품,SKU001,TC,15RA0010001,RA,피킹가능,100,12,8,4,2026-06-01,14,60,2026-01-01"
        csv_bytes = (alt_header + row).encode("utf-8")
        df = load_inventory_csv_from_bytes(csv_bytes)
        assert df["상품코드"][0] == "SKU001"
        assert df["센터상품명"][0] == "테스트상품"


class TestClassifyInventory:
    def test_picking_replenish_split(self, session):
        seed_zone(session)
        csv_bytes = make_csv(
            base_row(bin_id="15RA0010001", picking="피킹가능"),
            base_row(sku="SKU002", bin_id="15RA0020001", picking="피킹불가", deadline_days=5),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        result = classify_inventory(df, session)

        assert len(result["picking"]) == 1
        assert len(result["replenish"]) == 1

    def test_hold_zone_excluded(self, session):
        """보류존(STOP 패턴) bin_id는 hold로 분류"""
        seed_zone(session)
        csv_bytes = make_csv(
            base_row(bin_id="STOP00001", picking="피킹불가", deadline_days=5),
            base_row(sku="SKU002", bin_id="15RA0010001", picking="피킹가능"),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        result = classify_inventory(df, session)

        assert len(result["hold"]) == 1
        assert result["hold"]["지번"][0] == "STOP00001"

    def test_expired_replenish_filtered(self, session):
        """판매마감일수 <= 0인 보충존 행은 제거"""
        seed_zone(session)
        csv_bytes = make_csv(
            base_row(bin_id="15RA0010001", picking="피킹불가", deadline_days=0),
            base_row(sku="SKU002", bin_id="15RA0020001", picking="피킹불가", deadline_days=-5),
            base_row(sku="SKU003", bin_id="15RA0030001", picking="피킹불가", deadline_days=3),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        result = classify_inventory(df, session)

        assert len(result["replenish"]) == 1
        assert result["replenish"]["상품코드"][0] == "SKU003"

    def test_mixed_lot_not_aggregated(self, session):
        """혼적 처리: 동일 bin_id + 다른 판매마감일수 → 행 분리 유지, groupby 금지"""
        seed_zone(session)
        csv_bytes = make_csv(
            base_row(bin_id="15RA0010001", picking="피킹불가", deadline_days=10, deadline_dt="2026-05-28"),
            base_row(bin_id="15RA0010001", picking="피킹불가", deadline_days=20, deadline_dt="2026-06-07"),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        result = classify_inventory(df, session)

        # groupby로 합산되지 않고 2행이 유지되어야 함
        assert len(result["replenish"]) == 2
        # 판매마감일수 ASC 정렬 확인
        assert result["replenish"]["판매마감일수"][0] == 10

    def test_replenish_sorted_by_deadline_asc(self, session):
        """보충존은 판매마감일수 ASC 정렬"""
        seed_zone(session)
        csv_bytes = make_csv(
            base_row(sku="SKU_B", bin_id="15RA0020001", picking="피킹불가", deadline_days=30),
            base_row(sku="SKU_A", bin_id="15RA0010001", picking="피킹불가", deadline_days=5),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        result = classify_inventory(df, session)

        assert result["replenish"]["상품코드"][0] == "SKU_A"


class TestDetectUnknownZones:
    def test_unknown_zone_detected(self, session):
        """zone_config에 없는 존 prefix → unknown_zone_flags 등록"""
        csv_bytes = make_csv(
            base_row(bin_id="15XX0010001"),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        unknowns = detect_unknown_zones(df, session)

        assert any(u["zone_prefix"] == "XX" for u in unknowns)

    def test_known_zone_not_flagged(self, session):
        seed_zone(session, prefix="RA")
        csv_bytes = make_csv(base_row(bin_id="15RA0010001"))
        df = load_inventory_csv_from_bytes(csv_bytes)
        unknowns = detect_unknown_zones(df, session)

        assert not any(u["zone_prefix"] == "RA" for u in unknowns)

    def test_upsert_increments_seen_count(self, session):
        """같은 미등록 존을 두 번 감지하면 seen_count 증가"""
        csv_bytes = make_csv(base_row(bin_id="15ZZ0010001"))
        df = load_inventory_csv_from_bytes(csv_bytes)

        detect_unknown_zones(df, session)
        detect_unknown_zones(df, session)

        from sqlmodel import select
        from app.models.zone import UnknownZoneFlag
        flag = session.exec(
            select(UnknownZoneFlag).where(UnknownZoneFlag.zone_prefix == "ZZ")
        ).first()
        assert flag is not None
        assert flag.seen_count == 2


class TestDetectMultiPickingBins:
    def test_multi_bin_detected(self, session):
        """동일 SKU가 피킹존에 2개 지번으로 등장 → has_multi_bin=True"""
        from app.models.sku import SkuPickingHistory
        from app.services.csv_parser import update_picking_history

        csv_bytes = make_csv(
            base_row(sku="SKU_MULTI", bin_id="15RA0010001", picking="피킹가능"),
        )
        df = load_inventory_csv_from_bytes(csv_bytes)
        update_picking_history(df, session)

        # 2번째 지번으로 추가
        picking_df = pl.DataFrame({
            "상품코드":    ["SKU_MULTI", "SKU_MULTI"],
            "센터":        ["TC", "TC"],
            "지번":        ["15RA0010001", "15RA0010002"],
            "피킹가능":    ["피킹가능", "피킹가능"],
            "가용수량":    [50, 30],
        })
        multi = detect_multi_picking_bins(picking_df, session)

        assert len(multi) == 1

        from sqlmodel import select
        history = session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id == "SKU_MULTI"
            )
        ).first()
        assert history is not None
        assert history.has_multi_bin is True
        alt = json.loads(history.alt_bin_ids)
        assert len(alt) == 2

    def test_single_bin_not_flagged(self, session):
        picking_df = pl.DataFrame({
            "상품코드": ["SKU_SINGLE"],
            "센터":     ["TC"],
            "지번":     ["15RA0010001"],
            "피킹가능": ["피킹가능"],
            "가용수량": [100],
        })
        multi = detect_multi_picking_bins(picking_df, session)
        assert len(multi) == 0
