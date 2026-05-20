import io
import json
import re
from datetime import date, datetime
from typing import Optional

import polars as pl
from sqlmodel import Session, select

from app.core.config import get_config, get_config_list
from app.models.sku import SkuPickingHistory, SkuSalesSummary
from app.models.zone import ZoneConfig, UnknownZoneFlag


REQUIRED_COLUMNS = [
    "상품코드", "센터상품명", "센터",
    "지번", "존", "피킹가능",
    "가용수량", "입수", "박스수", "박스잔량",
    "센터 판매마감일", "판매마감일수", "유통가능일수", "입고일자",
]

DTYPE_OVERRIDES = {
    "상품코드":       pl.Utf8,
    "센터":           pl.Utf8,
    "지번":           pl.Utf8,
    "센터 판매마감일": pl.Utf8,
    "입고일자":       pl.Utf8,
    "판매마감일수":   pl.Int32,
    "유통가능일수":   pl.Int32,
    "가용수량":       pl.Int32,
    "입수":           pl.Int32,
    "박스수":         pl.Int32,
    "박스잔량":       pl.Int32,
}


def load_inventory_csv(path: str) -> pl.DataFrame:
    raw: Optional[str] = None
    for enc in ["cp949", "utf-8"]:
        try:
            with open(path, encoding=enc) as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError(f"CSV 인코딩 읽기 실패: {path}")
    return pl.read_csv(
        io.StringIO(raw),
        columns=REQUIRED_COLUMNS,
        schema_overrides=DTYPE_OVERRIDES,
        null_values=["", " "],
    )


def load_inventory_csv_from_bytes(content: bytes) -> pl.DataFrame:
    raw: Optional[str] = None
    for enc in ["cp949", "utf-8"]:
        try:
            raw = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError("CSV 인코딩 읽기 실패")
    return pl.read_csv(
        io.StringIO(raw),
        columns=REQUIRED_COLUMNS,
        schema_overrides=DTYPE_OVERRIDES,
        null_values=["", " "],
    )


def parse_bin_id(bin_id: str) -> dict | None:
    """
    지번 문자열을 구조 딕셔너리로 파싱.
    표준 패턴: ^15[A-Z]{2}\\d{7}$
    비표준(보류지번)은 None 반환.
    """
    if not isinstance(bin_id, str):
        return None
    if not re.match(r'^15[A-Z]{2}\d{7}$', bin_id.strip()):
        return None
    return {
        "temp":  bin_id[0:2],
        "zone":  bin_id[2:4],
        "aisle": int(bin_id[4:6]),
        "bay":   int(bin_id[6:8]),
        "level": int(bin_id[8:11]),
    }


def extract_zone_prefix(bin_id: str) -> str:
    """
    지번에서 존 코드 2자리 추출.
    지번 구조: [온도(2)][존(2)][통로(2)][베이(2)][단(3)]
    비표준 지번(보류지번 등)은 'UNKNOWN' 반환.
    """
    if not bin_id or len(bin_id) < 4:
        return "UNKNOWN"
    parsed = parse_bin_id(bin_id)
    if parsed:
        return parsed["zone"]
    return "UNKNOWN"


def classify_inventory(df: pl.DataFrame, session: Session) -> dict[str, pl.DataFrame]:
    """
    재고 CSV를 피킹존 / 보충존 / 보류존으로 분류.
    - 보류존: bin_id_pattern 미매칭 or exclude_zone_patterns 매칭
    - 보충존: 판매마감일수 <= 0 행 제거 후 판매마감일수 ASC 정렬
    - groupby 절대 사용 금지 (혼적 처리)
    """
    bin_pattern = get_config("bin_id_pattern", session)
    exclude_patterns = get_config_list("exclude_zone_patterns", session, cast=str)

    compiled = re.compile(bin_pattern)

    def _is_exclude_zone(bin_id: str) -> bool:
        if not bin_id:
            return True
        if not compiled.match(bin_id):
            return True
        for pat in exclude_patterns:
            if pat.strip() and pat.strip() in bin_id:
                return True
        return False

    exclude_mask = df["지번"].map_elements(_is_exclude_zone, return_dtype=pl.Boolean)
    hold_df = df.filter(exclude_mask)
    active_df = df.filter(~exclude_mask)

    picking_df = active_df.filter(pl.col("피킹가능") == "피킹가능")

    replenish_raw = active_df.filter(pl.col("피킹가능") == "피킹불가")
    # v1.6: 판매마감일수 <= 0 보충존 행 제거
    replenish_df = replenish_raw.filter(
        pl.col("판매마감일수").is_not_null() & (pl.col("판매마감일수") > 0)
    )
    # 혼적 처리: groupby 금지, 판매마감일수 ASC 정렬만 사용
    replenish_df = replenish_df.sort("판매마감일수")

    return {
        "picking": picking_df,
        "replenish": replenish_df,
        "hold": hold_df,
    }


def detect_unknown_zones(
    picking_df: pl.DataFrame,
    replenish_df: pl.DataFrame,
    session: Session,
) -> list[dict]:
    """
    zone_config 미등록 존 감지 → unknown_zone_flags UPSERT.
    보류존(hold) 행은 대상 제외 — bin_id_pattern 미매칭 bin의 가짜 경고 방지.
    """
    known_prefixes = {
        z.zone_prefix
        for z in session.exec(select(ZoneConfig)).all()
    }

    # 피킹존 + 보충존만 대상 (보류존 제외)
    active_df = pl.concat([picking_df, replenish_df], how="diagonal")

    unknown: dict[str, str] = {}  # zone_prefix → sample bin_id
    for row in active_df.iter_rows(named=True):
        bin_id = row.get("지번") or ""
        prefix = extract_zone_prefix(bin_id)
        if prefix != "UNKNOWN" and prefix not in known_prefixes:
            unknown.setdefault(prefix, bin_id)

    now = datetime.utcnow()
    for prefix, sample_bin in unknown.items():
        existing = session.exec(
            select(UnknownZoneFlag).where(UnknownZoneFlag.zone_prefix == prefix)
        ).first()
        if existing:
            existing.seen_count += 1
            existing.last_seen_at = now
            existing.sample_bin_id = sample_bin
        else:
            session.add(UnknownZoneFlag(
                zone_prefix=prefix,
                sample_bin_id=sample_bin,
                first_seen_at=now,
                last_seen_at=now,
            ))
    session.commit()
    return [{"zone_prefix": p, "sample_bin_id": b} for p, b in unknown.items()]


def detect_multi_picking_bins(picking_df: pl.DataFrame, session: Session) -> pl.DataFrame:
    """동일 SKU의 피킹존 지번이 2개 이상인 케이스 감지 (v1.6)"""
    multi = (
        picking_df
        .group_by(["상품코드", "센터"])
        .agg(
            pl.col("지번").alias("bins"),
            pl.len().alias("cnt"),
        )
        .filter(pl.col("cnt") > 1)
    )

    for row in multi.iter_rows(named=True):
        sku_id = row["상품코드"]
        center_cd = row["센터"]
        bins = row["bins"]
        history = session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id == sku_id,
                SkuPickingHistory.center_cd == center_cd,
            )
        ).first()
        if history:
            history.has_multi_bin = True
            history.alt_bin_ids = json.dumps(bins, ensure_ascii=False)

    session.commit()
    return multi


def update_picking_history(picking_df: pl.DataFrame, session: Session) -> None:
    """피킹존 데이터로 sku_picking_history UPSERT + confidence 갱신"""
    high_days = int(get_config("confidence_high_days", session))
    medium_days = int(get_config("confidence_medium_days", session))
    low_days = int(get_config("confidence_low_days", session))

    today = date.today()

    for row in picking_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        center_cd = row["센터"]
        bin_id = row["지번"]
        avail_qty = row["가용수량"]

        existing = session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id == sku_id,
                SkuPickingHistory.center_cd == center_cd,
            )
        ).first()

        if existing:
            last_seen = existing.last_seen_date
            days_ago = (today - last_seen).days if last_seen else None

            if days_ago is None:
                confidence = "NEW"
            elif days_ago <= high_days:
                confidence = "HIGH"
            elif days_ago <= medium_days:
                confidence = "MEDIUM"
            elif days_ago <= low_days:
                confidence = "LOW"
            else:
                confidence = "STALE"

            existing.picking_bin = bin_id
            existing.last_seen_date = today
            existing.last_seen_qty = avail_qty
            existing.confidence = confidence
            existing.updated_at = datetime.utcnow()
        else:
            zone_prefix = extract_zone_prefix(bin_id or "")
            session.add(SkuPickingHistory(
                sku_id=sku_id,
                center_cd=center_cd,
                picking_bin=bin_id,
                zone=zone_prefix,
                last_seen_date=today,
                last_seen_qty=avail_qty,
                is_new_sku=True,
                confidence="NEW",
                updated_at=datetime.utcnow(),
            ))

    session.commit()


def detect_new_skus(replenish_df: pl.DataFrame, session: Session) -> list[str]:
    """보충존에만 있고 sku_picking_history + sku_sales_summary 모두 없는 신규 SKU"""
    new_skus: list[str] = []
    seen: set[str] = set()

    for row in replenish_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        center_cd = row["센터"]
        key = f"{sku_id}:{center_cd}"
        if key in seen:
            continue
        seen.add(key)

        has_history = session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id == sku_id,
                SkuPickingHistory.center_cd == center_cd,
            )
        ).first()
        has_sales = session.exec(
            select(SkuSalesSummary).where(
                SkuSalesSummary.sku_id == sku_id,
                SkuSalesSummary.center_cd == center_cd,
            )
        ).first()

        if not has_history and not has_sales:
            new_skus.append(sku_id)

    return new_skus
