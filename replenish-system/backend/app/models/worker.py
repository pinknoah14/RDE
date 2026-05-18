from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Worker(SQLModel, table=True):
    __tablename__ = "workers"

    worker_id: Optional[int] = Field(default=None, primary_key=True)
    worker_name: str = Field(nullable=False)
    worker_type: str = Field(nullable=False)        # FORKLIFT / WALKING
    zone_access: str = Field(nullable=False)        # JSON 배열: ["RA", "RB"]
    max_tasks: int = Field(default=6, nullable=False)
    slack_id: Optional[str] = None
    is_active: bool = Field(default=False, nullable=False)
    is_sub_worker: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: Optional[datetime] = None
