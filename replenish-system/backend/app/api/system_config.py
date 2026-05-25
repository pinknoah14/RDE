from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import invalidate_cache
from app.core.dependencies import get_session
from app.models.config import SystemConfig

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    config_value: str
    updated_by: str = "관리자"


@router.get("")
def list_all_configs(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    configs = session.exec(select(SystemConfig).order_by(SystemConfig.config_group, SystemConfig.config_key)).all()
    return [_to_dict(c) for c in configs]


@router.get("/group/{group}")
def list_configs_by_group(group: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    configs = session.exec(
        select(SystemConfig)
        .where(SystemConfig.config_group == group.upper())
        .order_by(SystemConfig.config_key)
    ).all()
    return [_to_dict(c) for c in configs]


@router.patch("/{config_key}")
def update_config(
    config_key: str,
    body: ConfigUpdateRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    row = session.exec(
        select(SystemConfig).where(SystemConfig.config_key == config_key)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"설정 키 없음: {config_key}")

    row.config_value = body.config_value
    row.updated_by = body.updated_by
    row.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(row)

    # 캐시 무효화
    invalidate_cache()
    return _to_dict(row)


def _to_dict(c: SystemConfig) -> dict[str, Any]:
    return {
        "config_key": c.config_key,
        "config_value": c.config_value if c.config_type != "SECRET" else "***",
        "config_type": c.config_type,
        "config_group": c.config_group,
        "label": c.label,
        "description": c.description,
        "updated_by": c.updated_by,
        "updated_at": c.updated_at,
    }
