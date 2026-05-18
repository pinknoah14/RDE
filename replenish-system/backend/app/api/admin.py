import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.database import DB_PATH

BACKUP_PATH = DB_PATH.parent / "replenish_backup.db"

router = APIRouter()


@router.get("/db-export")
def export_db():
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="DB 파일 없음")
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
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="호환되지 않는 DB 파일입니다")

    # 현재 DB 백업
    if DB_PATH.exists():
        shutil.copy(DB_PATH, BACKUP_PATH)

    # 교체
    shutil.move(str(temp_path), str(DB_PATH))
    return {"message": "DB 가져오기 완료. 서버를 재시작하세요."}
