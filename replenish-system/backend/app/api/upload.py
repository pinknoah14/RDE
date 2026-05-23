from datetime import datetime

import polars as pl
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.bin_master import BinMaster
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.models.zone import ZoneConfig
from app.services.csv_parser import (
    classify_inventory,
    detect_multi_picking_bins,
    detect_new_skus,
    detect_unknown_zones,
    load_inventory_csv_from_bytes,
    update_picking_history,
)
from app.services.sales_parser import parse_outbound_csv, parse_pivot_csv
from app.services.sales_service import upsert_daily_sales, update_all_sales_summaries

router = APIRouter()


def save_replenish_snapshot(
    replenish_df: pl.DataFrame,
    upload_session_id: int,
    center_cd: str,
    session: Session,
) -> None:
    """보충존 재고 스냅샷을 DB에 저장. run_algorithm()이 읽는다."""
    for row in replenish_df.iter_rows(named=True):
        session.add(ReplenishBinSnapshot(
            upload_session_id=upload_session_id,
            center_cd=center_cd,
            sku_id=row["상품코드"],
            sku_name=row.get("센터상품명"),
            replenish_bin=row["지번"],
            avail_qty=int(row.get("가용수량") or 0),
            unit_size=int(row.get("입수") or 1),
            deadline_days=row.get("판매마감일수"),
            receipt_date=str(row["입고일자"]) if row.get("입고일자") else None,
        ))
    session.commit()


@router.post("/inventory")
async def upload_inventory(
    file: UploadFile = File(...),
    center_cd: str = Form(default="GGH1"),
    uploaded_by: str = Form(default="관리자"),
    session: Session = Depends(get_session),
):
    content = await file.read()
    try:
        df = load_inventory_csv_from_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    classified = classify_inventory(df, session)
    picking_df = classified["picking"]
    replenish_df = classified["replenish"]

    unknown_zones = detect_unknown_zones(picking_df, replenish_df, session)
    update_picking_history(picking_df, session)
    multi_bins = detect_multi_picking_bins(picking_df, session)
    new_skus = detect_new_skus(replenish_df, session)

    upload_record = UploadSession(
        upload_type="INVENTORY",
        file_name=file.filename or "unknown.csv",
        uploaded_by=uploaded_by,
        uploaded_at=datetime.utcnow(),
        record_count=len(df),
        center_cd=center_cd,
    )
    session.add(upload_record)
    session.commit()
    session.refresh(upload_record)

    save_replenish_snapshot(replenish_df, upload_record.upload_id, center_cd, session)

    return {
        "upload_id": upload_record.upload_id,
        "record_count": len(df),
        "picking_count": len(picking_df),
        "replenish_count": len(replenish_df),
        "hold_count": len(classified["hold"]),
        "unknown_zones": unknown_zones,
        "multi_bin_skus": len(multi_bins),
        "new_skus": new_skus,
    }


def _save_sales(
    df: pl.DataFrame,
    file_name: str,
    upload_type: str,
    center_cd: str,
    uploaded_by: str,
    session: Session,
) -> dict:
    """판매 DataFrame을 daily_sales_history에 UPSERT 후 요약 갱신."""
    rows = upsert_daily_sales(center_cd, df, session)
    sku_count = update_all_sales_summaries(center_cd, session)

    upload_record = UploadSession(
        upload_type=upload_type,
        file_name=file_name,
        uploaded_by=uploaded_by,
        uploaded_at=datetime.utcnow(),
        record_count=rows,
        center_cd=center_cd,
    )
    session.add(upload_record)
    session.commit()
    session.refresh(upload_record)

    return {
        "upload_id": upload_record.upload_id,
        "record_count": rows,
        "sku_count": sku_count,
        "message": f"판매 {rows}행 반영 — {sku_count}개 SKU 요약 갱신",
    }


@router.post("/outbound")
async def upload_outbound(
    file: UploadFile = File(...),
    center_cd: str = Form(default="GGH1"),
    uploaded_by: str = Form(default="관리자"),
    session: Session = Depends(get_session),
):
    content = await file.read()
    try:
        df = parse_outbound_csv(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _save_sales(df, file.filename or "outbound.csv", "OUTBOUND", center_cd, uploaded_by, session)


@router.post("/pivot-sales")
async def upload_pivot_sales(
    file: UploadFile = File(...),
    center_cd: str = Form(default="GGH1"),
    uploaded_by: str = Form(default="관리자"),
    session: Session = Depends(get_session),
):
    content = await file.read()
    try:
        df = parse_pivot_csv(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _save_sales(df, file.filename or "pivot.csv", "PIVOT", center_cd, uploaded_by, session)


@router.post("/bin-master")
async def upload_bin_master(
    file: UploadFile = File(...),
    bin_type: str = Query(default="PICKING", pattern="^(PICKING|REPLENISH)$"),
    center_cd: str = Form(default="GGH1"),
    session: Session = Depends(get_session),
):
    content = await file.read()

    # CP949 → UTF-8 폴백
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail="파일 인코딩을 인식할 수 없습니다 (UTF-8 또는 CP949)")

    try:
        import io
        df = pl.read_csv(io.StringIO(text), infer_schema_length=0, truncate_ragged_lines=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 파싱 오류: {e}")

    # 컬럼 정규화 (공백 제거)
    df = df.rename({c: c.strip() for c in df.columns})
    cols = df.columns

    def find_col(*candidates: str) -> str | None:
        for c in candidates:
            if c in cols:
                return c
        return None

    zone_col = find_col("존", "ZONE", "zone")
    bin_col = find_col("작업존 지번 설명", "지번", "BIN_ID", "bin_id")
    center_col = find_col("센터 창고", "센터", "CENTER")
    status_col = find_col("작업구역 지번명 상태", "상태")
    receive_col = find_col("입고가능")
    pick_col = find_col("피킹가능")
    width_col = find_col("가로(mm)", "가로")
    height_col = find_col("높이(mm)", "높이")
    depth_col = find_col("세로(mm)", "세로")
    cbm_col = find_col("CBM")
    mixed_prod_col = find_col("제품 혼적", "제품혼적")
    mixed_lot_col = find_col("Lot. 혼적", "LOT 혼적", "Lot혼적")

    if not bin_col or not zone_col:
        missing = []
        if not bin_col:
            missing.append("작업존 지번 설명(지번코드)")
        if not zone_col:
            missing.append("존")
        raise HTTPException(status_code=400, detail=f"필수 컬럼 누락: {', '.join(missing)}")

    def _bool(val: str | None) -> bool:
        if val is None:
            return False
        return str(val).strip().upper() in ("Y", "YES", "TRUE", "1", "O", "O")

    def _int(val: str | None) -> int | None:
        if val is None:
            return None
        try:
            return int(float(str(val).strip()))
        except (ValueError, TypeError):
            return None

    def _float(val: str | None) -> float | None:
        if val is None:
            return None
        try:
            return float(str(val).strip())
        except (ValueError, TypeError):
            return None

    now = datetime.utcnow()
    bins_upserted = 0
    zone_prefixes: set[str] = set()

    for row in df.iter_rows(named=True):
        bin_id = str(row[bin_col] or "").strip()
        zone_val = str(row[zone_col] or "").strip()
        if not bin_id or not zone_val:
            continue

        zone_prefixes.add(zone_val)

        existing = session.get(BinMaster, bin_id)
        rec = BinMaster(
            bin_id=bin_id,
            center_cd=str(row.get(center_col) or center_cd).strip() if center_col else center_cd,
            zone_prefix=zone_val,
            bin_type=bin_type,
            can_receive=_bool(row.get(receive_col) if receive_col else None),
            can_pick=_bool(row.get(pick_col) if pick_col else None),
            width_mm=_int(row.get(width_col) if width_col else None),
            height_mm=_int(row.get(height_col) if height_col else None),
            depth_mm=_int(row.get(depth_col) if depth_col else None),
            cbm=_float(row.get(cbm_col) if cbm_col else None),
            allow_mixed_product=_bool(row.get(mixed_prod_col) if mixed_prod_col else None),
            allow_mixed_lot=_bool(row.get(mixed_lot_col) if mixed_lot_col else None),
            description=str(row.get(status_col) or "").strip() if status_col else None,
            status=None,
            updated_at=now,
        )
        if existing:
            for field in BinMaster.model_fields:
                if field != "bin_id":
                    setattr(existing, field, getattr(rec, field))
            session.add(existing)
        else:
            session.add(rec)
        bins_upserted += 1

    # 존코드 자동 등록
    zones_created = 0
    zones_existing = 0
    for zp in zone_prefixes:
        exists = session.exec(select(ZoneConfig).where(ZoneConfig.zone_prefix == zp)).first()
        if exists:
            zones_existing += 1
        else:
            session.add(ZoneConfig(
                zone_prefix=zp,
                zone_name=zp,
                slack_channel="",
                access_type="FORKLIFT",
                list_section="MAIN",
            ))
            zones_created += 1

    session.commit()
    return {
        "bins_upserted": bins_upserted,
        "zones_created": zones_created,
        "zones_existing": zones_existing,
    }


@router.get("/sessions")
def list_upload_sessions(session: Session = Depends(get_session)):
    return session.exec(
        select(UploadSession).order_by(UploadSession.uploaded_at.desc()).limit(50)
    ).all()
