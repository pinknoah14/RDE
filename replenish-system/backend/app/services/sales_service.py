from datetime import date, timedelta

import polars as pl
from sqlmodel import Session, select

from app.models.sku import DailySalesHistory, SkuSalesSummary
from app.models.event import Event


def upsert_daily_sales(center_cd: str, sales_df: pl.DataFrame, session: Session) -> int:
    """
    판매 DataFrame(상품코드, 센터, 판매일자, 판매수량) → daily_sales_history UPSERT.
    반환: 처리 행 수
    """
    count = 0
    for row in sales_df.iter_rows(named=True):
        sku_id = row["상품코드"]
        ctr = row.get("센터") or center_cd
        sales_date_str = row["판매일자"]
        qty = int(row["판매수량"] or 0)

        try:
            sales_date = date.fromisoformat(sales_date_str)
        except (ValueError, TypeError):
            continue

        existing = session.exec(
            select(DailySalesHistory).where(
                DailySalesHistory.sku_id == sku_id,
                DailySalesHistory.center_cd == ctr,
                DailySalesHistory.sales_date == sales_date,
            )
        ).first()

        if existing:
            existing.sales_qty = qty
        else:
            session.add(DailySalesHistory(
                sku_id=sku_id,
                center_cd=ctr,
                sales_date=sales_date,
                sales_qty=qty,
            ))
        count += 1

    session.commit()
    return count


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

    rows = session.exec(
        select(DailySalesHistory).where(
            DailySalesHistory.sku_id == sku_id,
            DailySalesHistory.center_cd == center_cd,
            DailySalesHistory.sales_date >= window_start,
        ).order_by(DailySalesHistory.sales_date)
    ).all()

    if not rows:
        return {"base_daily_avg": 0.0, "trend_coef": 1.0, "adjusted_daily": 0.0, "recent_daily_avg": 0.0}

    event_dates = _get_event_dates(sku_id, center_cd, session)

    # Build dict: date → qty
    sales_map: dict[date, int] = {r.sales_date: r.sales_qty for r in rows}
    all_dates = [window_start + timedelta(days=i) for i in range(14)]

    # Detect stockout periods: consecutive days with 0 sales (unassigned_qty also 0)
    stockout_mask: set[date] = set()
    zero_streak: list[date] = []
    for d in all_dates:
        qty = sales_map.get(d, 0)
        if qty == 0:
            zero_streak.append(d)
        else:
            zero_streak = []
    # Mark consecutive trailing zeros as stockout
    for d in reversed(all_dates):
        qty = sales_map.get(d, 0)
        if qty == 0:
            stockout_mask.add(d)
        else:
            break

    valid_days: list[int] = []
    recent_7: list[int] = []   # days 0-6 ago
    prior_7: list[int] = []    # days 7-13 ago
    cutoff_recent = today - timedelta(days=7)

    for i, d in enumerate(all_dates):
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

    if prior_avg > 0:
        trend_coef = max(0.5, min(2.0, recent_avg / prior_avg))
    else:
        trend_coef = 1.0

    adjusted_daily = base_daily_avg * trend_coef

    return {
        "base_daily_avg": round(base_daily_avg, 4),
        "trend_coef": round(trend_coef, 4),
        "adjusted_daily": round(adjusted_daily, 4),
        "recent_daily_avg": round(recent_avg, 4),
    }


def update_sku_sales_summary(sku_id: str, center_cd: str, session: Session) -> SkuSalesSummary:
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

    session.commit()
    return summary


def update_all_sales_summaries(center_cd: str, session: Session) -> int:
    """해당 센터의 모든 SKU 판매속도 갱신. 반환: 처리 SKU 수"""
    skus = session.exec(
        select(DailySalesHistory.sku_id).where(
            DailySalesHistory.center_cd == center_cd
        ).distinct()
    ).all()
    for sku_id in skus:
        update_sku_sales_summary(sku_id, center_cd, session)
    return len(skus)
