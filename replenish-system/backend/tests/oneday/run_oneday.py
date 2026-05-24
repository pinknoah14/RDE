"""
원 데이 운영 시뮬레이션
실제 주문 인입 주기 기반으로 전체 웨이브 사이클 실행
"""
import json
import sys
import time

sys.path.insert(0, ".")

# models 먼저 import (app 초기화 전 필수)
import app.models  # noqa: F401
from app.main import app  # noqa: E402
from app.core.database import engine, init_db  # noqa: E402

from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.worker import Worker
from app.services.slack_service import build_wave_messages_v2
from app.services.wave_builder import calculate_prestock_cutoff
from tests.oneday.generate_oneday_data import (
    gen_snapshot, gen_outbound, gen_pivot, time_to_elapsed, OUTDIR,
)

client = TestClient(app)


# ── 웨이브 슬롯 정의 ──────────────────────────────────────────
def build_wave_slots():
    slots = []

    h, m = 9, 30
    while (h * 60 + m) <= (20 * 60 + 30):
        slots.append({"time": f"{h:02d}:{m:02d}", "interval": 30, "type": "REGULAR"})
        m += 30
        if m >= 60:
            h += 1
            m -= 60

    h, m = 20, 45
    while (h * 60 + m) <= (23 * 60 + 0):
        slots.append({"time": f"{h:02d}:{m:02d}", "interval": 15, "type": "REGULAR"})
        m += 15
        if m >= 60:
            h += 1
            m -= 60

    slots.append({"time": "23:20", "interval": 100, "type": "PRESTOCK"})
    return slots


ALL_SLOTS    = build_wave_slots()
ACTIVE_SLOTS = [s for s in ALL_SLOTS if s["time"] >= "13:00"]

# ── 보고서 ────────────────────────────────────────────────────
report = {
    "total_slots":  len(ALL_SLOTS),
    "active_slots": len(ACTIVE_SLOTS),
    "waves":        [],
    "issues":       [],
    "slack_preview": [],
}

replenished_skus: set = set()


# ── 유틸 ─────────────────────────────────────────────────────
def upload_snapshot(time_label: str, force_shortage: int = 0):
    elapsed = time_to_elapsed(time_label)

    inv_path, inv_rows = gen_snapshot(
        time_label, elapsed, replenished_skus,
        force_shortage_count=force_shortage,
    )
    with open(inv_path, "rb") as f:
        r = client.post(
            "/api/v1/upload/inventory",
            files={"file": (f"inventory_{time_label}.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )
    inv_ok = r.status_code == 200
    inv_d  = r.json() if inv_ok else {}

    out_path = gen_outbound(time_label, elapsed)
    with open(out_path, "rb") as f:
        r = client.post(
            "/api/v1/upload/outbound",
            files={"file": (f"outbound_{time_label}.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )
    out_ok = r.status_code == 200

    if not (inv_ok and out_ok):
        report["issues"].append(f"[{time_label}] CSV 업로드 실패 inv={inv_ok} out={out_ok}")

    return inv_d


def run_wave_cycle(slot: dict, max_cand: int = None, force_shortage: int = 0) -> dict | None:
    """단일 웨이브 사이클: 업로드 → 생성 → 승인 → 확정 → Slack"""
    t_label   = slot["time"]
    wave_type = slot["type"]

    # 1. CSV 업로드 (force_shortage: 14:00 미할당 폭발 시나리오 등)
    inv_d = upload_snapshot(t_label, force_shortage=force_shortage)
    picking_cnt   = inv_d.get("picking_count", 0)
    unknown_zones = inv_d.get("unknown_zones", [])
    if unknown_zones:
        report["issues"].append(f"[{t_label}] 미등록 존: {unknown_zones}")

    # 2. 웨이브 생성
    body: dict = {"wave_type": wave_type, "center_cd": "GGH1"}
    if max_cand:
        body["max_candidates"] = max_cand

    t0      = time.time()
    r       = client.post("/api/v1/waves", json=body)
    elapsed = time.time() - t0

    if r.status_code not in [200, 201]:
        report["issues"].append(f"[{t_label}] 웨이브 생성 실패: {r.status_code} {r.text[:200]}")
        return None

    wave    = r.json()
    wave_id = wave.get("wave_id")
    algo    = wave.get("algorithm", {})
    total_c = algo.get("total_candidates", 0)
    crit    = algo.get("critical", 0)

    # 3. 후보 조회 + 전체 승인
    r = client.get(f"/api/v1/waves/{wave_id}/candidates")
    candidates = r.json() if r.status_code == 200 else []
    batched    = [c for c in candidates if c.get("batch_tag")]

    approved = 0
    for c in candidates:
        ra = client.post(
            f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve"
        )
        if ra.status_code == 200:
            approved += 1

    # 4. 웨이브 확정
    rc        = client.post(f"/api/v1/waves/{wave_id}/confirm")
    confirmed = rc.status_code == 200

    # 5. DB 검증: Location 완전성
    missing_loc = 0
    with Session(engine) as s:
        tasks = s.exec(
            select(ReplenishConfirmedTask)
            .where(ReplenishConfirmedTask.wave_id == wave_id)
        ).all()
        if tasks:
            task_ids = {t.task_id for t in tasks}
            locs     = s.exec(
                select(ReplenishTaskLocation)
                .where(ReplenishTaskLocation.task_id.in_(list(task_ids)))
            ).all()
            loc_task_ids = {l.task_id for l in locs}
            missing_loc  = len([t for t in tasks if t.task_id not in loc_task_ids])
            if missing_loc:
                report["issues"].append(
                    f"[{t_label}] Location 없는 태스크: {missing_loc}건"
                )

    # 6. Slack 메시지 생성 (v2) + 검증
    slack_ok     = True
    first_preview = ""
    with Session(engine) as s:
        try:
            ch_msgs = build_wave_messages_v2(wave_id, s)
            for ch_key, msgs in ch_msgs.items():
                full = "\n".join(msgs)
                # v2 형식: 지번(15로 시작), 메시지 footer에 <!here>
                if "<!here>" not in full:
                    report["issues"].append(
                        f"[{t_label}] Slack <!here> 없음 [{ch_key}]"
                    )
                    slack_ok = False
                has_bin = any(
                    line.strip() and not line.startswith("[") and not line.startswith("*")
                    and "15" in line
                    for line in full.split("\n")
                )
                if not has_bin and total_c > 0:
                    report["issues"].append(
                        f"[{t_label}] Slack 지번 코드 없음 [{ch_key}]"
                    )
                    slack_ok = False
            if ch_msgs:
                first_key     = next(iter(ch_msgs))
                first_preview = ch_msgs[first_key][0][:200] + "..."
        except Exception as e:
            report["issues"].append(f"[{t_label}] Slack 오류: {e}")
            slack_ok = False

    # 7. Slack 전송 (mock — bot_token 미설정 → queue 저장)
    client.post(f"/api/v1/waves/{wave_id}/send")

    # 8. 결과 기록
    result = {
        "time":             t_label,
        "interval":         slot["interval"],
        "wave_type":        wave_type,
        "wave_id":          wave_id,
        "elapsed_s":        round(elapsed, 2),
        "picking_cnt":      picking_cnt,
        "candidates":       total_c,
        "critical":         crit,
        "approved":         approved,
        "confirmed":        confirmed,
        "batched":          len(batched),
        "slack_ok":         slack_ok,
        "missing_loc":      missing_loc,
        "prestock_cutoff":  wave.get("prestock_cutoff"),
    }
    report["waves"].append(result)
    if first_preview:
        report["slack_preview"].append({"time": t_label, "preview": first_preview})

    return result


def verify_1400_resolution(wave_id_1400: int, wave_id_1430: int | None) -> bool:
    """
    14:00 미할당 104개가 16:00 마감 전에 처리되는지 검증.

    작업자 4명(설정), UPH 12(기본) ~ 15(배치 묶음 최대).
    가용 시간: 14:00~16:00 = 2시간.

    검증 항목:
      1. 14:00 CRITICAL 추천이 104의 80% 이상
      2. 배치 묶음 비율로 유효 UPH 산출 → 2시간 처리 용량
      3. 용량이 104 이상
    """
    with Session(engine) as s:
        tasks_1400 = s.exec(
            select(ReplenishConfirmedTask)
            .where(ReplenishConfirmedTask.wave_id == wave_id_1400)
        ).all()
        tasks_1430 = []
        if wave_id_1430:
            tasks_1430 = s.exec(
                select(ReplenishConfirmedTask)
                .where(ReplenishConfirmedTask.wave_id == wave_id_1430)
            ).all()

        # batch_tag는 candidate 측에 저장 — candidate_id로 lookup
        from app.models.task import ReplenishCandidate
        cand_ids = [t.candidate_id for t in tasks_1400 if t.candidate_id]
        batched_cands = 0
        if cand_ids:
            batched_cands = len(s.exec(
                select(ReplenishCandidate)
                .where(ReplenishCandidate.candidate_id.in_(cand_ids))
                .where(ReplenishCandidate.batch_tag.is_not(None))
            ).all())

        # 활성 작업자 수 (실제 DB 기준)
        n_workers = len(s.exec(
            select(Worker).where(Worker.is_active == True)  # noqa: E712
        ).all())

    total_1400    = len(tasks_1400)
    batch_ratio   = batched_cands / total_1400 if total_1400 else 0
    effective_uph = 12 + (15 - 12) * batch_ratio   # 12 ~ 15
    capacity_2h   = n_workers * effective_uph * 2

    print(f"\n  📊 14:00 미할당 폭발 검증")
    print(f"  강제 주입: 104개")
    print(f"  14:00 CRITICAL 추천: {total_1400}개")
    print(f"  14:30 추가 추천:     {len(tasks_1430)}개")
    print(f"  배치 태그 비율:      {batch_ratio:.0%}")
    print(f"  유효 UPH:            {effective_uph:.1f} (12~15)")
    print(f"  2시간 처리 용량:     {capacity_2h:.0f}개 (작업자 {n_workers}명)")
    print(f"  104개 처리 가능:     {'✅' if capacity_2h >= 104 else '❌'}")
    print(f"  CRITICAL 추천 80개+: {'✅' if total_1400 >= 80 else '❌ (' + str(total_1400) + ')'}")

    ok = capacity_2h >= 104 and total_1400 >= 80
    if not ok:
        report["issues"].append(
            f"14:00 미할당 104개 — 처리 용량 {capacity_2h:.0f}, 추천 {total_1400}"
        )
    return ok


def simulate_completions(wave_id: int, done_ratio: float = 0.7, block_ratio: float = 0.1):
    """작업자 태스크 완료 시뮬레이션 + 보충 완료 SKU 추적"""
    import random as rnd
    with Session(engine) as s:
        tasks = s.exec(
            select(ReplenishConfirmedTask)
            .where(ReplenishConfirmedTask.wave_id == wave_id)
            .where(ReplenishConfirmedTask.task_status == "READY")
        ).all()

        n_done    = int(len(tasks) * done_ratio)
        n_blocked = int(len(tasks) * block_ratio)

        for t in tasks[:n_done]:
            t.task_status = "DONE"
            replenished_skus.add(t.sku_id)

        for t in tasks[n_done: n_done + n_blocked]:
            t.task_status = "BLOCKED"
            t.block_reason = "통로 혼잡 / 재고 접근 불가"

        s.commit()
        return n_done, n_blocked


# ══════════════════════════════════════════════════════════════
# 하루 운영 실행
# ══════════════════════════════════════════════════════════════

print("=" * 60)
print("  📅 원 데이 운영 시뮬레이션 시작")
print(f"  전체 슬롯: {len(ALL_SLOTS)}개  |  실행: {len(ACTIVE_SLOTS)}개 (13:00~)")
print("=" * 60)

# 피벗 CSV 1회 업로드
pivot_path = gen_pivot()
with open(pivot_path, "rb") as f:
    r = client.post(
        "/api/v1/upload/pivot-sales",
        files={"file": ("pivot.csv", f, "text/csv")},
        data={"center_cd": "GGH1"},
    )
print(f"\n피벗 CSV 업로드: {'✅' if r.status_code == 200 else '❌ ' + str(r.status_code)}")

# 작업자 출근 처리
with Session(engine) as s:
    workers = s.exec(select(Worker)).all()
    for w in workers:
        w.is_active = True
    s.commit()
print(f"작업자 {len(workers)}명 출근 처리")

# 이전 웨이브 ID 추적
prev_wave_id = None
# 14:00 미할당 폭발 시나리오 — 14:00, 14:30 wave_id 별도 기록
wave_id_1400 = None
wave_id_1430 = None

print(f"\n{'─'*60}")
print(f"  {'시각':8s} {'타입':10s} {'후보':>5s} {'CRIT':>5s} {'배치':>5s} {'소요':>6s}")
print(f"{'─'*60}")

for i, slot in enumerate(ACTIVE_SLOTS):
    t_label    = slot["time"]
    is_prestock = slot["type"] == "PRESTOCK"

    # 이전 웨이브 완료 시뮬레이션
    if prev_wave_id and not is_prestock:
        simulate_completions(prev_wave_id, done_ratio=0.65, block_ratio=0.08)

    # 14:00 슬롯: 미할당 104개 강제 주입
    shortage = 104 if t_label == "14:00" else 0
    result   = run_wave_cycle(slot, force_shortage=shortage)
    if result:
        prev_wave_id = result["wave_id"]
        if t_label == "14:00":
            wave_id_1400 = result["wave_id"]
        elif t_label == "14:30":
            wave_id_1430 = result["wave_id"]
        status = "✅" if result["confirmed"] and result["slack_ok"] else "❌"
        print(
            f"  {status} {t_label:8s} {slot['type']:10s} "
            f"{result['candidates']:5d} "
            f"{result['critical']:5d} "
            f"{result['batched']:5d} "
            f"{result['elapsed_s']:5.1f}s"
        )
    else:
        print(f"  ❌ {t_label:8s} {slot['type']:10s} 실패")

    if t_label == "20:30":
        print(f"{'─'*60}")
        print("  ⏰ 20:30 이후 → 15분 간격으로 전환")
        print(f"{'─'*60}")
    if t_label == "23:00":
        print(f"{'─'*60}")
        print("  🌙 주문 마감 — 선보충 준비")
        print(f"{'─'*60}")

# 마지막 웨이브 완료
if prev_wave_id:
    simulate_completions(prev_wave_id, done_ratio=0.5, block_ratio=0.05)

# 14:00 미할당 폭발 검증 (daily-reset 전에 작업자 활성 상태에서 수행)
if wave_id_1400:
    verify_1400_resolution(wave_id_1400, wave_id_1430)

# ── 01:00 마감 ───────────────────────────────────────────────
print(f"\n{'═'*60}")
print("  🌙 01:00 일 마감")
print(f"{'═'*60}")

r = client.get("/api/v1/admin/db-export")
export_ok = r.status_code == 200 and len(r.content) > 0
if export_ok:
    Path("tests/oneday/data/oneday_export.db").write_bytes(r.content)
print(f"  {'✅' if export_ok else '❌'} DB 내보내기 ({len(r.content):,} bytes)")

r        = client.post("/api/v1/workers/daily-reset")
reset_cnt = r.json().get("reset_count", 0) if r.status_code == 200 else 0
print(f"  {'✅' if r.status_code == 200 else '❌'} 작업자 초기화: {reset_cnt}명")


# ══════════════════════════════════════════════════════════════
# 최종 보고서
# ══════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
print("  📋 원 데이 운영 시뮬레이션 결과")
print(f"{'═'*60}")

waves       = report["waves"]
total_w     = len(waves)
ok_waves    = sum(1 for w in waves if w["confirmed"] and w["slack_ok"])
fail_w      = total_w - ok_waves
issues      = report["issues"]

total_cands   = sum(w["candidates"] for w in waves)
total_batched = sum(w["batched"] for w in waves)
avg_elapsed   = sum(w["elapsed_s"] for w in waves) / total_w if waves else 0
max_elapsed   = max((w["elapsed_s"] for w in waves), default=0)

print(f"\n  웨이브 실행 현황")
print(f"  전체 슬롯:  {len(ALL_SLOTS)}개 (09:30~23:20)")
print(f"  실행 슬롯:  {total_w}개 (13:00~)")
print(f"  성공:       {ok_waves}개")
print(f"  실패:       {fail_w}개")

print(f"\n  알고리즘 성능")
print(f"  총 추천 후보: {total_cands}건")
print(f"  배치 태그:    {total_batched}건 (혼적 묶음)")
print(f"  평균 소요:    {avg_elapsed:.2f}s / 웨이브")
print(f"  최대 소요:    {max_elapsed:.2f}s / 웨이브")

slots_30 = [w for w in waves if w["interval"] == 30]
slots_15 = [w for w in waves if w["interval"] == 15]
if slots_30:
    avg_30 = sum(w["elapsed_s"] for w in slots_30) / len(slots_30)
    print(f"\n  구간별 응답 시간")
    print(f"  30분 구간 평균: {avg_30:.2f}s ({len(slots_30)}개 슬롯)")
if slots_15:
    avg_15 = sum(w["elapsed_s"] for w in slots_15) / len(slots_15)
    print(f"  15분 구간 평균: {avg_15:.2f}s ({len(slots_15)}개 슬롯)")

prestock = [w for w in waves if w["wave_type"] == "PRESTOCK"]
if prestock:
    ps     = prestock[0]
    cutoff = ps.get("prestock_cutoff") or {}
    print(f"\n  선보충 (23:20)")
    if cutoff:
        print(f"  컷오프: {cutoff['active_workers']}명 × {cutoff['uph']} UPH × "
              f"{cutoff['minutes']}분 = {cutoff['max_sku']}개")
    print(f"  알고리즘 추천 전체: {ps['candidates']}개  →  승인(컷오프 후): {ps['approved']}개")

if report["slack_preview"]:
    preview = report["slack_preview"][-1]
    print(f"\n  📱 마지막 Slack 미리보기 [{preview['time']}]:")
    for line in preview["preview"].split("\n")[:6]:
        print(f"    {line}")

if issues:
    print(f"\n  ❌ 발견된 문제 ({len(issues)}건):")
    for iss in issues:
        print(f"    • {iss}")
else:
    print(f"\n  ✅ 발견된 문제 없음")

Path("tests/oneday/oneday_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(f"\n  보고서: tests/oneday/oneday_report.json")
print(f"\n{'═'*60}")
print(
    "  🎉 원 데이 운영 시뮬레이션 완료" if not issues else
    f"  ⚠️  {len(issues)}건 이슈 발견 — 수정 후 재실행"
)
print(f"{'═'*60}")
