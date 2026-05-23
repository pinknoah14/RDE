from pathlib import Path
from sqlmodel import create_engine, SQLModel, Session
from sqlalchemy import event

DB_PATH = Path(__file__).parent.parent.parent / "data" / "replenish.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=10000")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


SYSTEM_CONFIG_SEED = [
    # SLACK
    ("slack_bot_token",          "",                       "SECRET",  "SLACK",     "Slack 봇 토큰",               None),
    ("slack_workspace",          "",                       "TEXT",    "SLACK",     "워크스페이스명",              None),
    # WAVE
    ("wave_default_sku_count",   "40",                     "INTEGER", "WAVE",      "기본 추천 SKU 수",            None),
    ("wave_default_min_score",   "40",                     "INTEGER", "WAVE",      "기본 최소 위험도",             None),
    ("wave_default_min_boxes",   "2",                      "INTEGER", "WAVE",      "기본 최소 박스수",             None),
    ("wave_morning_until",       "12:00",                  "TEXT",    "WAVE",      "오전 기준 시각",               None),
    ("wave_afternoon_until",     "17:00",                  "TEXT",    "WAVE",      "오후 기준 시각",               None),
    ("target_days_morning",      "2.0",                    "REAL",    "WAVE",      "오전 목표 보유 일수",          None),
    ("target_days_afternoon",    "1.0",                    "REAL",    "WAVE",      "오후 목표 보유 일수",          None),
    ("target_days_closing",      "0.5",                    "REAL",    "WAVE",      "마감 목표 보유 일수",          None),
    ("new_sku_initial_boxes",    "3",                      "INTEGER", "WAVE",      "신규 상품 최초 보충 박스",     None),
    ("unclaimed_alert_minutes",  "30",                     "INTEGER", "WAVE",      "미선점 경고 기준(분)",          None),
    # ALGORITHM
    ("operating_hours_per_day",  "16",                     "INTEGER", "ALGORITHM", "일 운영 시간(h)",              None),
    ("score_boundary_hours",     "0,1,2,4,6,8",            "CSV_INT", "ALGORITHM", "위험도 구간 경계(시간)",       None),
    ("score_boundary_values",    "100,90,75,55,35,15,0",   "CSV_INT", "ALGORITHM", "위험도 구간 점수",             None),
    ("expiry_warning_days",      "30",                     "INTEGER", "ALGORITHM", "유통기한 임박 기준일",         None),
    ("trend_low_threshold",      "0.7",                    "REAL",    "ALGORITHM", "트렌드 하락 판단 기준",        None),
    ("weight_unassigned",        "15",                     "INTEGER", "ALGORITHM", "미할당 가중치",                None),
    ("weight_expiry",            "10",                     "INTEGER", "ALGORITHM", "유통기한 임박 가중치",         None),
    ("weight_event_active",      "10",                     "INTEGER", "ALGORITHM", "이벤트 SKU 가중치",            None),
    ("weight_new_sku",           "5",                      "INTEGER", "ALGORITHM", "신규 SKU 가중치",              None),
    ("weight_prev_blocked",      "5",                      "INTEGER", "ALGORITHM", "BLOCKED 이력 가중치",          None),
    ("weight_event_ended",       "-10",                    "INTEGER", "ALGORITHM", "이벤트 종료 가중치",           None),
    ("weight_trend_low",         "-5",                     "INTEGER", "ALGORITHM", "트렌드 하락 가중치",           None),
    ("trend_w_avg3d",            "0.6",                    "REAL",    "ALGORITHM", "3일 평균 가중치",              None),
    ("trend_w_yesterday",        "0.4",                    "REAL",    "ALGORITHM", "전일 가중치",                  None),
    ("trend_alpha_today",        "0.30",                   "REAL",    "ALGORITHM", "당일 반영 강도",               None),
    ("trend_cap_mult",           "1.60",                   "REAL",    "ALGORITHM", "급등 상한 배수",               None),
    ("trend_coef_max",           "2.0",                    "REAL",    "ALGORITHM", "트렌드 계수 상한",             None),
    ("trend_coef_min",           "0.5",                    "REAL",    "ALGORITHM", "트렌드 계수 하한",             None),
    # ALGORITHM (v1.7 물리 좌표)
    ("floor_change_penalty",              "60",  "INTEGER", "ALGORITHM", "층 이동 패널티 (수평거리 환산 m)",    None),
    ("proximity_score_threshold_near",    "10",  "INTEGER", "ALGORITHM", "인접 판정 — 근접 기준 (m)",           None),
    ("proximity_score_threshold_mid",     "30",  "INTEGER", "ALGORITHM", "인접 판정 — 중간 기준 (m)",           None),
    ("proximity_score_threshold_far",     "70",  "INTEGER", "ALGORITHM", "인접 판정 — 원거리 기준 (m)",         None),
    ("expiry_critical_days",              "7",   "INTEGER", "ALGORITHM", "유통기한 위급 기준일",                 None),
    ("weight_expiry_critical",            "20",  "INTEGER", "ALGORITHM", "유통기한 위급 가중치",                 None),
    ("weight_replenishing_now",           "-5",  "INTEGER", "ALGORITHM", "이미 보충 중 패널티",                  None),
    ("max_replen_bins",                   "3",   "INTEGER", "ALGORITHM", "보충지번 최대 개수",                   None),
    ("target_days_default",               "1.5", "REAL",    "ALGORITHM", "기본 목표 보유 일수",                  None),
    # PICKING
    ("confidence_high_days",     "3",                      "INTEGER", "PICKING",   "HIGH 신뢰도 기준일",           None),
    ("confidence_medium_days",   "14",                     "INTEGER", "PICKING",   "MEDIUM 신뢰도 기준일",         None),
    ("confidence_low_days",      "30",                     "INTEGER", "PICKING",   "LOW 신뢰도 기준일",            None),
    # WORKER
    ("worker_default_max_tasks", "6",                      "INTEGER", "WORKER",    "작업자 기본 최대 태스크",      None),
    # SYSTEM
    ("bin_id_pattern",           "^15[A-Z]{2}\\d{7}$",    "TEXT",    "SYSTEM",    "지번 정규식 패턴",             None),
    ("exclude_zone_patterns",    "PKMOVE01,STOP,LQ,RT",    "CSV_STR", "SYSTEM",    "보류존 패턴",                  None),
    ("retention_days_sales",     "30",                     "INTEGER", "SYSTEM",    "판매이력 보존(일)",             None),
    ("retention_days_operation", "90",                     "INTEGER", "SYSTEM",    "운영이력 보존(일)",             None),
]


def seed_system_config(session: Session | None = None):
    from app.models.config import SystemConfig
    from sqlmodel import select

    def _seed(s: Session):
        for row in SYSTEM_CONFIG_SEED:
            key, value, ctype, group, label, desc = row
            existing = s.exec(
                select(SystemConfig).where(SystemConfig.config_key == key)
            ).first()
            if not existing:
                s.add(SystemConfig(
                    config_key=key,
                    config_value=value,
                    config_type=ctype,
                    config_group=group,
                    label=label,
                    description=desc,
                ))
        s.commit()

    if session is not None:
        _seed(session)
    else:
        with Session(engine) as s:
            _seed(s)


def init_db():
    # 모든 모델 임포트하여 메타데이터에 등록
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(engine)
    seed_system_config()
