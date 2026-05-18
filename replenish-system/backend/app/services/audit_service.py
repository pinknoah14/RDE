import json
from typing import Any
from sqlmodel import Session

from app.models.audit import AuditLog


def write_audit_log(
    entity_type: str,
    entity_id: int,
    action: str,
    actor: str,
    before: Any = None,
    after: Any = None,
    memo: str | None = None,
    session: Session | None = None,
) -> AuditLog:
    before_json = json.dumps(before, ensure_ascii=False, default=str) if before is not None else None
    after_json = json.dumps(after, ensure_ascii=False, default=str) if after is not None else None

    log = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        before_json=before_json,
        after_json=after_json,
        memo=memo,
    )
    if session is not None:
        session.add(log)
    return log
