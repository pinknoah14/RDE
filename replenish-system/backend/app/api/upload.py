from datetime import datetime

import polars as pl
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.inventory import ReplenishBinSnapshot
from app.models.upload import UploadSession
from app.services.csv_parser import (
    classify_inventory,
    detect_multi_picking_bins,
    detect_new_skus,
    detect_unknown_zones,
    load_inventory_csv_from_bytes,
    update_picking_history,
)

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


@router.get("/history")
def list_upload_history(session: Session = Depends(get_session)):
    return session.exec(
        select(UploadSession).order_by(UploadSession.uploaded_at.desc()).limit(50)
    ).all()
