"""
전체 운영 흐름 시뮬레이션 + 결과 검증
업로드 → 알고리즘 → 웨이브 → 검수 → 확정 → Slack 검증
"""
import json
import time
from datetime import date
from pathlib import Path

import app.models  # noqa: F401 — register all ORM models before engine use
from app.main import app
from app.core.database import engine, init_db
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.sku import SkuSalesSummary
from app.services.slack_service import build_wave_messages_v2
from fastapi.testclient import TestClient
from sqlmodel import Session, select

SIM_DIR = Path("tests/sim/data")
client  = TestClient(app)

report = {
    "timestamp": date.today().isoformat(),
    "steps": [],
    "issues": [],
    "summary": {},
}

def log(step, result, detail="", ok=True):
    icon = "✅" if ok else "❌"
    print(f"{icon} [{step}] {result}")
    if detail:
        print(f"   {detail}")
    report["steps"].append({"step": step, "ok": ok, "result": result, "detail": detail})
    if not ok:
        report["issues"].append(f"[{step}] {result}")

def check(cond, step, ok_msg, fail_msg, detail=""):
    if cond:
        log(step, ok_msg, detail, ok=True)
    else:
        log(step, fail_msg, detail, ok=False)
    return cond


# ─────────────────────────────────────────────
# STEP 1. CSV 업로드
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 1. CSV 업로드")
print("="*50)

t0 = time.time()
with open(SIM_DIR / "inventory.csv", "rb") as f:
    res = client.post("/api/v1/upload/inventory",
                      files={"file": ("inventory.csv", f, "text/csv")},
                      data={"center_cd": "GGH1"})
upload_inv = res.json()
t1 = time.time()

check(res.status_code == 200, "업로드/재고",
      f"재고 CSV 업로드 성공 ({upload_inv.get('record_count',0):,}행, {t1-t0:.1f}s)",
      f"업로드 실패: {res.text}")
check(upload_inv.get("picking_count", 0) > 0, "업로드/피킹존",
      f"피킹존 {upload_inv.get('picking_count',0)}행 분류",
      "피킹존 분류 실패")
check(upload_inv.get("replenish_count", 0) > 0, "업로드/보충존",
      f"보충존 {upload_inv.get('replenish_count',0)}행 분류",
      "보충존 분류 실패")

unknown = upload_inv.get("unknown_zones", [])
if unknown:
    log("업로드/미등록존", f"미등록 존 감지: {unknown}", ok=False)

multi = upload_inv.get("multi_bin_skus", 0)
log("업로드/다중피킹", f"다중 피킹지번 SKU: {multi}개 (시나리오 3개 예상)",
    ok=(multi >= 3))

# 출고 CSV 업로드
t0 = time.time()
with open(SIM_DIR / "outbound.csv", "rb") as f:
    res = client.post("/api/v1/upload/outbound",
                      files={"file": ("outbound.csv", f, "text/csv")},
                      data={"center_cd": "GGH1"})
t1 = time.time()
check(res.status_code == 200, "업로드/출고",
      f"출고 CSV 업로드 성공 ({res.json().get('record_count',0):,}행, {t1-t0:.1f}s)",
      f"실패: {res.text}")

# 피벗 CSV 업로드
t0 = time.time()
with open(SIM_DIR / "pivot.csv", "rb") as f:
    res = client.post("/api/v1/upload/pivot-sales",
                      files={"file": ("pivot.csv", f, "text/csv")},
                      data={"center_cd": "GGH1"})
t1 = time.time()
check(res.status_code == 200, "업로드/피벗",
      f"피벗 CSV 업로드 성공 ({res.json().get('record_count',0):,}행, {t1-t0:.1f}s)",
      f"실패: {res.text}")


# ─────────────────────────────────────────────
# STEP 2. 알고리즘 실행 (웨이브 생성)
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 2. 웨이브 생성 (알고리즘 실행)")
print("="*50)

t0 = time.time()
res = client.post("/api/v1/waves", json={
    "wave_type": "REGULAR",
    "center_cd": "GGH1",
    "max_candidates": 40,
})
t1 = time.time()

check(res.status_code in [200, 201], "웨이브/생성",
      f"웨이브 생성 성공 ({t1-t0:.1f}s)",
      f"생성 실패: {res.text}")

wave = res.json()
wave_id = wave.get("wave_id")
algo = wave.get("algorithm", {})

print(f"\n  알고리즘 결과:")
print(f"  총 후보:   {algo.get('total_candidates', 0)}개")
print(f"  CRITICAL:  {algo.get('critical', 0)}개")
print(f"  HIGH:      {algo.get('high', 0)}개")
print(f"  MEDIUM:    {algo.get('medium', 0)}개")
print(f"  LOW:       {algo.get('low', 0)}개")
print(f"  보충불가:  {len(algo.get('no_replen_skus', []))}개")
print(f"  실행시간:  {algo.get('execution_ms', 0)}ms")

check(algo.get("total_candidates", 0) > 0, "알고리즘/후보수",
      f"추천 후보 {algo.get('total_candidates',0)}개 생성",
      "추천 후보 0개 — 알고리즘 오류")
check(algo.get("critical", 0) > 0, "알고리즘/CRITICAL",
      f"CRITICAL {algo.get('critical',0)}개 (시나리오: 10개 예상)",
      "CRITICAL 0개 — 위험도 계산 이상",
      detail="재고 고갈 SKU 10개 포함했는데 CRITICAL 없으면 알고리즘 재검토 필요")
check(algo.get("execution_ms", 9999) < 10000, "알고리즘/성능",
      f"실행 시간 {algo.get('execution_ms',0)}ms (기준 10초)",
      f"성능 초과: {algo.get('execution_ms',0)}ms")


# ─────────────────────────────────────────────
# STEP 3. 알고리즘 결과 품질 검사
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 3. 알고리즘 결과 품질 검사")
print("="*50)

res = client.get(f"/api/v1/waves/{wave_id}/candidates")
candidates = res.json()

# 3-1. 위험도 내림차순 정렬 확인
scores = [c["risk_score"] for c in candidates]
is_sorted = all(scores[i] >= scores[i+1] for i in range(len(scores)-1))
check(is_sorted, "품질/정렬", "위험도 내림차순 정렬 확인", "정렬 오류 발견")

# 3-2. matched_bins 구조 확인
bins_ok = all(isinstance(c.get("matched_bins", []), list) for c in candidates)
bins_nonempty = sum(1 for c in candidates if c.get("matched_bins"))
check(bins_ok, "품질/matched_bins 구조", "모든 후보에 matched_bins 배열 존재",
      "matched_bins 타입 오류")
check(bins_nonempty > 0, "품질/matched_bins 데이터",
      f"{bins_nonempty}/{len(candidates)}개 후보에 보충지번 있음",
      "보충지번 없는 후보만 존재")

# 3-3. FEFO 순서 검증
fefo_violations = 0
for c in candidates:
    bins = c.get("matched_bins", [])
    for i in range(len(bins) - 1):
        d1 = bins[i].get("deadline_days") or 9999
        d2 = bins[i+1].get("deadline_days") or 9999
        if d1 > d2:
            fefo_violations += 1
check(fefo_violations == 0, "품질/FEFO",
      f"FEFO 위반 0건 (전체 {len(candidates)}개 후보 검사)",
      f"FEFO 위반 {fefo_violations}건 발견 — 정렬 로직 재검토")

# 3-4. today_sales 확인
sales_nonzero = sum(1 for c in candidates if c.get("today_sales", 0) > 0)
log("품질/today_sales",
    f"오늘 판매량 > 0인 후보: {sales_nonzero}/{len(candidates)}개",
    ok=(sales_nonzero > 0))

# 3-5. proximity_score 범위 확인
invalid_score = []
for c in candidates:
    for b in c.get("matched_bins", []):
        s = b.get("proximity_score")
        if s is not None and s not in [1, 2, 3, 4]:
            invalid_score.append(s)
check(len(invalid_score) == 0, "품질/proximity_score 범위",
      "모든 proximity_score 1~4 범위",
      f"범위 위반 {len(invalid_score)}건: {invalid_score[:5]}")

# 3-6. MAIN / SUB 분류 확인
main_cnt = sum(1 for c in candidates if c.get("list_section") == "MAIN")
sub_cnt  = sum(1 for c in candidates if c.get("list_section") == "SUB")
log("품질/섹션분류", f"MAIN {main_cnt}개 / SUB {sub_cnt}개",
    ok=(main_cnt > 0))

# 3-7. 상위 5개 CRITICAL SKU 출력
print("\n  🔴 CRITICAL SKU 상위 5개:")
critical = [c for c in candidates if c["risk_level"] == "CRITICAL"][:5]
for i, c in enumerate(critical, 1):
    bins = c.get("matched_bins", [])
    bin_str = " → ".join(
        f"{b['replenish_bin']}({b['allocated_qty']}개)" for b in bins[:2]
    )
    print(f"  {i}. {c['sku_name']}")
    print(f"     위험도 {c['risk_score']}점 | ETA {c.get('eta_hours', '?')}h")
    print(f"     보충: {bin_str or '없음'}")


# ─────────────────────────────────────────────
# STEP 4. 검수 시뮬레이션
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 4. 검수 시뮬레이션 (승인/거절/섹션 이동)")
print("="*50)

if not candidates:
    log("검수", "후보 없음 — 검수 생략", ok=False)
else:
    total   = len(candidates)
    approve = candidates[:int(total * 0.8)]   # 80% 승인
    reject  = candidates[int(total * 0.8):]   # 20% 거절

    approved_count = 0
    for c in approve:
        r = client.post(f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve")
        if r.status_code == 200:
            approved_count += 1

    rejected_count = 0
    for c in reject[:3]:   # 최대 3개만 거절 테스트
        r = client.post(
            f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/reject",
            params={"reason": "시뮬레이션 거절 테스트"}
        )
        if r.status_code == 200:
            rejected_count += 1

    check(approved_count > 0, "검수/승인",
          f"{approved_count}개 승인 완료",
          "승인 실패")
    log("검수/거절", f"{rejected_count}개 거절 (사유 포함)")

    # 섹션 이동 테스트
    if approve:
        c = approve[0]
        current = c["list_section"]
        target  = "SUB" if current == "MAIN" else "MAIN"
        r = client.patch(
            f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}",
            json={"list_section": target}
        )
        check(r.status_code == 200, "검수/섹션이동",
              f"{current} → {target} 이동 성공",
              f"섹션 이동 실패: {r.text}")


# ─────────────────────────────────────────────
# STEP 5. 웨이브 확정 + ReplenishTaskLocation 검증
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 5. 웨이브 확정")
print("="*50)

t0 = time.time()
res = client.post(f"/api/v1/waves/{wave_id}/confirm")
t1 = time.time()

check(res.status_code == 200, "확정/API",
      f"웨이브 확정 성공 ({t1-t0:.1f}s)",
      f"확정 실패: {res.text}")

confirm_data = res.json()
tasks_created = confirm_data.get("tasks_created", 0)
log("확정/태스크", f"태스크 {tasks_created}개 생성")

# DB 직접 검증
with Session(engine) as s:
    tasks = s.exec(
        select(ReplenishConfirmedTask)
        .where(ReplenishConfirmedTask.wave_id == wave_id)
    ).all()
    locs = s.exec(
        select(ReplenishTaskLocation)
        .where(ReplenishTaskLocation.task_id.in_([t.task_id for t in tasks]))
    ).all()

    check(len(tasks) > 0, "확정/DB 태스크", f"DB ReplenishConfirmedTask {len(tasks)}건",
          "DB에 태스크 없음")
    check(len(locs) >= len(tasks), "확정/DB 지번",
          f"DB ReplenishTaskLocation {len(locs)}건 (태스크당 ≥1개)",
          f"Location 부족: tasks={len(tasks)}, locations={len(locs)}")

    # 모든 태스크에 최소 1개 Location 확인
    task_ids_with_loc = {l.task_id for l in locs}
    tasks_without_loc = [t for t in tasks if t.task_id not in task_ids_with_loc]
    check(len(tasks_without_loc) == 0, "확정/Location 완전성",
          "모든 태스크에 Location ≥ 1개",
          f"Location 없는 태스크 {len(tasks_without_loc)}개 발견")


# ─────────────────────────────────────────────
# STEP 6. Slack 메시지 형식 검증
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 6. Slack 메시지 형식 검증")
print("="*50)

with Session(engine) as s:
    channel_blocks = build_wave_messages_v2(wave_id, s)

check(len(channel_blocks) > 0, "Slack/채널분류",
      f"{len(channel_blocks)}개 채널로 분류",
      "채널 분류 실패")

for channel, blocks in channel_blocks.items():
    texts = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "section" and "text" in b
    ]
    has_bin     = any("`15" in t for t in texts)
    has_icon    = any(any(icon in t for icon in ["🟢","🟠","🟡","⚪"]) for t in texts)
    has_qty     = any("개" in t for t in texts)
    has_warning = any("보충지번 정보 없음" in t for t in texts)

    print(f"\n  채널: {channel}")
    print(f"  블록 수: {len(blocks)}개")
    check(has_bin, f"Slack/{channel}/지번",  "보충지번 포함", "보충지번 없음")
    check(has_qty, f"Slack/{channel}/수량",  "수량 포함",     "수량 없음")
    check(has_icon, f"Slack/{channel}/아이콘", "proximity 아이콘 포함",
          "proximity 아이콘 없음 (좌표 미설정이면 ⚪ 예상)")
    if has_warning:
        log(f"Slack/{channel}/경고", "보충지번 없는 태스크 발견", ok=False)

    # 메시지 미리보기
    if texts:
        print(f"\n  📱 미리보기 (첫 번째 항목):")
        print("  " + texts[0].replace("\n", "\n  "))


# ─────────────────────────────────────────────
# STEP 7. 대시보드 데이터 확인
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 7. 대시보드 확인")
print("="*50)

res = client.get("/api/v1/dashboard")
check(res.status_code == 200, "대시보드/API", "대시보드 API 정상", f"실패: {res.text}")

dash = res.json()
rc   = dash.get("risk_counts", {})
print(f"\n  위험도 분포: {rc}")
print(f"  CRITICAL SKU: {len(dash.get('critical_skus', []))}개")
print(f"  작업자: {dash.get('active_workers')}/{dash.get('total_workers')}명")
print(f"  미등록 존: {dash.get('unknown_zones', [])}")
print(f"  다중 피킹지번: {dash.get('multi_bin_skus')}개")


# ─────────────────────────────────────────────
# STEP 8. 결과 보고서 생성
# ─────────────────────────────────────────────
print("\n" + "="*50)
print("STEP 8. 결과 보고서")
print("="*50)

total_steps  = len(report["steps"])
passed_steps = sum(1 for s in report["steps"] if s["ok"])
issues       = report["issues"]

report["summary"] = {
    "total_steps":  total_steps,
    "passed":       passed_steps,
    "failed":       total_steps - passed_steps,
    "issues":       issues,
    "candidates":   algo.get("total_candidates", 0),
    "critical":     algo.get("critical", 0),
    "tasks_created": tasks_created,
    "channels":     list(channel_blocks.keys()),
}

print(f"\n  총 검사 항목: {total_steps}개")
print(f"  통과: {passed_steps}개")
print(f"  실패: {total_steps - passed_steps}개")

if issues:
    print(f"\n  ❌ 발견된 문제:")
    for issue in issues:
        print(f"     • {issue}")
else:
    print(f"\n  ✅ 발견된 문제 없음")

# JSON 보고서 저장
report_path = Path("tests/sim/simulation_report.json")
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f"\n  보고서 저장: {report_path}")

# 최종 판정
if not issues:
    print("\n🎉 시뮬레이션 전체 통과 — 운영 투입 준비 완료")
else:
    print(f"\n⚠️  {len(issues)}개 문제 발견 — 수정 후 재실행 권장")
