from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from sqlalchemy import UniqueConstraint


class ReplenishCandidate(SQLModel, table=True):
    __tablename__ = "replenish_candidates"

    candidate_id: Optional[int] = Field(default=None, primary_key=True)
    wave_id: int = Field(foreign_key="waves.wave_id", nullable=False)
    sku_id: str = Field(nullable=False)
    sku_name: str = Field(nullable=False)
    picking_bin: str = Field(nullable=False)
    picking_confidence: Optional[str] = None        # HIGH / MEDIUM / LOW / STALE / NEW
    zone: str = Field(nullable=False)
    slack_channel: str = Field(nullable=False)
    list_section: str = Field(default="MAIN", nullable=False)   # MAIN / SUB
    risk_score: float = Field(nullable=False)
    risk_level: str = Field(nullable=False)         # CRITICAL / HIGH / MEDIUM / LOW
    eta_hours: Optional[float] = None
    avg_daily_sales: Optional[float] = None
    today_sales: int = Field(default=0)
    recommended_qty: int = Field(nullable=False)
    reason_flags: Optional[str] = None              # JSON: ["미할당", "이벤트", "BLOCKED이력"]
    matched_bins_json: Optional[str] = None         # JSON list from match_replen_bins + proximity_score
    candidate_status: str = Field(default="PENDING", nullable=False)
    # PENDING / APPROVED / REJECTED / MODIFIED
    modified_qty: Optional[int] = None
    rejected_reason: Optional[str] = None
    batch_tag: Optional[str] = Field(default=None)
    # 1순위 보충지번 문자열 — 동일 파렛트 공유 SKU 그룹 식별자 (NULL=단독)
    batch_seq: Optional[int] = Field(default=None)
    # 배치 내 처리 순서 (risk_score 기준 1, 2, 3, ...)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: Optional[datetime] = None


class ReplenishConfirmedTask(SQLModel, table=True):
    __tablename__ = "replenish_confirmed_tasks"

    task_id: Optional[int] = Field(default=None, primary_key=True)
    candidate_id: Optional[int] = Field(default=None, foreign_key="replenish_candidates.candidate_id")
    wave_id: int = Field(foreign_key="waves.wave_id", nullable=False)
    worker_id: Optional[int] = Field(default=None, foreign_key="workers.worker_id")
    sku_id: str = Field(nullable=False)
    sku_name: str = Field(nullable=False)
    picking_bin: str = Field(nullable=False)
    zone: str = Field(nullable=False)
    slack_channel: str = Field(nullable=False)
    list_section: str = Field(default="MAIN", nullable=False)
    section_seq: Optional[int] = None
    worker_type: str = Field(nullable=False)
    total_qty: int = Field(nullable=False)
    shortage_qty: int = Field(default=0)
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    block_reason: Optional[str] = None
    confirm_type: str = Field(nullable=False)       # AUTO / MANUAL
    confirmed_by: str = Field(nullable=False)
    task_status: str = Field(default="READY", nullable=False)
    # READY / QUEUED / SENT / DONE / BLOCKED / CANCELLED
    list_seq: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    sent_at: Optional[datetime] = None
    done_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None


class ReplenishTaskLocation(SQLModel, table=True):
    __tablename__ = "replenish_task_locations"
    __table_args__ = (UniqueConstraint("task_id", "seq", name="uq_task_location"),)

    location_id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="replenish_confirmed_tasks.task_id", nullable=False)
    seq: int = Field(nullable=False)
    replenish_bin: str = Field(nullable=False)
    replenish_zone: Optional[str] = None
    replenish_zone_prefix: Optional[str] = None
    allocated_qty: int = Field(nullable=False)
    sales_deadline_days: Optional[int] = None
    receipt_date: Optional[str] = None             # DATE stored as TEXT in SQLite
    proximity_score: Optional[int] = None          # 1-4
    location_status: str = Field(default="PENDING", nullable=False)
    # PENDING / ISSUED / DONE / SKIPPED
    reason_code: Optional[str] = None
    # SKIPPED_EMPTY / SKIPPED_BLOCKED / SKIPPED_NOT_FOUND / SKIPPED_OTHER
    reason_memo: Optional[str] = None
    issued_at: Optional[datetime] = None
    done_at: Optional[datetime] = None


class ReplenishTaskQueue(SQLModel, table=True):
    __tablename__ = "replenish_task_queue"

    queue_id: Optional[int] = Field(default=None, primary_key=True)
    wave_id: int = Field(foreign_key="waves.wave_id", nullable=False)
    worker_id: Optional[int] = Field(default=None, foreign_key="workers.worker_id")
    slack_channel: str = Field(nullable=False)
    slack_channel_id: Optional[str] = None
    target_channel_id: Optional[str] = None        # v1.6: 재전송 override 채널
    list_section: str = Field(default="MAIN", nullable=False)
    message_text: str = Field(nullable=False)
    blocks_json: Optional[str] = None
    queue_status: str = Field(default="WAITING", nullable=False)
    # WAITING / SENT / FAILED
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    sent_at: Optional[datetime] = None
    slack_ts: Optional[str] = None
    retry_count: int = Field(default=0)
    error_message: Optional[str] = None
