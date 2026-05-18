from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.dependencies import get_session
from app.models.audit import AuditLog

router = APIRouter()


@router.get("")
def list_recent_audit_logs(
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_session),
):
    return session.exec(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()


@router.get("/{entity_type}/{entity_id}")
def list_entity_audit_logs(
    entity_type: str,
    entity_id: int,
    session: Session = Depends(get_session),
):
    return session.exec(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
    ).all()
