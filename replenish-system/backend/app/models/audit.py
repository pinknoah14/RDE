from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    log_id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str = Field(nullable=False)
    entity_id: int = Field(nullable=False)
    action: str = Field(nullable=False)
    before_json: Optional[str] = None
    after_json: Optional[str] = None
    actor: str = Field(default="system", nullable=False)
    memo: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
