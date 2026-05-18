from datetime import datetime
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.upload import UploadSession
from app.services.csv_parser import (
    load_inventory_csv_from_bytes,
    classify_inventory,
    detect_unknown_zones,
    detect_multi_picking_bins,
    update_picking_history,
    detect_new_skus,
)

router = APIRouter()


@router.post("/inventory")
async def upload_inventory(
    file: UploadFile = File(...),
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
    update_picking_history(picking_df, session)       # 레코드 먼저 생성
    multi_bins = detect_multi_picking_bins(picking_df, session)  # 그 다음 멀티빈 감지
    new_skus = detect_new_skus(replenish_df, session)

    upload_record = UploadSession(
        upload_type="INVENTORY",
        file_name=file.filename or "unknown.csv",
        uploaded_by=uploaded_by,
        record_count=len(df),
        uploaded_at=datetime.utcnow(),
    )
    session.add(upload_record)
    session.commit()
    session.refresh(upload_record)

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
    records = session.exec(
        select(UploadSession).order_by(UploadSession.uploaded_at.desc()).limit(50)
    ).all()
    return records
