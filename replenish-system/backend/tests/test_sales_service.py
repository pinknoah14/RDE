from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.models.sku import DailySalesHistory, SkuSalesSummary
from app.models.event import Event
from app.services.sales_service import (
    calculate_sales_speed,
    upsert_daily_sales,
    update_sku_sales_summary,
)
from app.services.sales_parser import parse_pivot_csv, parse_outbound_csv


# ---------------------------------------------------------------------------
# sales_parser tests
# ---------------------------------------------------------------------------

class TestParsePivotCsv:
    def test_wide_to_long(self):
        today = date.today()
        d1, d2 = str(today - timedelta(days=1)), str(today)
        csv_bytes = f"상품코드,센터,{d1},{d2}\nSKU001,GGH1,10,20\n".encode("utf-8")
        df = parse_pivot_csv(csv_bytes)
        assert len(df) == 2
        assert set(df.columns) >= {"상품코드", "센터", "판매일자", "판매수량"}
        assert 10 in df["판매수량"].to_list()

    def test_cp949_encoding(self):
        today = str(date.today())
        csv_bytes = f"상품코드,센터,{today}\nSKU001,GGH1,5\n".encode("cp949")
        df = parse_pivot_csv(csv_bytes)
        assert len(df) == 1


class TestParseOutboundCsv:
    def test_long_format(self):
        csv_bytes = "상품코드,센터,판매일자,판매수량\nSKU001,GGH1,2026-05-01,15\n".encode("utf-8")
        df = parse_outbound_csv(csv_bytes)
        assert len(df) == 1
        assert df["판매수량"][0] == 15


# ---------------------------------------------------------------------------
# sales_service tests
# ---------------------------------------------------------------------------

class TestUpsertDailySales:
    def test_insert_rows(self, session):
        today = date.today()
        d1 = str(today - timedelta(days=1))
        csv_bytes = f"상품코드,센터,{d1}\nSKU001,GGH1,10\n".encode("utf-8")
        from app.services.sales_parser import parse_pivot_csv
        df = parse_pivot_csv(csv_bytes)
        count = upsert_daily_sales("GGH1", df, session)
        assert count == 1

    def test_upsert_updates_existing(self, session):
        today = date.today()
        d = str(today)
        session.add(DailySalesHistory(
            sku_id="SKU001", center_cd="GGH1", sales_date=today, sales_qty=5
        ))
        session.commit()

        csv_bytes = f"상품코드,센터,{d}\nSKU001,GGH1,99\n".encode("utf-8")
        from app.services.sales_parser import parse_pivot_csv
        df = parse_pivot_csv(csv_bytes)
        upsert_daily_sales("GGH1", df, session)

        row = session.exec(
            select(DailySalesHistory).where(
                DailySalesHistory.sku_id == "SKU001",
                DailySalesHistory.sales_date == today,
            )
        ).first()
        assert row.sales_qty == 99


class TestCalculateSalesSpeed:
    def _seed_sales(self, session, sku_id="SKU001", days=14, qty=10):
        today = date.today()
        for i in range(days):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(
                sku_id=sku_id, center_cd="GGH1", sales_date=d, sales_qty=qty
            ))
        session.commit()

    def test_basic_calculation(self, session):
        self._seed_sales(session, qty=10)
        result = calculate_sales_speed("SKU001", "GGH1", session)
        assert result["base_daily_avg"] == 10.0
        assert result["adjusted_daily"] > 0

    def test_no_data_returns_zero(self, session):
        result = calculate_sales_speed("NONEXIST", "GGH1", session)
        assert result["base_daily_avg"] == 0.0
        assert result["adjusted_daily"] == 0.0

    def test_trend_coef_clamped_high(self, session):
        today = date.today()
        # Recent 7 days: 100/day, Prior 7 days: 10/day → ratio = 10 → clamped at 2.0
        for i in range(7):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_T", center_cd="GGH1", sales_date=d, sales_qty=100))
        for i in range(7, 14):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_T", center_cd="GGH1", sales_date=d, sales_qty=10))
        session.commit()
        result = calculate_sales_speed("SKU_T", "GGH1", session)
        assert result["trend_coef"] == 2.0

    def test_trend_coef_clamped_low(self, session):
        today = date.today()
        # Recent 7 days: 1/day, Prior 7 days: 100/day → ratio = 0.01 → clamped at 0.5
        for i in range(7):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_D", center_cd="GGH1", sales_date=d, sales_qty=1))
        for i in range(7, 14):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_D", center_cd="GGH1", sales_date=d, sales_qty=100))
        session.commit()
        result = calculate_sales_speed("SKU_D", "GGH1", session)
        assert result["trend_coef"] == 0.5

    def test_event_period_excluded(self, session):
        today = date.today()
        # Seed 14 days: normal 10/day except event period gets 0
        for i in range(14):
            d = today - timedelta(days=i)
            qty = 0 if i < 5 else 10
            session.add(DailySalesHistory(sku_id="SKU_E", center_cd="GGH1", sales_date=d, sales_qty=qty))
        from datetime import datetime
        event_start = today - timedelta(days=4)
        event_end = today
        session.add(Event(
            sku_id="SKU_E",
            event_type="PROMOTION",
            event_name="테스트이벤트",
            start_dt=datetime.combine(event_start, datetime.min.time()),
            end_dt=datetime.combine(event_end, datetime.min.time()),
            registered_by="테스트",
        ))
        session.commit()
        result = calculate_sales_speed("SKU_E", "GGH1", session)
        # Event days (0~4) are excluded, remaining days should be ~10
        assert result["base_daily_avg"] == pytest.approx(10.0, abs=1.0)

    def test_stockout_trailing_excluded(self, session):
        today = date.today()
        # Last 3 days: 0 (stockout), before that: 10
        for i in range(3):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_S", center_cd="GGH1", sales_date=d, sales_qty=0))
        for i in range(3, 14):
            d = today - timedelta(days=i)
            session.add(DailySalesHistory(sku_id="SKU_S", center_cd="GGH1", sales_date=d, sales_qty=10))
        session.commit()
        result = calculate_sales_speed("SKU_S", "GGH1", session)
        assert result["base_daily_avg"] == pytest.approx(10.0, abs=1.0)
