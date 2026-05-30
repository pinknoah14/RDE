from datetime import date, timedelta

import polars as pl
from sqlalchemy import insert as sa_insert
from sqlmodel import Session, select

from app.models.sku import DailySalesHistory, SkuSalesSummary
from app.models.event import Event


def upsert_daily_sales(center_cd: str, sales_df: pl.DataFrame, session: Session) -> int:
    """
    판매 DataFrame(상품코드, 센터, 판매일자, 판매수량) → daily_sales_history UPSERT.
    반환: 처리 행 수
    """
    rows_to_insert: list[dict] = []
    for row in sales_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        ctr = row.get("센터") or center_cd
        sales_date_str = row["판매일자"]
        qty = int(row["판매수량"] or 0)

        try:
            sales_date = date.fromisoformat(sales_date_str)
        except (ValueError, TypeError):
            continue

        rows_to_insert.append({
            "sku_id": sku_id,
            "center_cd": ctr,
            "sales_date": sales_date,
            "sales_qty": qty,
        })

    if rows_to_insert:
        # 행별 SELECT 대신 벌크 INSERT OR REPLACE 1회 실행
        session.execute(
            sa_insert(DailySalesHistory).prefix_with("OR REPLACE"),
            rows_to_insert,
        )
    session.commit()
    return len(rows_to_insert)


def _get_event_dates(sku_id: str, center_cd: str, session: Session) -> set[date]:
    """이벤트 구간에 해당하는 날짜 집합 반환."""
    today = date.today()
    cutoff = today - timedelta(days=14)
    events = session.exec(
        select(Event).where(
            Event.sku_id == sku_id,
            Event.end_dt >= cutoff,
        )
    ).all()
    event_dates: set[date] = set()
    for ev in events:
        d = ev.start_dt.date() if hasattr(ev.start_dt, "date") else ev.start_dt
        end = ev.end_dt.date() if hasattr(ev.end_dt, "date") else ev.end_dt
        while d <= end:
            event_dates.add(d)
            d += timedelta(days=1)
    return event_dates


def _compute_speed_from_map(
    sales_map: dict[date, int],
    event_dates: set[date],
    all_dates: list[date],
    cutoff_recent: date,
) -> dict:
    if not sales_map:
        return {"base_daily_avg": 0.0, "trend_coef": 1.0, "adjusted_daily": 0.0, "recent_daily_avg": 0.0}

    stockout_mask: set[date] = set()
    for d in reversed(all_dates):
        if sales_map.get(d, 0) == 0:
            stockout_mask.add(d)
        else:
            break

    valid_days: list[int] = []
    recent_7: list[int] = []
    prior_7: list[int] = []

    for d in all_dates:
        if d in event_dates or d in stockout_mask:
            continue
        qty = sales_map.get(d, 0)
        valid_days.append(qty)
        if d >= cutoff_recent:
            recent_7.append(qty)
        else:
            prior_7.append(qty)

    base_daily_avg = sum(valid_days) / len(valid_days) if valid_days else 0.0
    recent_avg = sum(recent_7) / len(recent_7) if recent_7 else base_daily_avg
    prior_avg = sum(prior_7) / len(prior_7) if prior_7 else base_daily_avg

    trend_coef = max(0.5, min(2.0, recent_avg / prior_avg)) if prior_avg > 0 else 1.0
    adjusted_daily = base_daily_avg * trend_coef

    return {
        "base_daily_avg": round(base_daily_avg, 4),
        "trend_coef": round(trend_coef, 4),
        "adjusted_daily": round(adjusted_daily, 4),
        "recent_daily_avg": round(recent_avg, 4),
    }


def calculate_sales_speed(sku_id: str, center_cd: str, session: Session) -> dict:
    """
    최근 14일 데이터로 판매속도 계산.
    - 품절 구간(재고=0 & 판매=0 연속) 제외
    - 이벤트 구간 제외
    - 트렌드: 직전7일 평균 / 이전7일 평균, CLAMP(0.5, 2.0)
    반환: {base_daily_avg, trend_coef, adjusted_daily, recent_daily_avg}
    """
    today = date.today()
    window_start = today - timedelta(days=13)
    all_dates = [window_start + timedelta(days=i) for i in range(14)]
    cutoff_recent = today - timedelta(days=7)

    rows = session.exec(
        select(DailySalesHistory).where(
            DailySalesHistory.sku_id == sku_id,
            DailySalesHistory.center_cd == center_cd,
            DailySalesHistory.sales_date >= window_start,
        ).order_by(DailySalesHistory.sales_date)
    ).all()

    sales_map: dict[date, int] = {r.sales_date: r.sales_qty for r in rows}
    event_dates = _get_event_dates(sku_id, center_cd, session)
    return _compute_speed_from_map(sales_map, event_dates, all_dates, cutoff_recent)


def _batch_calculate_sales_speeds(
    center_cd: str, sku_ids: list[str], session: Session
) -> dict[str, dict]:
    """N개 SKU 판매속도를 쿼리 3회로 일괄 계산 (기존 2N회 대비 대폭 감소)."""
    today = date.today()
    window_start = today - timedelta(days=13)
    cutoff_recent = today - timedelta(days=7)
    all_dates = [window_start + timedelta(days=i) for i in range(14)]
    sku_set = set(sku_ids)

    # 1. center_cd + window 조건으로 전체 판매 이력 1회 조회
    sales_rows = session.exec(
        select(DailySalesHistory).where(
            DailySalesHistory.center_cd == center_cd,
            DailySalesHistory.sales_date >= window_start,
        )
    ).all()
    sales_by_sku: dict[str, dict[date, int]] = {}
    for r in sales_rows:
        if r.sku_id in sku_set:
            sales_by_sku.setdefault(r.sku_id, {})[r.sales_date] = r.sales_qty

    # 2. 활성 이벤트 전체 1회 조회
    cutoff_event = today - timedelta(days=14)
    event_rows = session.exec(
        select(Event).where(Event.end_dt >= cutoff_event)
    ).all()
    event_dates_by_sku: dict[str, set[date]] = {}
    for ev in event_rows:
        if ev.sku_id not in sku_set:
            continue
        d = ev.start_dt.date() if hasattr(ev.start_dt, "date") else ev.start_dt
        end = ev.end_dt.date() if hasattr(ev.end_dt, "date") else ev.end_dt
        s = event_dates_by_sku.setdefault(ev.sku_id, set())
        while d <= end:
            s.add(d)
            d += timedelta(days=1)

    return {
        sku_id: _compute_speed_from_map(
            sales_by_sku.get(sku_id, {}),
            event_dates_by_sku.get(sku_id, set()),
            all_dates,
            cutoff_recent,
        )
        for sku_id in sku_ids
    }


def update_sku_sales_summary(
    sku_id: str, center_cd: str, session: Session, commit: bool = True
) -> SkuSalesSummary:
    speed = calculate_sales_speed(sku_id, center_cd, session)

    summary = session.exec(
        select(SkuSalesSummary).where(
            SkuSalesSummary.sku_id == sku_id,
            SkuSalesSummary.center_cd == center_cd,
        )
    ).first()

    if summary:
        summary.base_daily_avg = speed["base_daily_avg"]
        summary.recent_daily_avg = speed["recent_daily_avg"]
        summary.trend_coef = speed["trend_coef"]
        summary.adjusted_daily = speed["adjusted_daily"]
    else:
        summary = SkuSalesSummary(
            sku_id=sku_id,
            center_cd=center_cd,
            base_daily_avg=speed["base_daily_avg"],
            recent_daily_avg=speed["recent_daily_avg"],
            trend_coef=speed["trend_coef"],
            adjusted_daily=speed["adjusted_daily"],
        )
        session.add(summary)

    if commit:
        session.commit()
    return summary


def update_all_sales_summaries(
    center_cd: str, session: Session, sku_ids: list[str] | None = None
) -> int:
    """해당 센터의 SKU 판매속도 갱신. sku_ids 지정 시 해당 SKU만 갱신. 반환: 처리 SKU 수"""
    if sku_ids is None:
        sku_ids = list(session.exec(
            select(DailySalesHistory.sku_id).where(
                DailySalesHistory.center_cd == center_cd
            ).distinct()
        ).all())

    if not sku_ids:
        return 0

    # 배치 계산: 쿼리 2회로 전체 SKU 판매속도 계산
    speeds = _batch_calculate_sales_speeds(center_cd, sku_ids, session)

    # 벌크 INSERT OR REPLACE로 SkuSalesSummary 일괄 갱신
    rows = [
        {
            "sku_id": sku_id,
            "center_cd": center_cd,
            "base_daily_avg": sp["base_daily_avg"],
            "recent_daily_avg": sp["recent_daily_avg"],
            "trend_coef": sp["trend_coef"],
            "adjusted_daily": sp["adjusted_daily"],
        }
        for sku_id, sp in speeds.items()
    ]
    session.execute(sa_insert(SkuSalesSummary).prefix_with("OR REPLACE"), rows)
    session.commit()
    return len(sku_ids)
