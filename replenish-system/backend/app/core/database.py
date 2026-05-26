from pathlib import Path
import shutil
from datetime import datetime

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
    ("slack_items_per_message",  "6",                      "INTEGER", "SLACK",     "메시지당 SKU 수 (5~6 권장)",   None),
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
    ("prestock_uph",             "12",                     "INTEGER", "WAVE",      "선보충 UPH (시간당 처리 SKU 수)", None),
    ("prestock_minutes",         "100",                    "INTEGER", "WAVE",      "선보충 가용 시간(분)",          None),
    # ALGORITHM
    ("operating_hours_per_day",  "16",                     "INTEGER", "ALGORITHM", "일 운영 시간(h)",              None),
    ("score_boundary_hours",     "0,1,2,4,6,8",            "CSV_INT", "ALGORITHM", "위험도 구간 경계(시간)",       None),
    ("score_boundary_values",    "100,90,75,55,35,15,0",   "CSV_INT", "ALGORITHM", "위험도 구간 점수",             None),
    ("expiry_warning_days",      "30",                     "INTEGER", "ALGORITHM", "유통기한 임박 기준일",         None),
    ("trend_low_threshold",      "0.7",                    "REAL",    "ALGORITHM", "트렌드 하락 판단 기준",        None),
    (
        "weight_unassigned", "15", "INTEGER", "ALGORITHM",
        "미할당 발생 위험도 가중치",
        (
            "기본값: +15\n"
            "\n"
            "[현장 조정 가이드]\n"
            "실운영 2~3 사이클 후 아래 기준으로 조정:\n"
            "\n"
            "· CRITICAL 추천이 늦다 (미할당 터지고 나서야 추천됨)\n"
            "  → +20 또는 +25로 상향\n"
            "\n"
            "· CRITICAL이 너무 많아 관리 불가\n"
            "  → 현재값(15) 유지\n"
            "\n"
            "권장 조정 범위: +15 ~ +25\n"
            "최대값 +25 초과 시 다른 가중치와 균형 붕괴 위험"
        ),
    ),
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
    ("batch_tag_min_group",               "2",   "INTEGER", "ALGORITHM", "배치 태그 최소 공유 SKU 수",            None),
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
    (
        "admin_pin", "", "SECRET", "SYSTEM",
        "관리자 PIN",
        "4~8자리 숫자. 빈 값이면 PIN 없이 접속. 설정 후 재접속 필요.",
    ),
    # CSV_COLUMNS — 재고 CSV 컬럼명
    ("col_inv_sku",           "상품코드",        "TEXT", "CSV_COLUMNS", "재고CSV: SKU 컬럼명",           None),
    ("col_inv_sku_name",      "센터상품명",      "TEXT", "CSV_COLUMNS", "재고CSV: 상품명 컬럼명",        None),
    ("col_inv_center",        "센터",            "TEXT", "CSV_COLUMNS", "재고CSV: 센터 컬럼명",          None),
    ("col_inv_bin",           "지번",            "TEXT", "CSV_COLUMNS", "재고CSV: 지번 컬럼명",          None),
    ("col_inv_zone",          "존",              "TEXT", "CSV_COLUMNS", "재고CSV: 존 컬럼명",            None),
    ("col_inv_pickable",      "피킹가능",        "TEXT", "CSV_COLUMNS", "재고CSV: 피킹가능여부 컬럼명",  None),
    ("col_inv_pickable_yes",  "피킹가능",        "TEXT", "CSV_COLUMNS", "재고CSV: 피킹가능 값(value)",   None),
    ("col_inv_pickable_no",   "피킹불가",        "TEXT", "CSV_COLUMNS", "재고CSV: 피킹불가 값(value)",   None),
    ("col_inv_avail_qty",     "가용수량",        "TEXT", "CSV_COLUMNS", "재고CSV: 가용수량 컬럼명",      None),
    ("col_inv_unit_size",     "입수",            "TEXT", "CSV_COLUMNS", "재고CSV: 입수 컬럼명",          None),
    ("col_inv_box_count",     "박스수",          "TEXT", "CSV_COLUMNS", "재고CSV: 박스수 컬럼명",        None),
    ("col_inv_box_remain",    "박스잔량",        "TEXT", "CSV_COLUMNS", "재고CSV: 박스잔량 컬럼명",      None),
    ("col_inv_deadline_date", "센터 판매마감일", "TEXT", "CSV_COLUMNS", "재고CSV: 판매마감일 컬럼명",    None),
    ("col_inv_deadline_days", "판매마감일수",    "TEXT", "CSV_COLUMNS", "재고CSV: 판매마감일수 컬럼명",  None),
    ("col_inv_shelf_days",    "유통가능일수",    "TEXT", "CSV_COLUMNS", "재고CSV: 유통가능일수 컬럼명",  None),
    ("col_inv_receipt_date",  "입고일자",        "TEXT", "CSV_COLUMNS", "재고CSV: 입고일자 컬럼명",      None),
    # CSV_COLUMNS — 피벗 CSV 컬럼명
    ("col_pivot_sku",         "상품코드",        "TEXT", "CSV_COLUMNS", "피벗CSV: SKU 컬럼명",           None),
    ("col_pivot_center",      "센터",            "TEXT", "CSV_COLUMNS", "피벗CSV: 센터 컬럼명",          None),
    # CSV_COLUMNS — 출고 CSV 컬럼명
    ("col_out_sku",           "상품코드",        "TEXT", "CSV_COLUMNS", "출고CSV: SKU 컬럼명",           None),
    ("col_out_center",        "센터",            "TEXT", "CSV_COLUMNS", "출고CSV: 센터 컬럼명",          None),
    ("col_out_date",          "판매일자",        "TEXT", "CSV_COLUMNS", "출고CSV: 판매일자 컬럼명",      None),
    ("col_out_qty",           "판매수량",        "TEXT", "CSV_COLUMNS", "출고CSV: 판매수량 컬럼명",      None),
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


def auto_backup_db() -> Path | None:
    """앱 시작 시 DB를 data/backups/ 에 타임스탬프 백업. 7일 이전 백업 삭제."""
    if not DB_PATH.exists():
        return None

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"replenish_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)

    cutoff = datetime.now().timestamp() - (7 * 24 * 3600)
    for old in backup_dir.glob("replenish_*.db"):
        if old.stat().st_mtime < cutoff:
            old.unlink()

    return backup_path


def init_db():
    # 모든 모델 임포트하여 메타데이터에 등록
    import app.models  # noqa: F401
    auto_backup_db()
    SQLModel.metadata.create_all(engine)
    seed_system_config()
