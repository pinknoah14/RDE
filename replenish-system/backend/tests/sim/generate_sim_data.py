"""
운영 시뮬레이션 데이터 생성기

실제 센터 운영 패턴 반영:
  - SKU별 판매속도 편차 (파레토: 상위 20%가 80% 판매)
  - 재고 고갈 직전 SKU 의도적 포함 (CRITICAL 검증용)
  - 혼적 SKU 포함 (동일 지번 + 다른 판매마감일)
  - 다중 피킹지번 SKU 포함
  - 판매마감 임박 SKU 포함
  - 메자닌 존 SKU 포함
  - PW 산재 존 SKU 포함
"""

import polars as pl
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)  # 재현 가능한 결과
SIM_DIR = Path("tests/sim/data")
SIM_DIR.mkdir(parents=True, exist_ok=True)

TODAY = date.today()
N_SKU = 500  # 시뮬레이션 규모 (빠른 실행)

# 존 구성 (실제 센터 반영)
ZONES = {
    "RA": {"floor": 0, "scattered": False, "aisles": 20, "access": "FORKLIFT"},
    "RB": {"floor": 0, "scattered": False, "aisles": 15, "access": "FORKLIFT"},
    "SF": {"floor": 1, "scattered": False, "aisles": 10, "access": "WALKING"},
    "SM": {"floor": 1, "scattered": True,  "aisles": 5,  "access": "WALKING"},
    "PW": {"floor": 0, "scattered": True,  "aisles": 5,  "access": "FORKLIFT"},
    "NC": {"floor": 0, "scattered": False, "aisles": 12, "access": "FORKLIFT"},
}
PICKING_ZONES = ["RA", "RB", "NC"]
REPLEN_ZONES  = list(ZONES.keys())

def make_bin(zone, aisle, bay, level=1):
    return f"15{zone}{aisle:02d}{bay:02d}{level:03d}"

def generate_inventory(n_sku=N_SKU):
    rows = []

    # 파레토 판매속도 분포
    sales_speeds = [0] * n_sku
    for i in range(n_sku):
        if i < n_sku * 0.05:      # 상위 5%: 고속 판매
            sales_speeds[i] = random.randint(80, 200)
        elif i < n_sku * 0.20:    # 상위 20%: 중속
            sales_speeds[i] = random.randint(20, 80)
        elif i < n_sku * 0.60:    # 중간 40%: 저속
            sales_speeds[i] = random.randint(5, 20)
        else:                      # 하위 40%: 매우 저속
            sales_speeds[i] = random.randint(0, 5)

    for sku_idx in range(n_sku):
        sku_id = f"SKU{sku_idx:05d}"
        daily  = sales_speeds[sku_idx]
        unit   = random.choice([6, 8, 10, 12, 20, 24])

        # === 피킹존 ===
        zone_p = random.choice(PICKING_ZONES)
        aisle  = random.randint(1, ZONES[zone_p]["aisles"])
        bay    = random.randint(1, 10)
        bin_p  = make_bin(zone_p, aisle, bay)
        deadline_p = random.randint(1, 365)

        # 의도적 시나리오 삽입
        if sku_idx < 10:
            # CRITICAL: 재고 거의 없음
            avail_p = random.randint(0, unit)
        elif sku_idx < 30:
            # HIGH: 1~2일치 재고
            avail_p = int(daily * random.uniform(1.0, 2.0))
        elif sku_idx < 60:
            # MEDIUM: 3~5일치
            avail_p = int(daily * random.uniform(3.0, 5.0))
        else:
            avail_p = int(daily * random.uniform(5.0, 20.0))

        if daily == 0:
            avail_p = random.randint(10, 100)

        rows.append({
            "상품코드": sku_id, "센터상품명": f"상품_{sku_idx:05d}",
            "센터": "GGH1", "지번": bin_p, "존": zone_p,
            "피킹가능": "피킹가능",
            "가용수량": avail_p, "입수": unit,
            "박스수": avail_p // unit, "박스잔량": avail_p % unit,
            "센터 판매마감일": (TODAY + timedelta(days=deadline_p)).isoformat(),
            "판매마감일수": deadline_p, "유통가능일수": deadline_p + 30,
            "입고일자": (TODAY - timedelta(days=random.randint(0, 60))).isoformat(),
        })

        # 다중 피킹지번 (SKU 10~12: 시나리오)
        if 10 <= sku_idx < 13:
            aisle2 = (aisle % ZONES[zone_p]["aisles"]) + 1
            bin_p2 = make_bin(zone_p, aisle2, bay + 1)
            rows.append({**rows[-1], "지번": bin_p2, "가용수량": random.randint(5, 30)})

        # === 보충존 (혼적 포함) ===
        n_lots = random.choices([0, 1, 2, 3], weights=[15, 55, 25, 5])[0]
        for lot_idx in range(n_lots):
            zone_r = random.choice(REPLEN_ZONES)
            cfg_r  = ZONES[zone_r]
            aisle_r = random.randint(1, cfg_r["aisles"])
            bay_r   = random.randint(1, 15)
            bin_r   = make_bin(zone_r, aisle_r, bay_r)

            # 판매마감 임박 시나리오 (SKU 5~9)
            if 5 <= sku_idx < 10 and lot_idx == 0:
                deadline_r = random.randint(3, 15)   # 임박
            else:
                deadline_r = random.randint(7, 300)

            box_count = random.randint(1, 20)
            rows.append({
                "상품코드": sku_id, "센터상품명": f"상품_{sku_idx:05d}",
                "센터": "GGH1", "지번": bin_r, "존": zone_r,
                "피킹가능": "피킹불가",
                "가용수량": unit * box_count, "입수": unit,
                "박스수": box_count, "박스잔량": 0,
                "센터 판매마감일": (TODAY + timedelta(days=deadline_r)).isoformat(),
                "판매마감일수": deadline_r, "유통가능일수": deadline_r + 30,
                "입고일자": (TODAY - timedelta(days=random.randint(0, 60))).isoformat(),
            })

    df = pl.DataFrame(rows)
    path = SIM_DIR / "inventory.csv"
    df.write_csv(path)
    print(f"재고 CSV: {len(df):,}행 → {path}")
    return path


def generate_outbound(n_sku=N_SKU):
    """출고현황 CSV — 파서 형식: 상품코드, 센터, 판매일자, 판매수량 (long format)"""
    rows = []
    for sku_idx in range(n_sku):
        sku_id = f"SKU{sku_idx:05d}"
        base = max(0, 200 - sku_idx // 3)

        for day_offset in range(7):
            dt = TODAY - timedelta(days=day_offset)
            qty = 0 if random.random() < 0.05 else max(0,
                int(base * random.uniform(0.6, 1.4)))
            rows.append({
                "상품코드": sku_id, "센터": "GGH1",
                "판매일자": dt.isoformat(), "판매수량": qty,
            })

    df = pl.DataFrame(rows)
    path = SIM_DIR / "outbound.csv"
    df.write_csv(path)
    print(f"출고 CSV: {len(df):,}행 → {path}")
    return path


def generate_pivot(n_sku=N_SKU):
    """피벗 판매 CSV — 파서 형식: 상품코드, 센터, [YYYY-MM-DD, ...] (wide format)"""
    date_cols = [(TODAY - timedelta(days=d)).isoformat() for d in range(28)]

    rows = []
    for sku_idx in range(n_sku):
        sku_id = f"SKU{sku_idx:05d}"
        base = max(0, 200 - sku_idx // 3)
        row = {"상품코드": sku_id, "센터": "GGH1"}
        for col in date_cols:
            qty = 0 if random.random() < 0.08 else max(0,
                int(base * random.uniform(0.6, 1.5)))
            row[col] = qty
        rows.append(row)

    df = pl.DataFrame(rows)
    path = SIM_DIR / "pivot.csv"
    df.write_csv(path)
    print(f"피벗 CSV: {len(df):,}행, {len(date_cols)+2}컬럼 → {path}")
    return path


if __name__ == "__main__":
    generate_inventory()
    generate_outbound()
    generate_pivot()
    print("시뮬레이션 데이터 생성 완료")
