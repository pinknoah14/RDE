"""
실규모(25,000 SKU) 원 데이 시뮬레이션 데이터 생성기

generate_oneday_data.py의 확장판.
목표: ~50,000행 재고 CSV, ~20MB 파일 크기.
"""
import polars as pl
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
TODAY  = date.today()
OUTDIR = Path("tests/oneday/data/realscale")
OUTDIR.mkdir(parents=True, exist_ok=True)

N_SKU = 25_000

ZONES_CFG = {
    "RA": {"floor": 0, "scattered": False, "aisles": 20, "access": "FORKLIFT", "channel": "R존"},
    "RB": {"floor": 0, "scattered": False, "aisles": 15, "access": "FORKLIFT", "channel": "R존"},
    "NC": {"floor": 0, "scattered": False, "aisles": 12, "access": "FORKLIFT", "channel": "R존"},
    "PW": {"floor": 0, "scattered": True,  "aisles": 5,  "access": "FORKLIFT", "channel": "R존"},
    "SF": {"floor": 1, "scattered": False, "aisles": 10, "access": "WALKING",  "channel": "P존"},
    "SM": {"floor": 1, "scattered": True,  "aisles": 5,  "access": "WALKING",  "channel": "P존"},
}
PICKING_ZONES = ["RA", "RB", "NC", "SF", "SM"]
REPLEN_ZONES  = list(ZONES_CFG.keys())


def make_bin(zone: str, aisle: int, bay: int, level: int = 1) -> str:
    return f"15{zone}{aisle:02d}{bay:02d}{level:03d}"


# SKU 마스터 (25,000개 — seed 고정으로 재현 가능)
SKU_MASTER: list[dict] = []
for _i in range(N_SKU):
    _daily = (
        random.randint(80, 200) if _i < N_SKU * 0.05 else
        random.randint(20, 80)  if _i < N_SKU * 0.2  else
        random.randint(5, 20)   if _i < N_SKU * 0.6  else
        random.randint(0, 5)
    )
    _unit   = random.choice([6, 8, 10, 12, 20, 24])
    _zone_p = random.choice(PICKING_ZONES)
    _aisle  = random.randint(1, ZONES_CFG[_zone_p]["aisles"])
    SKU_MASTER.append({
        "sku_id":   f"SKU{_i:05d}",
        "sku_name": f"상품_{_i:05d}",
        "daily":    _daily,
        "unit":     _unit,
        "zone_p":   _zone_p,
        "bin_p":    make_bin(_zone_p, _aisle, random.randint(1, 10)),
        "replen_bins": [
            {
                "bin_r":        make_bin(
                    random.choice(REPLEN_ZONES),
                    random.randint(1, 10),
                    random.randint(1, 15),
                ),
                "zone_r":        random.choice(REPLEN_ZONES),
                "box_count":     random.randint(1, 15),
                "deadline_days": random.randint(3, 15) if _i < 10 else random.randint(7, 300),
            }
            for _ in range(random.choices([0, 1, 2, 3], weights=[10, 55, 30, 5])[0])
        ],
    })


def gen_snapshot(
    time_label: str,
    elapsed_hours: float,
    replenished_skus: set | None = None,
    force_shortage_count: int = 0,
) -> tuple[Path, int]:
    if replenished_skus is None:
        replenished_skus = set()

    rows: list[dict] = []
    for i, sku in enumerate(SKU_MASTER):
        daily  = sku["daily"]
        unit   = sku["unit"]
        sold   = (daily / 16) * elapsed_hours

        avail_init = int(daily * random.uniform(0.5, 2.5))
        if i < 5:
            avail_init = 0
        elif i < 15:
            avail_init = int(daily * random.uniform(0.1, 0.5))

        avail_p = max(0, int(avail_init - sold))
        if sku["sku_id"] in replenished_skus:
            avail_p = int(daily * random.uniform(1.0, 2.0))
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


def gen_outbound(time_label: str, elapsed_hours: float) -> Path:
    rows: list[dict] = []
    for sku in SKU_MASTER:
        daily = sku["daily"]
        base  = max(0, int(daily * (elapsed_hours / 16)))
        for day in range(7):
            dt  = TODAY - timedelta(days=day)
            qty = random.randint(0, max(1, base)) if base > 0 else random.randint(0, max(1, daily // 3))
            if qty > 0:
                rows.append({
                    "상품코드": sku["sku_id"],
                    "센터":     "GGH1",
                    "판매일자": dt.isoformat(),
                    "판매수량": qty,
                })
    path = OUTDIR / f"outbound_{time_label}.csv"
    pl.DataFrame(rows).write_csv(path)
    return path


def gen_pivot() -> Path:
    date_cols = [(TODAY - timedelta(days=d)).isoformat() for d in range(28)]
    rows: list[dict] = []
    for sku in SKU_MASTER:
        row: dict = {"상품코드": sku["sku_id"], "센터": "GGH1"}
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
    h, m = map(int, time_str.split(":"))
    return ((h * 60 + m) - (9 * 60 + 30)) / 60


if __name__ == "__main__":
    import time as _time

    print(f"실규모 데이터 생성 시작 (SKU {N_SKU:,}개)")

    t0 = _time.time()
    piv = gen_pivot()
    print(f"  피벗 CSV:  {piv.stat().st_size / 1_048_576:.1f}MB  ({_time.time()-t0:.1f}s)")

    t0 = _time.time()
    inv, rows = gen_snapshot("13:00", elapsed_hours=3.5)
    print(f"  재고 CSV:  {inv.stat().st_size / 1_048_576:.1f}MB  {rows:,}행  ({_time.time()-t0:.1f}s)")

    t0 = _time.time()
    out = gen_outbound("13:00", elapsed_hours=3.5)
    print(f"  출고 CSV:  {out.stat().st_size / 1_048_576:.1f}MB  ({_time.time()-t0:.1f}s)")

    print(f"완료 → {OUTDIR}")
