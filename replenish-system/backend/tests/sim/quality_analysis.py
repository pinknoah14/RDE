"""
알고리즘 추천 품질 심층 분석
실제 운영자가 확인할 항목을 자동으로 검사
"""
import app.models  # noqa: F401
from app.core.database import engine
from sqlmodel import Session, select
from app.models.task import ReplenishCandidate, ReplenishTaskLocation
from app.models.sku import SkuSalesSummary
import json

with Session(engine) as s:
    candidates = s.exec(
        select(ReplenishCandidate)
        .order_by(ReplenishCandidate.risk_score.desc())
    ).all()

    print("=" * 60)
    print("알고리즘 품질 심층 분석")
    print("=" * 60)

    # 1. 위험도 분포
    levels = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for c in candidates:
        levels[c.risk_level] = levels.get(c.risk_level, 0) + 1
    print(f"\n📊 위험도 분포:")
    total = len(candidates)
    for lv, cnt in levels.items():
        pct = (cnt / total * 100) if total else 0
        bar = "█" * int(pct / 5)
        print(f"  {lv:8s} {cnt:3d}개 ({pct:4.1f}%) {bar}")

    # 2. 보충지번 수 분포
    bin_counts = {}
    for c in candidates:
        bins = json.loads(c.matched_bins_json or "[]")
        n = len(bins)
        bin_counts[n] = bin_counts.get(n, 0) + 1
    print(f"\n📦 보충지번 수 분포:")
    for n, cnt in sorted(bin_counts.items()):
        print(f"  {n}개 지번: {cnt}건")

    # 3. proximity_score 분포
    score_dist = {1: 0, 2: 0, 3: 0, 4: 0}
    for c in candidates:
        bins = json.loads(c.matched_bins_json or "[]")
        for b in bins:
            sc = b.get("proximity_score", 1)
            score_dist[sc] = score_dist.get(sc, 0) + 1
    print(f"\n📍 Proximity Score 분포 (좌표 미설정 → 1 또는 2 예상):")
    icons = {4: "🟢", 3: "🟠", 2: "🟡", 1: "⚪"}
    for sc in [4, 3, 2, 1]:
        cnt = score_dist.get(sc, 0)
        print(f"  {icons[sc]} score {sc}: {cnt}건")

    # 4. today_sales 분포
    ts_zero = sum(1 for c in candidates if c.today_sales == 0)
    ts_nonzero = total - ts_zero
    print(f"\n📈 today_sales:")
    print(f"  0인 후보:  {ts_zero}개 (outbound 데이터 없을 때 정상)")
    print(f"  >0인 후보: {ts_nonzero}개")

    # 5. FEFO 검증 상세
    fefo_ok = 0
    fefo_fail = 0
    for c in candidates:
        bins = json.loads(c.matched_bins_json or "[]")
        for i in range(len(bins) - 1):
            if (bins[i].get("deadline_days") or 9999) <= (bins[i+1].get("deadline_days") or 9999):
                fefo_ok += 1
            else:
                fefo_fail += 1
    print(f"\n🗓️  FEFO 검증:")
    print(f"  정렬 올바름: {fefo_ok}건")
    print(f"  FEFO 위반:   {fefo_fail}건 {'❌' if fefo_fail else '✅'}")

    # 6. CRITICAL 상위 10개 출력
    print(f"\n🔴 CRITICAL 상위 10개 (운영자 확인용):")
    critical = [c for c in candidates if c.risk_level == "CRITICAL"][:10]
    for i, c in enumerate(critical, 1):
        bins = json.loads(c.matched_bins_json or "[]")
        sales = s.exec(
            select(SkuSalesSummary)
            .where(SkuSalesSummary.sku_id == c.sku_id)
        ).first()
        daily = sales.adjusted_daily if sales else 0
        print(f"\n  {i:2d}. [{c.risk_score:.0f}점] {c.sku_name}")
        print(f"      피킹: {c.picking_bin} | 재고: {c.avg_daily_sales:.0f}개 | 일평균: {daily:.1f}")
        eta = f"{c.eta_hours:.1f}h" if c.eta_hours else "품절"
        print(f"      예상소진: {eta} | 보충지번 {len(bins)}개")
        if bins:
            b = bins[0]
            print(f"      1순위: {b['replenish_bin']} {b['allocated_qty']}개 (D-{b.get('deadline_days','?')})")

print("\n분석 완료")
