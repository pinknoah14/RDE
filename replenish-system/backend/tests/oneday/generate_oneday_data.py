"""
원 데이 시뮬레이션 데이터 생성기

재고 스냅샷을 시간(depletion_hours) 함수로 동적 생성.
매 웨이브 슬롯마다 새로운 CSV를 생성하여
실제 운영처럼 주문 인입 후 최신 데이터 반영을 재현.

# CSV 형식 (파서 요구사항)
# - 재고: 상품코드, 센터상품명, 센터, 지번, 존, 피킹가능, 가용수량, 입수, ...
# - 출고: 상품코드, 센터, 판매일자, 판매수량  (long format)
# - 피벗: 상품코드, 센터, [YYYY-MM-DD, ...]  (wide format, one col per date)
"""
import polars as pl
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
TODAY  = date.today()
OUTDIR = Path("tests/oneday/data")
OUTDIR.mkdir(parents=True, exist_ok=True)

N_SKU = 300  # CI 기준 규모 (실운영은 25,000)

ZONES_CFG = {
    "RA": {"floor": 0, "scattered": False, "aisles": 20, "access": "FORKLIFT", "channel": "R존"},
    "RB": {"floor": 0, "scattered": False, "aisles": 15, "access": "FORKLIFT", "channel": "R존"},
    "NC": {"floor": 0, "scattered": False, "aisles": 12, "access": "FORKLIFT", "channel": "R존"},
    "PW": {"floor": 0, "scattered": True,  "aisles": 5,  "access": "FORKLIFT", "channel": "R존"},
    "SF": {"floor": 1, "scattered": False, "aisles": 10, "access": "WALKING",  "channel": "P존"},
    "SM": {"floor": 1, "scattered": True,  "aisles": 5,  "access": "WALKING",  "channel": "P존"},
}
PICKING_ZONES = ["RA", "RB", "NC"]
REPLEN_ZONES  = list(ZONES_CFG.keys())


def make_bin(zone, aisle, bay, level=1):
    return f"15{zone}{aisle:02d}{bay:02d}{level:03d}"


# SKU 마스터 (고정 — seed 고정으로 재현 가능)
SKU_MASTER = []
for i in range(N_SKU):
    daily = (
        random.randint(80, 200) if i < N_SKU * 0.05 else
        random.randint(20, 80)  if i < N_SKU * 0.2  else
        random.randint(5, 20)   if i < N_SKU * 0.6  else
        random.randint(0, 5)
    )
    unit   = random.choice([6, 8, 10, 12, 20, 24])
    zone_p = random.choice(PICKING_ZONES)
    aisle  = random.randint(1, ZONES_CFG[zone_p]["aisles"])
    SKU_MASTER.append({
        "sku_id":   f"SKU{i:05d}",
        "sku_name": f"상품_{i:05d}",
        "daily":    daily,
        "unit":     unit,
        "zone_p":   zone_p,
        "bin_p":    make_bin(zone_p, aisle, random.randint(1, 10)),
        "replen_bins": [
            {
                "bin_r": make_bin(
                    random.choice(REPLEN_ZONES),
                    random.randint(1, 10),
                    random.randint(1, 15),
                ),
                "zone_r":        random.choice(REPLEN_ZONES),
                "box_count":     random.randint(1, 15),
                "deadline_days": (
                    random.randint(3, 15) if i < 10
                    else random.randint(7, 300)
                ),
            }
            for _ in range(random.choices([0, 1, 2, 3], weights=[10, 55, 30, 5])[0])
        ],
    })


def gen_snapshot(
    time_label: str,
    elapsed_hours: float,
    replenished_skus: set = None,
    force_shortage_count: int = 0,
):
    """
    elapsed_hours: 09:30 기준 경과 시간
    replenished_skus: 이미 보충 완료된 SKU 집합 (피킹존 재고 회복)
    force_shortage_count: 해당 시각에 강제로 피킹존 재고=0 처리할 SKU 수.
                         상위 N개 SKU의 avail_p=0 → WMS 버그 재현(피킹존 행 자체 삭제됨)
    """
    if replenished_skus is None:
        replenished_skus = set()

    rows = []
    for i, sku in enumerate(SKU_MASTER):
        daily   = sku["daily"]
        unit    = sku["unit"]
        hourly  = daily / 16  # 16시간 운영 기준
        sold    = hourly * elapsed_hours

        avail_init = int(daily * random.uniform(0.5, 2.5))

        # 의도적 시나리오
        if i < 5:
            avail_init = 0
        elif i < 15:
            avail_init = int(daily * random.uniform(0.1, 0.5))

        avail_p = max(0, int(avail_init - sold))

        if sku["sku_id"] in replenished_skus:
            avail_p = int(daily * random.uniform(1.0, 2.0))

        # 강제 품절 주입 (14:00 미할당 폭발 시나리오)
        if i < force_shortage_count:
            avail_p = 0

        deadline_p = random.randint(10, 365)

        if avail_p > 0:
            rows.append({
                "상품코드": sku["sku_id"], "센터상품명": sku["sku_name"],
                "센터": "GGH1", "지번": sku["bin_p"], "존": sku["zone_p"],
                "피킹가능": "피킹가능",
                "가용수량": avail_p, "입수": unit,
                "박스수": avail_p // unit, "박스잔량": avail_p % unit,
                "센터 판매마감일": (TODAY + timedelta(days=deadline_p)).isoformat(),
                "판매마감일수": deadline_p, "유통가능일수": deadline_p + 30,
                "입고일자": (TODAY - timedelta(days=random.randint(0, 60))).isoformat(),
            })

        for replen in sku["replen_bins"]:
            rows.append({
                "상품코드": sku["sku_id"], "센터상품명": sku["sku_name"],
                "센터": "GGH1",
                "지번": replen["bin_r"], "존": replen["zone_r"],
                "피킹가능": "피킹불가",
                "가용수량": unit * replen["box_count"], "입수": unit,
                "박스수": replen["box_count"], "박스잔량": 0,
                "센터 판매마감일": (TODAY + timedelta(days=replen["deadline_days"])).isoformat(),
                "판매마감일수": replen["deadline_days"],
                "유통가능일수": replen["deadline_days"] + 30,
                "입고일자": (TODAY - timedelta(days=random.randint(0, 30))).isoformat(),
            })

    path = OUTDIR / f"inventory_{time_label}.csv"
    pl.DataFrame(rows).write_csv(path)
    return path, len(rows)


def gen_outbound(time_label: str, elapsed_hours: float):
    """
    출고현황 CSV — 파서 요구 형식: 상품코드, 센터, 판매일자, 판매수량
    (long format: SKU × 날짜별 1행)
    """
    rows = []
    for sku in SKU_MASTER:
        daily = sku["daily"]
        base  = max(0, int(daily * (elapsed_hours / 16)))
        for day in range(7):
            dt  = TODAY - timedelta(days=day)
            qty = random.randint(0, max(1, base)) if base > 0 else random.randint(0, max(1, daily // 3))
            if qty > 0:
                rows.append({
                    "상품코드":  sku["sku_id"],
                    "센터":      "GGH1",
                    "판매일자":  dt.isoformat(),
                    "판매수량":  qty,
                })
    path = OUTDIR / f"outbound_{time_label}.csv"
    pl.DataFrame(rows).write_csv(path)
    return path


def gen_pivot():
    """
    피벗 판매 CSV — 파서 요구 형식: 상품코드, 센터, [YYYY-MM-DD, ...]
    (wide format: SKU별 1행, 날짜별 열)
    """
    date_cols = [(TODAY - timedelta(days=d)).isoformat() for d in range(28)]
    rows = []
    for sku in SKU_MASTER:
        row = {"상품코드": sku["sku_id"], "센터": "GGH1"}
        for col in date_cols:
            qty = 0 if random.random() < 0.08 else max(
                0, int(sku["daily"] * random.uniform(0.5, 1.5))
            )
            row[col] = qty
        rows.append(row)
    path = OUTDIR / "pivot.csv"
    pl.DataFrame(rows).write_csv(path)
    return path


def time_to_elapsed(time_str: str) -> float:
    """09:30 기준 경과 시간(시간 단위) 계산"""
    h, m = map(int, time_str.split(":"))
    base  = 9 * 60 + 30
    return ((h * 60 + m) - base) / 60


if __name__ == "__main__":
    print("원 데이 데이터 생성...")
    gen_pivot()
    print("피벗 생성 완료")
    print(f"SKU 마스터: {N_SKU}개")
    print("CSV는 시뮬레이션 실행 중 동적 생성됨")
