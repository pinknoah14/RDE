from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class Worker(SQLModel, table=True):
    __tablename__ = "workers"

    worker_id: Optional[int] = Field(default=None, primary_key=True)
    worker_name: str = Field(nullable=False)
    worker_type: str = Field(nullable=False)        # FORKLIFT / WALKING (보유 장비)
    zone_access: str = Field(nullable=False)        # JSON 배열: ["RA", "RB"]
    max_tasks: int = Field(default=6, nullable=False)
    slack_id: Optional[str] = None
    is_active: bool = Field(default=False, nullable=False)
    is_sub_worker: bool = Field(default=False, nullable=False)
    skill_level: str = Field(default="NORMAL", nullable=False)
    # EXPERT / NORMAL / JUNIOR — 숙련도
    work_type: str = Field(default="FORKLIFT", nullable=False)
    # FORKLIFT / WALKING — 당일 실제 작업 방식 (worker_type과 별개)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: Optional[datetime] = None
