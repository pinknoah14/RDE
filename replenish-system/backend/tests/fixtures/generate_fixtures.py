"""
운영 규모 샘플 데이터 생성기
  SKU:    25,000개
  재고행: 약 60,000행 (피킹존 + 보충존)
  판매행: 약 350,000행 (25,000 SKU × 14일)

CSV 포맷:
  inventory_sample.csv — 재고현황 (bin_id_pattern ^15[A-Z]{2}\\d{7}$ 준수)
  pivot_sample.csv     — 판매데이터 (상품코드, 센터, 판매일자, 판매수량)
"""

import random
from datetime import date, timedelta
from pathlib import Path

import polars as pl

OUTPUT_DIR = Path(__file__).parent
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ZONES = {
    # zone_code: (access_type, floor, is_scattered, n_aisles)
    "RA": ("FORKLIFT", 0, False, 20),
    "RB": ("FORKLIFT", 0, False, 15),
    "SF": ("WALKING",  1, False, 10),
    "SM": ("WALKING",  1, True,   5),
    "PW": ("FORKLIFT", 0, True,   5),
    "NC": ("FORKLIFT", 0, False, 12),
    "PA": ("WALKING",  1, False,  8),
    "SC": ("FORKLIFT", 0, True,   4),
}

PICKING_ZONES  = ["RA", "RB", "NC"]
REPLENISH_ZONES = ["RA", "RB", "PW", "SF", "SM"]


def make_bin(zone: str, aisle: int, bay: int, level: int) -> str:
    """15{ZZ}{aa}{bb}{lll} — matches ^15[A-Z]{2}\\d{7}$"""
    return f"15{zone}{aisle:02d}{bay:02d}{level:03d}"


def generate_inventory_csv(n_sku: int = 25_000) -> Path:
    today = date.today()
    rows: list[dict] = []

    for idx in range(n_sku):
        sku_id   = f"SKU{idx:06d}"
        sku_name = f"상품_{idx:06d}"
        center   = "GGH1"

        # 피킹존 (80% 확률)
        if random.random() < 0.80:
            zone  = random.choice(PICKING_ZONES)
            aisle = random.randint(1, ZONES[zone][3])
            bay   = random.randint(1, 10)
            level = random.randint(1, 5)
            dd    = random.randint(-5, 365)
            rows.append({
                "상품코드": sku_id, "센터상품명": sku_name, "센터": center,
                "지번": make_bin(zone, aisle, bay, level), "존": zone,
                "피킹가능": "피킹가능",
                "가용수량": random.randint(0, 50),
                "입수": random.choice([6, 8, 10, 12, 20, 24]),
                "박스수": random.randint(0, 5),
                "박스잔량": random.randint(0, 20),
                "센터 판매마감일": (today + timedelta(days=dd)).isoformat(),
                "판매마감일수": dd,
                "유통가능일수": dd + 30,
                "입고일자": (today - timedelta(days=random.randint(0, 90))).isoformat(),
            })

        # 보충존 (60% 확률, 혼적 포함)
        if random.random() < 0.60:
            n_lots = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
            for _ in range(n_lots):
                zone  = random.choice(REPLENISH_ZONES)
                aisle = random.randint(1, ZONES[zone][3])
                bay   = random.randint(1, 15)
                level = random.randint(1, 8)
                dd    = random.randint(1, 300)
                unit  = random.choice([6, 8, 10, 12, 20, 24])
                boxes = random.randint(1, 20)
                rows.append({
                    "상품코드": sku_id, "센터상품명": sku_name, "센터": center,
                    "지번": make_bin(zone, aisle, bay, level), "존": zone,
                    "피킹가능": "피킹불가",
                    "가용수량": unit * boxes,
                    "입수": unit,
                    "박스수": boxes,
                    "박스잔량": random.randint(0, unit - 1),
                    "센터 판매마감일": (today + timedelta(days=dd)).isoformat(),
                    "판매마감일수": dd,
                    "유통가능일수": dd + 30,
                    "입고일자": (today - timedelta(days=random.randint(0, 60))).isoformat(),
                })

    path = OUTPUT_DIR / "inventory_sample.csv"
    pl.DataFrame(rows).write_csv(path)
    print(f"✅ 재고현황 CSV: {len(rows):,}행 → {path}")
    return path


def generate_sales_csv(n_sku: int = 25_000, days: int = 14) -> Path:
    """판매 데이터 CSV (상품코드, 센터, 판매일자, 판매수량) — parse_outbound_csv 호환"""
    today = date.today()
    rows: list[dict] = []

    for idx in range(n_sku):
        sku_id     = f"SKU{idx:06d}"
        daily_base = random.randint(0, 200)

        for d in range(days):
            sales_date = today - timedelta(days=d)
            qty = 0 if random.random() < 0.10 else max(0, int(daily_base * random.uniform(0.5, 1.8)))
            rows.append({
                "상품코드":  sku_id,
                "센터":      "GGH1",
                "판매일자":  sales_date.isoformat(),
                "판매수량":  qty,
            })

    path = OUTPUT_DIR / "pivot_sample.csv"
    pl.DataFrame(rows).write_csv(path)
    print(f"✅ 판매 CSV: {len(rows):,}행 → {path}")
    return path


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25_000
    print(f"샘플 데이터 생성 시작... (SKU {n:,}개)")
    generate_inventory_csv(n)
    generate_sales_csv(n)
    print("완료.")
