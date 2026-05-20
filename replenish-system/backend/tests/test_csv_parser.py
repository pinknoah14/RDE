import io
import json

import polars as pl
import pytest

from app.services.csv_parser import (
    REQUIRED_COLUMNS,
    classify_inventory,
    detect_multi_picking_bins,
    detect_unknown_zones,
    extract_zone_prefix,
    load_inventory_csv_from_bytes,
    parse_bin_id,
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
    from sqlmodel import select
    existing = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)).first()
    if existing:
        return
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
    def _run(self, session, picking_bytes=None, replenish_bytes=None):
        """헬퍼: picking_df / replenish_df 를 만들어 detect_unknown_zones 호출"""
        picking_df = (
            load_inventory_csv_from_bytes(picking_bytes)
            if picking_bytes is not None
            else pl.DataFrame({col: [] for col in REQUIRED_COLUMNS})
        )
        replenish_df = (
            load_inventory_csv_from_bytes(replenish_bytes)
            if replenish_bytes is not None
            else pl.DataFrame({col: [] for col in REQUIRED_COLUMNS})
        )
        return detect_unknown_zones(picking_df, replenish_df, session)

    def test_unknown_zone_detected(self, session):
        """피킹존 bin_id의 존 prefix가 zone_config에 없으면 unknown_zone_flags 등록"""
        csv_bytes = make_csv(base_row(bin_id="15XX0010001", picking="피킹가능"))
        unknowns = self._run(session, picking_bytes=csv_bytes)
        assert any(u["zone_prefix"] == "XX" for u in unknowns)

    def test_known_zone_not_flagged(self, session):
        seed_zone(session, prefix="RA")
        csv_bytes = make_csv(base_row(bin_id="15RA0010001", picking="피킹가능"))
        unknowns = self._run(session, picking_bytes=csv_bytes)
        assert not any(u["zone_prefix"] == "RA" for u in unknowns)

    def test_upsert_increments_seen_count(self, session):
        """같은 미등록 존을 두 번 감지하면 seen_count 증가"""
        csv_bytes = make_csv(base_row(bin_id="15ZZ0010001", picking="피킹가능"))

        self._run(session, picking_bytes=csv_bytes)
        self._run(session, picking_bytes=csv_bytes)

        from sqlmodel import select
        from app.models.zone import UnknownZoneFlag
        flag = session.exec(
            select(UnknownZoneFlag).where(UnknownZoneFlag.zone_prefix == "ZZ")
        ).first()
        assert flag is not None
        assert flag.seen_count == 2

    def test_hold_zone_bin_not_flagged(self, session):
        """보류존 bin(STOP* 등)은 unknown zone 경고를 생성하지 않아야 한다 (Bug 2 회귀)"""
        # STOP00001 은 bin_id_pattern 미매칭 → classify_inventory에서 hold로 분류
        # detect_unknown_zones는 picking/replenish만 받으므로 "ST" prefix가 나타나지 않음
        picking_bytes = make_csv(base_row(bin_id="15RA0010001", picking="피킹가능"))
        unknowns = self._run(session, picking_bytes=picking_bytes)
        # 보류존 prefix("ST", "PK" 등)가 포함되지 않아야 함
        hold_like = [u for u in unknowns if u["zone_prefix"] in ("ST", "PK", "LQ")]
        assert hold_like == []


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

    def test_first_upload_multi_bin_detected(self, session):
        """
        첫 업로드 시 동일 SKU가 2개 지번으로 나타나는 경우에도 has_multi_bin 설정 확인.
        (Bug 1 회귀: update_picking_history → detect_multi_picking_bins 순서 보장)
        """
        from app.models.sku import SkuPickingHistory
        from app.services.csv_parser import update_picking_history
        from sqlmodel import select

        picking_df = pl.DataFrame({
            "상품코드":    ["SKU_NEW", "SKU_NEW"],
            "센터":        ["TC", "TC"],
            "지번":        ["15RA0010001", "15RA0010002"],
            "피킹가능":    ["피킹가능", "피킹가능"],
            "가용수량":    [50, 30],
        })

        # 올바른 호출 순서: update_picking_history 먼저, detect_multi_picking_bins 나중
        update_picking_history(picking_df, session)
        multi = detect_multi_picking_bins(picking_df, session)

        assert len(multi) == 1
        history = session.exec(
            select(SkuPickingHistory).where(SkuPickingHistory.sku_id == "SKU_NEW")
        ).first()
        assert history is not None
        assert history.has_multi_bin is True


# ---------------------------------------------------------------------------
# 샘플 CSV 파일 기반 통합 테스트
# ---------------------------------------------------------------------------

class TestSampleCsvFile:
    def test_load_file_no_exception(self, sample_csv_path):
        from app.services.csv_parser import load_inventory_csv
        df = load_inventory_csv(str(sample_csv_path))
        assert df is not None
        assert len(df) > 0

    def test_required_columns_present(self, sample_csv_path):
        from app.services.csv_parser import load_inventory_csv
        df = load_inventory_csv(str(sample_csv_path))
        for col in REQUIRED_COLUMNS:
            assert col in df.columns, f"누락 컬럼: {col}"

    def test_excluded_columns_absent(self, sample_csv_path):
        from app.services.csv_parser import load_inventory_csv
        df = load_inventory_csv(str(sample_csv_path))
        assert "이동중" not in df.columns
        assert "입고중" not in df.columns

    def test_row_count(self, sample_csv_path):
        from app.services.csv_parser import load_inventory_csv
        df = load_inventory_csv(str(sample_csv_path))
        assert len(df) == 12

    def test_classify_picking_skus(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)

        picking_skus = set(result["picking"]["상품코드"].to_list())
        assert "SKU001" in picking_skus
        assert "SKU002" in picking_skus
        assert "SKU003" in picking_skus
        assert "SKU005" in picking_skus

    def test_classify_sku005_multi_bin(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)

        sku005_rows = result["picking"].filter(pl.col("상품코드") == "SKU005")
        assert len(sku005_rows) == 2

    def test_classify_replenish_fefo(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)

        sku001_rep = result["replenish"].filter(pl.col("상품코드") == "SKU001")
        assert len(sku001_rep) == 2
        days = sku001_rep["판매마감일수"].to_list()
        assert days[0] == 20
        assert days[1] == 45

    def test_classify_expired_filtered(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)

        rep_skus = set(result["replenish"]["상품코드"].to_list())
        assert "SKU004" not in rep_skus

    def test_classify_hold_zones(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)

        hold_bins = set(result["hold"]["지번"].to_list())
        assert "PKMOVE01" in hold_bins
        assert "RT0001234" in hold_bins

    def test_detect_unknown_zone_zz(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory, detect_unknown_zones
        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)
        unknowns = detect_unknown_zones(result["picking"], result["replenish"], session)

        prefixes = {u["zone_prefix"] for u in unknowns}
        assert "ZZ" in prefixes
        assert "RA" not in prefixes
        assert "RB" not in prefixes

    def test_update_picking_history_confidence(self, sample_csv_path, session):
        from app.services.csv_parser import load_inventory_csv, classify_inventory, update_picking_history
        from app.models.sku import SkuPickingHistory
        from sqlmodel import select
        from datetime import date

        df = load_inventory_csv(str(sample_csv_path))
        result = classify_inventory(df, session)
        update_picking_history(result["picking"], session)

        h = session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id == "SKU001",
                SkuPickingHistory.center_cd == "GGH1",
            )
        ).first()
        assert h is not None
        assert h.last_seen_date == date.today()
        assert h.confidence == "NEW"


# ---------------------------------------------------------------------------
# v1.7: parse_bin_id / extract_zone_prefix 단위 테스트
# ---------------------------------------------------------------------------

class TestParseBinId:
    def test_standard_bin(self):
        result = parse_bin_id("15RA0101001")
        assert result == {"temp": "15", "zone": "RA", "aisle": 1, "bay": 1, "level": 1}

    def test_pw_zone(self):
        result = parse_bin_id("15PW0301001")
        assert result is not None
        assert result["zone"] == "PW"
        assert result["aisle"] == 3
        assert result["bay"] == 1

    def test_large_values(self):
        # 15 RA 14 02 201
        result = parse_bin_id("15RA1402201")
        assert result == {"temp": "15", "zone": "RA", "aisle": 14, "bay": 2, "level": 201}

    def test_invalid_hold_bin(self):
        assert parse_bin_id("PKMOVE01")   is None
        assert parse_bin_id("STOP00001")  is None
        assert parse_bin_id("RT0001234")  is None

    def test_empty_and_none(self):
        assert parse_bin_id("")   is None
        assert parse_bin_id(None) is None  # type: ignore[arg-type]

    def test_short_string(self):
        assert parse_bin_id("15RA") is None


class TestExtractZonePrefix:
    def test_ra_zone(self):
        assert extract_zone_prefix("15RA1402201") == "RA"

    def test_rb_zone(self):
        assert extract_zone_prefix("15RB0501101") == "RB"

    def test_pw_returns_two_chars(self):
        # v1.7: PW03 아니라 PW 2자리만 반환
        assert extract_zone_prefix("15PW0301001") == "PW"

    def test_sm_zone(self):
        assert extract_zone_prefix("15SM0201501") == "SM"

    def test_hold_bin_unknown(self):
        assert extract_zone_prefix("PKMOVE01")  == "UNKNOWN"
        assert extract_zone_prefix("RT0001234") == "UNKNOWN"

    def test_empty_unknown(self):
        assert extract_zone_prefix("") == "UNKNOWN"
        assert extract_zone_prefix("15") == "UNKNOWN"
