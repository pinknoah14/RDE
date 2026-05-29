import shutil
import sqlite3

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.core.auth import get_admin_pin, issue_token
from app.core.database import DB_PATH
from app.core.dependencies import get_session
from app.core.exceptions import RDEException

BACKUP_PATH = DB_PATH.parent / "replenish_backup.db"

router = APIRouter()


class PinVerifyRequest(BaseModel):
    pin: str


@router.post("/verify-pin")
def verify_pin(body: PinVerifyRequest, session: Session = Depends(get_session)):
    """PIN 검증 + 세션 토큰 발급. 빈 admin_pin 설정이면 인증 비활성(항상 통과)."""
    stored_pin = get_admin_pin(session)

    if not stored_pin:
        return {"ok": True, "auth_required": False, "token": None, "message": "PIN 미설정"}

    if body.pin != stored_pin:
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "code": "INVALID_PIN",
                "message": "PIN이 올바르지 않습니다.",
                "detail": "",
            },
        )

    return {"ok": True, "auth_required": True, "token": issue_token(stored_pin)}


@router.get("/db-export")
def export_db():
    if not DB_PATH.exists():
        raise RDEException(code="DB_NOT_FOUND", message="DB 파일 없음", status_code=404)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    return FileResponse(
        path=str(DB_PATH),
        media_type="application/octet-stream",
        filename="replenish.db",
    )


@router.post("/db-import")
async def import_db(file: UploadFile = File(...)):
    content = await file.read()
    temp_path = DB_PATH.parent / "replenish_import_temp.db"
    temp_path.write_bytes(content)

    # 스키마 호환성 확인
    try:
        conn = sqlite3.connect(str(temp_path))
        conn.execute("SELECT config_key FROM system_config LIMIT 1")
        conn.close()
    except sqlite3.DatabaseError:
        temp_path.unlink(missing_ok=True)
        raise RDEException(code="DB_INCOMPATIBLE", message="호환되지 않는 DB 파일입니다", status_code=400)

    # 현재 DB 백업
    if DB_PATH.exists():
        shutil.copy(DB_PATH, BACKUP_PATH)

    # 교체
    shutil.move(str(temp_path), str(DB_PATH))
    return {"message": "DB 가져오기 완료. 서버를 재시작하세요."}
