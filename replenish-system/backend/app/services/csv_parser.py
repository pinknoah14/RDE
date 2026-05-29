import io
import json
import re
from datetime import date, datetime

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

# 시스템 설정 키 → 내부(표준) 컬럼명 / 피킹가능 값 기본값
INTERNAL_COL_MAP: dict[str, str] = {
    "col_inv_sku":           "상품코드",
    "col_inv_sku_name":      "센터상품명",
    "col_inv_center":        "센터",
    "col_inv_bin":           "지번",
    "col_inv_zone":          "존",
    "col_inv_pickable":      "피킹가능",
    "col_inv_pickable_yes":  "피킹가능",
    "col_inv_pickable_no":   "피킹불가",
    "col_inv_avail_qty":     "가용수량",
    "col_inv_unit_size":     "입수",
    "col_inv_box_count":     "박스수",
    "col_inv_box_remain":    "박스잔량",
    "col_inv_deadline_date": "센터 판매마감일",
    "col_inv_deadline_days": "판매마감일수",
    "col_inv_shelf_days":    "유통가능일수",
    "col_inv_receipt_date":  "입고일자",
    "col_pivot_sku":         "상품코드",
    "col_pivot_center":      "센터",
    "col_out_sku":           "상품코드",
    "col_out_center":        "센터",
    "col_out_date":          "판매일자",
    "col_out_qty":           "판매수량",
}

# 재고 CSV 컬럼명 키 순서 (REQUIRED_COLUMNS 대응)
_INV_COL_KEYS = [
    "col_inv_sku", "col_inv_sku_name", "col_inv_center",
    "col_inv_bin", "col_inv_zone", "col_inv_pickable",
    "col_inv_avail_qty", "col_inv_unit_size", "col_inv_box_count",
    "col_inv_box_remain", "col_inv_deadline_date", "col_inv_deadline_days",
    "col_inv_shelf_days", "col_inv_receipt_date",
]


def get_csv_col_map(session: Session) -> dict[str, str]:
    """DB 설정에서 CSV 컬럼명 매핑을 로드. 미설정 키는 INTERNAL_COL_MAP 기본값 사용."""
    result: dict[str, str] = {}
    for key, default in INTERNAL_COL_MAP.items():
        try:
            val = get_config(key, session)
            result[key] = val if val else default
        except KeyError:
            result[key] = default
    return result


def decode_csv_bytes(raw: bytes, encodings: tuple[str, ...] = ("cp949", "utf-8", "utf-8-sig")) -> str:
    """CP949 → UTF-8 순서로 디코딩 시도. 모두 실패 시 ValueError."""
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩을 인식할 수 없습니다 (UTF-8 또는 CP949)")


def load_inventory_csv(path: str) -> pl.DataFrame:
    for enc in ("cp949", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"CSV 인코딩 읽기 실패: {path}")
    return pl.read_csv(
        io.StringIO(raw),
        columns=REQUIRED_COLUMNS,
        schema_overrides=DTYPE_OVERRIDES,
        null_values=["", " "],
    )


def load_inventory_csv_from_bytes(content: bytes, col_map: dict[str, str] | None = None) -> pl.DataFrame:
    raw = decode_csv_bytes(content)

    # 실제 컬럼명(WMS 헤더) 목록과 내부명으로의 rename 맵 구성
    if col_map:
        actual_cols = [col_map.get(k, INTERNAL_COL_MAP[k]) for k in _INV_COL_KEYS]
        rename_map = {
            col_map[k]: INTERNAL_COL_MAP[k]
            for k in _INV_COL_KEYS
            if col_map.get(k) and col_map[k] != INTERNAL_COL_MAP[k]
        }
        actual_dtype_overrides = {
            col_map.get(k, INTERNAL_COL_MAP[k]): DTYPE_OVERRIDES[INTERNAL_COL_MAP[k]]
            for k in _INV_COL_KEYS
            if INTERNAL_COL_MAP[k] in DTYPE_OVERRIDES
        }
    else:
        actual_cols = REQUIRED_COLUMNS
        rename_map = {}
        actual_dtype_overrides = DTYPE_OVERRIDES

    try:
        df = pl.read_csv(
            io.StringIO(raw),
            columns=actual_cols,
            schema_overrides=actual_dtype_overrides,
            null_values=["", " "],
        )
    except Exception as parse_err:
        # 누락 컬럼 목록을 명시해 사용자가 원인을 파악할 수 있도록 한다.
        try:
            header_df = pl.read_csv(io.StringIO(raw), n_rows=0, infer_schema_length=0)
            actual_set = set(header_df.columns)
            missing = [c for c in actual_cols if c not in actual_set]
            if missing:
                raise ValueError(
                    f"재고 CSV 필수 컬럼 누락: {', '.join(missing)}\n"
                    f"업로드한 파일의 컬럼: {', '.join(sorted(actual_set))}"
                ) from parse_err
        except ValueError:
            raise
        except Exception:
            pass
        raise parse_err

    if rename_map:
        df = df.rename(rename_map)
    return df


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


def classify_inventory(df: pl.DataFrame, session: Session, col_map: dict[str, str] | None = None) -> dict[str, pl.DataFrame]:
    """
    재고 CSV를 피킹존 / 보충존 / 보류존으로 분류.
    - 보류존: bin_id_pattern 미매칭 or exclude_zone_patterns 매칭
    - 보충존: 판매마감일수 <= 0 행 제거 후 판매마감일수 ASC 정렬
    - groupby 절대 사용 금지 (혼적 처리)
    """
    bin_pattern = get_config("bin_id_pattern", session)
    exclude_patterns = get_config_list("exclude_zone_patterns", session, cast=str)

    pickable_yes = col_map["col_inv_pickable_yes"] if col_map else INTERNAL_COL_MAP["col_inv_pickable_yes"]
    pickable_no  = col_map["col_inv_pickable_no"]  if col_map else INTERNAL_COL_MAP["col_inv_pickable_no"]

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

    picking_df = active_df.filter(pl.col("피킹가능") == pickable_yes)

    replenish_raw = active_df.filter(pl.col("피킹가능") == pickable_no)
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


def restore_missing_picking_bins(
    picking_df: pl.DataFrame,
    session: Session,
) -> pl.DataFrame:
    """WMS 유실 피킹지번 복구 — avail_qty=0이면 CSV에서 행이 사라지는 버그 대응.

    sku_picking_history에 confidence HIGH/MEDIUM/LOW로 기록된 SKU 중
    이번 CSV에 없는 것들을 가용수량=0 행으로 복구한다.
    관리자가 대시보드에서 미할당으로 인식하도록 한다.
    """
    if picking_df.height == 0:
        sku_in_csv: set[str] = set()
    else:
        sku_in_csv = set(picking_df["상품코드"].to_list())

    histories = session.exec(
        select(SkuPickingHistory).where(
            SkuPickingHistory.confidence.in_(["HIGH", "MEDIUM", "LOW"]),
            SkuPickingHistory.picking_bin.is_not(None),
        )
    ).all()

    restored_rows: list[dict] = []
    for h in histories:
        if h.sku_id in sku_in_csv:
            continue
        restored_rows.append({
            "상품코드":       h.sku_id,
            "센터상품명":     h.sku_id,
            "센터":           h.center_cd or "GGH1",
            "지번":           h.picking_bin,
            "존":             h.zone or "",
            "피킹가능":       "피킹가능",
            "가용수량":       0,
            "입수":           1,
            "박스수":         0,
            "박스잔량":       0,
            "센터 판매마감일": "",
            "판매마감일수":   999,
            "유통가능일수":   999,
            "입고일자":       str(h.last_seen_date) if h.last_seen_date else "",
        })

    if not restored_rows:
        return picking_df

    # picking_df는 DTYPE_OVERRIDES로 정수 컬럼이 Int32. dict 기반 restored_df는
    # 기본 Int64로 추론되어 diagonal concat 시 스키마 충돌 → 동일 override 적용.
    restored_df = pl.DataFrame(restored_rows, schema_overrides=DTYPE_OVERRIDES)
    return pl.concat([picking_df, restored_df], how="diagonal")


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

    if multi.height == 0:
        return multi

    multi_sku_ids = multi["상품코드"].to_list()
    multi_center_cds = multi["센터"].to_list()

    history_map: dict[tuple[str, str], SkuPickingHistory] = {
        (h.sku_id, h.center_cd): h
        for h in session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id.in_(multi_sku_ids),
                SkuPickingHistory.center_cd.in_(multi_center_cds),
            )
        ).all()
    }

    for row in multi.iter_rows(named=True):
        history = history_map.get((row["상품코드"], row["센터"]))
        if history:
            history.has_multi_bin = True
            history.alt_bin_ids = json.dumps(row["bins"], ensure_ascii=False)

    session.commit()
    return multi


def update_picking_history(picking_df: pl.DataFrame, session: Session) -> None:
    """피킹존 데이터로 sku_picking_history UPSERT + confidence 갱신"""
    high_days = int(get_config("confidence_high_days", session))
    medium_days = int(get_config("confidence_medium_days", session))
    low_days = int(get_config("confidence_low_days", session))
    today = date.today()

    sku_ids = list(picking_df["상품코드"].unique().to_list())
    center_cds = list(picking_df["센터"].unique().to_list())

    existing_map: dict[tuple[str, str], SkuPickingHistory] = {
        (h.sku_id, h.center_cd): h
        for h in session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id.in_(sku_ids),
                SkuPickingHistory.center_cd.in_(center_cds),
            )
        ).all()
    }

    for row in picking_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        center_cd = row["센터"]
        bin_id = row["지번"]
        avail_qty = row["가용수량"]
        existing = existing_map.get((sku_id, center_cd))

        if existing:
            last_seen = existing.last_seen_date
            days_ago = (today - last_seen).days if last_seen else None
            if days_ago is None:           confidence = "NEW"
            elif days_ago <= high_days:    confidence = "HIGH"
            elif days_ago <= medium_days:  confidence = "MEDIUM"
            elif days_ago <= low_days:     confidence = "LOW"
            else:                          confidence = "STALE"
            existing.picking_bin = bin_id
            existing.last_seen_date = today
            existing.last_seen_qty = avail_qty
            existing.confidence = confidence
            existing.updated_at = datetime.utcnow()
        else:
            zone_prefix = extract_zone_prefix(bin_id or "")
            new_h = SkuPickingHistory(
                sku_id=sku_id, center_cd=center_cd,
                picking_bin=bin_id, zone=zone_prefix,
                last_seen_date=today, last_seen_qty=avail_qty,
                is_new_sku=True, confidence="NEW",
                updated_at=datetime.utcnow(),
            )
            session.add(new_h)
            existing_map[(sku_id, center_cd)] = new_h  # 같은 배치 내 중복 방지

    session.commit()


def detect_new_skus(replenish_df: pl.DataFrame, session: Session) -> list[str]:
    """보충존에만 있고 sku_picking_history + sku_sales_summary 모두 없는 신규 SKU"""
    sku_ids = list(replenish_df["상품코드"].unique().to_list())
    center_cds = list(replenish_df["센터"].unique().to_list())

    has_history_set: set[tuple[str, str]] = {
        (h.sku_id, h.center_cd)
        for h in session.exec(
            select(SkuPickingHistory).where(
                SkuPickingHistory.sku_id.in_(sku_ids),
                SkuPickingHistory.center_cd.in_(center_cds),
            )
        ).all()
    }
    has_sales_set: set[tuple[str, str]] = {
        (s.sku_id, s.center_cd)
        for s in session.exec(
            select(SkuSalesSummary).where(
                SkuSalesSummary.sku_id.in_(sku_ids),
                SkuSalesSummary.center_cd.in_(center_cds),
            )
        ).all()
    }

    new_skus: list[str] = []
    seen: set[str] = set()
    for row in replenish_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        center_cd = row["센터"]
        key = f"{sku_id}:{center_cd}"
        if key in seen:
            continue
        seen.add(key)
        if (sku_id, center_cd) not in has_history_set and (sku_id, center_cd) not in has_sales_set:
            new_skus.append(sku_id)

    return new_skus
