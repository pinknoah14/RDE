"""
원데이 운영 시뮬레이션 고도화 — 6개 Phase 종합 검증

Phase 1: 실규모 성능 측정 (25,000 SKU / ~20MB CSV)
Phase 2: 할당건(CRITICAL) 자동 감지 검증
Phase 3: 업무 타임라인 시뮬레이션 (13:00~23:20)
Phase 4: 예외 상황 검증 (BLOCKED / 중복 웨이브 / CSV 오류 / Slack 채널 없음)
Phase 5: 완료 이중 검증 (DONE 표시 vs 재고 재업로드)
Phase 6: GAP 리포트 출력

실행: cd replenish-system/backend && python tests/oneday/run_operational_sim.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import app.models  # noqa: F401
from app.main import app
from app.core.database import engine, init_db

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.task import ReplenishConfirmedTask, ReplenishCandidate
from app.models.worker import Worker
from app.services.slack_service import build_wave_messages_v2

from tests.oneday.generate_oneday_data import (
    gen_snapshot, gen_outbound, gen_pivot, time_to_elapsed, OUTDIR,
)

client = TestClient(app, raise_server_exceptions=False)

report: dict = {
    "phases": {},
    "gap_report": [],
    "summary": {},
}
gaps: list[dict] = report["gap_report"]
replenished_skus: set[str] = set()


# ── 내부 유틸 ──────────────────────────────────────────────────────────

def _gap(gap_id: str, severity: str, title: str, detail: str) -> None:
    # 동일 gap_id 중복 방지
    if any(g["gap_id"] == gap_id for g in gaps):
        return
    gaps.append({"gap_id": gap_id, "severity": severity, "title": title, "detail": detail})


def _setup() -> int:
    init_db()
    with Session(engine) as s:
        workers = s.exec(select(Worker)).all()
        for w in workers:
            w.is_active = True
        s.commit()
    return len(workers)


def _upload(time_label: str, elapsed: float, force_shortage: int = 0,
            skip_outbound: bool = False) -> dict:
    inv_path, inv_rows = gen_snapshot(
        time_label, elapsed, replenished_skus, force_shortage_count=force_shortage
    )
    with open(inv_path, "rb") as f:
        r = client.post(
            "/api/v1/upload/inventory",
            files={"file": (f"inv_{time_label}.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )
    inv_ok = r.status_code == 200
    inv_d  = r.json() if inv_ok else {}

    out_ok = True
    if not skip_outbound:
        out_path = gen_outbound(time_label, elapsed)
        with open(out_path, "rb") as f:
            r2 = client.post(
                "/api/v1/upload/outbound",
                files={"file": (f"out_{time_label}.csv", f, "text/csv")},
                data={"center_cd": "GGH1"},
            )
        out_ok = r2.status_code == 200
    return {"inv_ok": inv_ok, "out_ok": out_ok, "rows": inv_rows, "inv_d": inv_d}


def _wave(wave_type: str = "REGULAR", max_candidates: int | None = None) -> dict | None:
    body: dict = {"wave_type": wave_type, "center_cd": "GGH1"}
    if max_candidates:
        body["max_candidates"] = max_candidates
    t0 = time.time()
    r = client.post("/api/v1/waves", json=body)
    wave_ms = (time.time() - t0) * 1000
    if r.status_code not in [200, 201]:
        return None
    d = r.json()
    d["_ms"] = round(wave_ms)
    return d


def _approve_confirm(wave_id: int, max_approve: int = 30) -> tuple[int, bool]:
    r = client.get(f"/api/v1/waves/{wave_id}/candidates")
    cands = r.json() if r.status_code == 200 else []
    # 시뮬레이션 성능을 위해 상위 N개만 승인 (실운영에서는 전체 또는 선택 승인)
    approved = sum(
        1 for c in cands[:max_approve]
        if client.post(f"/api/v1/waves/{wave_id}/candidates/{c['candidate_id']}/approve").status_code == 200
    )
    rc = client.post(f"/api/v1/waves/{wave_id}/confirm")
    return approved, rc.status_code == 200


def _complete(wave_id: int, done_ratio: float = 0.65, block_ratio: float = 0.08) -> tuple[int, int]:
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
            t.block_reason = "보충지번 재고 없음"
        s.commit()
    return n_done, n_blocked


def _get_tasks(wave_id: int) -> list[ReplenishConfirmedTask]:
    with Session(engine) as s:
        return s.exec(
            select(ReplenishConfirmedTask)
            .where(ReplenishConfirmedTask.wave_id == wave_id)
        ).all()


# ══════════════════════════════════════════════════════════════════════
# Phase 1: 실규모 성능 측정 (25,000 SKU / ~20MB CSV)
# ══════════════════════════════════════════════════════════════════════

def phase1_perf() -> dict:
    print("\n" + "=" * 60)
    print("  [Phase 1] 실규모 성능 측정 (25,000 SKU / ~20MB)")
    print("=" * 60)

    # 지연 임포트 — SKU_MASTER 25,000개 생성은 이 Phase에서만 발생
    from tests.oneday.generate_realscale_data import (  # noqa: PLC0415
        gen_snapshot as rs_snap,
        gen_outbound as rs_out,
        gen_pivot    as rs_piv,
        SKU_MASTER   as RS_MASTER,
    )

    r = {
        "n_sku": len(RS_MASTER),
        "file_size_mb": None,
        "row_count": None,
        "upload_ms": None,
        "wave_ms": None,
        "candidates": None,
        "status": "FAIL",
    }

    # 피벗 업로드는 스킵 — 25,000 SKU × 28일 sales upsert가 O(N*D)로 매우 느림
    # (실측: >2분. 이것 자체가 GAP-08 성능 이슈)
    # main()에서 300-SKU 피벗이 이미 업로드된 상태
    _gap("GAP-08", "HIGH", "피벗 CSV 업로드 O(N*D) 성능 문제",
         "25,000 SKU × 28일 피벗 CSV 업로드 시 update_all_sales_summaries() 가 "
         "SKU별 루프로 동작하여 2분 이상 소요. 배치 UPSERT로 개선 필요.")
    print("  피벗 업로드 스킵 (GAP-08: 25k SKU × 28일 sales upsert 성능 이슈)")

    # 재고 CSV 생성 + 업로드 타이밍
    t_gen = time.time()
    inv_path, row_count = rs_snap("13:00", 3.5)
    gen_ms = (time.time() - t_gen) * 1000
    file_mb = inv_path.stat().st_size / 1_048_576
    r["file_size_mb"] = round(file_mb, 1)
    r["row_count"]    = row_count
    print(f"  재고 CSV 생성: {row_count:,}행  {file_mb:.1f}MB  ({gen_ms:.0f}ms)")

    t0 = time.time()
    with open(inv_path, "rb") as f:
        ri = client.post(
            "/api/v1/upload/inventory",
            files={"file": ("rs_inv.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )
    upload_ms = (time.time() - t0) * 1000
    upload_ok = ri.status_code == 200
    r["upload_ms"] = round(upload_ms)
    tag = "OK" if upload_ms < 60_000 else "SLOW"
    print(f"  업로드: {upload_ms:.0f}ms [{tag}]  →  {'성공' if upload_ok else 'FAIL ' + str(ri.status_code)}")

    if not upload_ok:
        r["upload_error"] = ri.text[:300]
        return r

    out_path = rs_out("13:00", 3.5)
    with open(out_path, "rb") as f:
        client.post(
            "/api/v1/upload/outbound",
            files={"file": ("rs_out.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )

    # 웨이브 생성 타이밍
    t0 = time.time()
    rw = client.post("/api/v1/waves", json={"wave_type": "REGULAR", "center_cd": "GGH1"})
    wave_ms = (time.time() - t0) * 1000
    wave_ok = rw.status_code in [200, 201]
    r["wave_ms"] = round(wave_ms)
    tag = "OK" if wave_ms < 10_000 else "SLOW"
    print(f"  웨이브 생성: {wave_ms:.0f}ms [{tag}]  →  {'성공' if wave_ok else 'FAIL ' + str(rw.status_code)}")

    if wave_ok:
        algo = rw.json().get("algorithm", {})
        r["candidates"] = algo.get("total_candidates", 0)
        r["critical"]   = algo.get("critical", 0)
        print(f"  후보: {r['candidates']:,}개  CRITICAL: {r['critical']:,}개")

    if not (upload_ok and wave_ok):
        r["status"] = "FAIL"
    elif upload_ms < 60_000 and wave_ms < 10_000:
        r["status"] = "PASS"
    else:
        r["status"] = "WARN"

    print(f"  결과: {r['status']}")
    return r


# ══════════════════════════════════════════════════════════════════════
# Phase 2: 할당건(CRITICAL) 자동 감지 검증
# ══════════════════════════════════════════════════════════════════════

def phase2_critical() -> dict:
    print("\n" + "=" * 60)
    print("  [Phase 2] 할당건(CRITICAL) 자동 감지 검증")
    print("=" * 60)

    INJECTION = 104
    r = {"injection": INJECTION, "detected": 0, "ratio": 0.0, "status": "FAIL"}

    u = _upload("14:00", elapsed=4.5, force_shortage=INJECTION)
    if not u["inv_ok"]:
        print("  재고 업로드 실패")
        return r

    # /urgent-from-dashboard: CRITICAL만 필터링 + 자동확정
    t0 = time.time()
    rw = client.post(
        "/api/v1/waves/urgent-from-dashboard",
        json={"center_cd": "GGH1", "min_risk_level": "CRITICAL", "auto_confirm": True},
    )
    wave_ms = (time.time() - t0) * 1000
    r["wave_ms"] = round(wave_ms)

    if rw.status_code not in [200, 201]:
        print(f"  웨이브 생성 실패: {rw.status_code} {rw.text[:200]}")
        _gap("GAP-01", "HIGH", "미할당수량 컬럼 없음",
             "가용수량=0 → CRITICAL 탐지 자체는 동작하나, 업로드 실패 시 "
             "WMS 긴급보충리스트 미연동으로 할당건 명시적 식별 불가.")
        return r

    wave_d  = rw.json()
    wave_id = wave_d.get("wave_id")
    algo    = wave_d.get("algorithm", {})
    crit_cnt = algo.get("critical", 0)

    r["detected"]        = crit_cnt
    r["ratio"]           = round(crit_cnt / INJECTION, 3) if INJECTION else 0
    r["total_candidates"] = algo.get("total_candidates", 0)

    print(f"  주입: {INJECTION}개  →  CRITICAL 감지: {crit_cnt}개  ({r['ratio']:.0%})")
    print(f"  웨이브 생성: {wave_ms:.0f}ms")

    # URGENT 웨이브가 CRITICAL만 포함하는지 확인
    rc = client.get(f"/api/v1/waves/{wave_id}/candidates")
    if rc.status_code == 200:
        non_crit = [c for c in rc.json() if c.get("risk_level") not in ("CRITICAL",)]
        r["non_critical_in_urgent"] = len(non_crit)
        if non_crit:
            print(f"  비-CRITICAL {len(non_crit)}개 포함 (필터 미적용)")
        else:
            print("  URGENT 웨이브 CRITICAL-only: OK")

    _gap("GAP-01", "HIGH", "미할당수량 컬럼 없음",
         f"가용수량=0 → CRITICAL 감지율 {r['ratio']:.0%}. 단, '주문이 잡혔으나 피킹재고=0' 상태는 "
         "WMS 긴급보충리스트 컬럼(미할당수량)이 없어 명시적 식별 불가. "
         "가용=0인 일반 품절과 구분하려면 WMS 별도 CSV 연동 필요.")

    r["status"] = "PASS" if r["ratio"] >= 0.8 else "WARN"
    print(f"  결과: {r['status']} (목표 80%+)")
    return r


# ══════════════════════════════════════════════════════════════════════
# Phase 3: 업무 타임라인 시뮬레이션 (13:00~23:20)
# ══════════════════════════════════════════════════════════════════════

# 현장 주요 슬롯 (전체 30분 주기 대신 핵심 이벤트 중심)
TIMELINE: list[dict] = [
    {"time": "13:00", "type": "URGENT",   "note": "업무시작/할당건",  "shortage": 104},
    {"time": "14:00", "type": "REGULAR",  "note": "1차출근자투입"},
    {"time": "15:00", "type": "PRESTOCK", "note": "선보충 1차"},
    {"time": "15:30", "type": "REGULAR",  "note": ""},
    # 15:50 휴게 (GAP: skip 처리 없음)
    {"time": "16:00", "type": "URGENT",   "note": "1회차 주문마감",   "shortage": 30},
    {"time": "16:30", "type": "REGULAR",  "note": ""},
    {"time": "17:00", "type": "PRESTOCK", "note": "선보충 2차"},
    # 17:50 휴게 (GAP)
    {"time": "18:00", "type": "REGULAR",  "note": "2타임"},
    {"time": "18:30", "type": "REGULAR",  "note": ""},
    {"time": "19:00", "type": "REGULAR",  "note": ""},
    {"time": "19:30", "type": "REGULAR",  "note": ""},
    {"time": "20:00", "type": "REGULAR",  "note": ""},
    {"time": "20:30", "type": "URGENT",   "note": "2회차 주문마감",   "shortage": 20},
    # 20:40~20:50 휴게 (GAP)
    {"time": "21:00", "type": "REGULAR",  "note": "3타임"},
    {"time": "21:30", "type": "REGULAR",  "note": ""},
    {"time": "22:00", "type": "REGULAR",  "note": ""},
    {"time": "22:30", "type": "REGULAR",  "note": ""},
    {"time": "23:00", "type": "URGENT",   "note": "3회차 주문마감",   "shortage": 10},
    # 23:10 휴게 (GAP)
    {"time": "23:20", "type": "PRESTOCK", "note": "익일 선보충"},
]

ORDER_CUTOFFS = {"16:00", "20:30", "23:00"}


def phase3_timeline() -> dict:
    print("\n" + "=" * 60)
    print("  [Phase 3] 업무 타임라인 시뮬레이션 (13:00~23:20)")
    print("=" * 60)
    print(f"  {'시각':8s} {'타입':10s} {'후보':>5s} {'CRIT':>5s} {'ms':>6s}  비고")
    print(f"  {'─' * 55}")

    r: dict = {"waves": [], "issues": [], "cutoff_events": [], "status": "FAIL"}
    prev_wave_id: int | None = None

    for slot in TIMELINE:
        t_label = slot["time"]
        wtype   = slot["type"]
        note    = slot.get("note", "")
        shortage = slot.get("shortage", 0)
        elapsed  = time_to_elapsed(t_label)

        if t_label in ORDER_CUTOFFS:
            r["cutoff_events"].append(t_label)

        if prev_wave_id and wtype != "PRESTOCK":
            _complete(prev_wave_id, done_ratio=0.65, block_ratio=0.08)

        # 아웃바운드 업로드는 update_all_sales_summaries() O(N) 문제로 슬롯마다 실행 시 매우 느림.
        # 시뮬레이션에서는 첫 슬롯에만 업로드하고 이후 스킵 (판매 이력은 pivot으로 충분)
        is_first = len(r["waves"]) == 0
        u = _upload(t_label, elapsed, force_shortage=shortage, skip_outbound=not is_first)
        if not u["inv_ok"]:
            r["issues"].append(f"[{t_label}] 재고 업로드 실패")
            print(f"  ERR {t_label:8s} {wtype:10s} CSV 실패")
            continue

        # URGENT 슬롯은 할당건 전용 → urgent-from-dashboard 사용
        if wtype == "URGENT":
            t0  = time.time()
            rw  = client.post(
                "/api/v1/waves/urgent-from-dashboard",
                json={"center_cd": "GGH1", "min_risk_level": "CRITICAL", "auto_confirm": True},
            )
            ms = round((time.time() - t0) * 1000)
            if rw.status_code not in [200, 201]:
                r["issues"].append(f"[{t_label}] URGENT 웨이브 실패: {rw.status_code}")
                print(f"  ERR {t_label:8s} {wtype:10s} URGENT 실패")
                continue
            wd      = rw.json()
            wave_id = wd.get("wave_id")
            algo    = wd.get("algorithm", {})
            cands   = algo.get("total_candidates", 0)
            crit    = algo.get("critical", 0)
            confirmed = True  # auto_confirm=True
        else:
            wd = _wave(wtype)
            if not wd:
                r["issues"].append(f"[{t_label}] 웨이브 생성 실패")
                print(f"  ERR {t_label:8s} {wtype:10s} 웨이브 실패")
                continue
            wave_id   = wd.get("wave_id")
            ms        = wd["_ms"]
            algo      = wd.get("algorithm", {})
            cands     = algo.get("total_candidates", 0)
            crit      = algo.get("critical", 0)
            _, confirmed = _approve_confirm(wave_id)

        prev_wave_id = wave_id

        # Slack 채널 분리 확인
        channels: set[str] = set()
        with Session(engine) as s:
            try:
                ch_msgs = build_wave_messages_v2(wave_id, s)
                channels = set(ch_msgs.keys())
            except Exception as e:
                r["issues"].append(f"[{t_label}] Slack 오류: {e}")

        rec = {
            "time": t_label, "type": wtype, "wave_id": wave_id,
            "candidates": cands, "critical": crit,
            "confirmed": confirmed, "wave_ms": ms,
            "slack_channels": list(channels), "note": note,
        }
        r["waves"].append(rec)

        icon = "OK" if confirmed else "NG"
        print(
            f"  {icon} {t_label:8s} {wtype:10s} "
            f"{cands:5d} {crit:5d} {ms:6d}ms  {note[:25]}"
        )

    # 휴게시간 GAP 기록
    _gap("GAP-03", "MEDIUM", "휴게/마감 자동 처리 없음",
         "15:50·17:50·20:40·23:10 휴게 전 미작업 skip 로직 없음. "
         "주문 마감(16:00/20:30/23:00) 전후 우선순위 자동 전환 없음. "
         "슬롯 타이밍은 관리자가 직접 조율해야 함.")

    ok  = sum(1 for w in r["waves"] if w["confirmed"])
    tot = len(r["waves"])
    r["ok_waves"]    = ok
    r["total_waves"] = tot
    r["status"] = "PASS" if ok == tot and tot > 0 else "WARN" if ok > 0 else "FAIL"
    print(f"\n  결과: {r['status']} ({ok}/{tot} 웨이브 성공)")
    return r


# ══════════════════════════════════════════════════════════════════════
# Phase 4: 예외 상황 검증
# ══════════════════════════════════════════════════════════════════════

def phase4_exceptions() -> dict:
    print("\n" + "=" * 60)
    print("  [Phase 4] 예외 상황 검증")
    print("=" * 60)

    scenarios: dict = {}

    # 4-A: 보충지번 재고 없음 → BLOCKED 전환 (DB 직접, 시뮬레이션 전용)
    print("\n  [4-A] 보충지번 재고 없음 → BLOCKED 처리")
    _upload("21:00", elapsed=11.5)
    wd = _wave("REGULAR")
    s4a: dict = {"wave_created": wd is not None, "blocked_tasks": 0, "status": "FAIL"}
    if wd:
        wave_id = wd["wave_id"]
        _approve_confirm(wave_id)
        with Session(engine) as s:
            tasks = s.exec(
                select(ReplenishConfirmedTask)
                .where(ReplenishConfirmedTask.wave_id == wave_id)
                .where(ReplenishConfirmedTask.task_status == "READY")
            ).all()
            blocked = 0
            for t in tasks[:3]:
                t.task_status = "BLOCKED"
                t.block_reason = "보충지번 재고 없음"
                blocked += 1
            s.commit()
        s4a["blocked_tasks"] = blocked
        s4a["status"] = "PASS" if blocked > 0 else "WARN"
        print(f"  BLOCKED 전환: {blocked}개  →  {s4a['status']}")

        # 전환 API 경로 확인 (READY→BLOCKED는 state machine 미지원 — 문서 갭)
        s4a["note"] = (
            "시뮬레이션에서는 DB 직접 변경. "
            "실제 API는 READY→QUEUED→SENT→BLOCKED 순서 필요 "
            "(POST /api/v1/tasks/{id}/transition)."
        )
    else:
        print("  웨이브 생성 실패 — 스킵")
    scenarios["4A_blocked"] = s4a

    # 4-B: 동일 SKU 연속 웨이브 중복 감지 부재
    print("\n  [4-B] 미완료 SKU 다음 웨이브 재등장 (중복 감지 GAP)")
    _upload("21:30", elapsed=12.0, force_shortage=5)
    wd2 = _wave("REGULAR")
    s4b: dict = {"status": "GAP"}
    if wd2:
        algo2  = wd2.get("algorithm", {})
        cands2 = algo2.get("total_candidates", 0)
        s4b["candidates_in_consecutive_wave"] = cands2
        s4b["note"] = (
            "이전 웨이브 READY/SENT 상태 SKU를 다음 웨이브에서 자동 제외하지 않음. "
            "관리자가 웨이브 목록을 확인해 수동 판단 필요."
        )
        print(f"  다음 웨이브 후보: {cands2}개 — 중복 자동감지 없음 (GAP)")
        _gap("GAP-04a", "MEDIUM", "연속 웨이브 중복 SKU 미제외",
             "직전 웨이브 미완료 SKU가 다음 슬롯에서 재추천될 수 있음. "
             "진행중(READY/SENT) 태스크 필터링 로직 부재.")
    scenarios["4B_duplicate"] = s4b

    # 4-C: 잘못된 CSV 컬럼 → 에러 메시지 품질
    print("\n  [4-C] 잘못된 CSV 형식 → 에러 메시지 품질")
    bad_csv = "wrong_col1,wrong_col2\n1,2\n"
    rc = client.post(
        "/api/v1/upload/inventory",
        files={"file": ("bad.csv", bad_csv.encode(), "text/csv")},
        data={"center_cd": "GGH1"},
    )
    err_body = rc.text
    has_col_info = any(kw in err_body for kw in ["상품코드", "column", "컬럼", "field", "필드", "missing"])
    s4c = {
        "status_code": rc.status_code,
        "error_mentions_column": has_col_info,
        "error_snippet": err_body[:200],
        "status": "PASS" if (rc.status_code != 200 and has_col_info)
                  else "WARN" if rc.status_code != 200
                  else "FAIL",
    }
    col_tag = "명시됨" if has_col_info else "명시 안됨 (개선 필요)"
    print(f"  응답: {rc.status_code}  컬럼 오류 명시: {col_tag}")
    scenarios["4C_bad_csv"] = s4c

    # 4-D: Slack 채널 설정 누락 시 동작
    print("\n  [4-D] Slack 메시지 생성 오류 처리")
    s4d: dict = {"status": "SKIP"}
    if wd2:
        with Session(engine) as s:
            try:
                ch_msgs = build_wave_messages_v2(wd2["wave_id"], s)
                s4d = {
                    "channels":      list(ch_msgs.keys()),
                    "message_count": sum(len(v) for v in ch_msgs.values()),
                    "status":        "PASS" if ch_msgs else "WARN",
                }
                print(f"  채널: {list(ch_msgs.keys())}  메시지: {s4d['message_count']}개")
            except Exception as e:
                s4d = {"status": "FAIL", "error": str(e)[:200]}
                print(f"  오류: {e}")

    _gap("GAP-06", "LOW", "Slack 전송 실패 재시도 없음",
         "채널 미설정 또는 bot_token 없을 때 메시지 유실. 재시도/알림 메커니즘 없음.")
    scenarios["4D_slack"] = s4d

    failed = sum(1 for v in scenarios.values() if v.get("status") == "FAIL")
    result = {"scenarios": scenarios, "status": "PASS" if failed == 0 else "WARN"}
    return result


# ══════════════════════════════════════════════════════════════════════
# Phase 5: 완료 이중 검증
# ══════════════════════════════════════════════════════════════════════

def phase5_verification() -> dict:
    print("\n" + "=" * 60)
    print("  [Phase 5] 완료 이중 검증")
    print("=" * 60)

    r: dict = {"cases": {}, "status": "PASS"}

    # 검증용 웨이브 생성
    _upload("22:00", elapsed=12.5, force_shortage=10)
    wd = _wave("REGULAR")
    if not wd:
        print("  웨이브 생성 실패 — Phase 5 스킵")
        r["status"] = "SKIP"
        return r

    wave_id = wd["wave_id"]
    _approve_confirm(wave_id)

    with Session(engine) as s:
        all_tasks = s.exec(
            select(ReplenishConfirmedTask)
            .where(ReplenishConfirmedTask.wave_id == wave_id)
            .where(ReplenishConfirmedTask.task_status == "READY")
        ).all()
        all_tasks = list(all_tasks)

    print(f"  검증 대상 태스크: {len(all_tasks)}개")

    # 5-A: DONE 표시 O + 재고 재업로드 → 다음 웨이브에서 미등장
    print("\n  [5-A] DONE 표시 O + 재고 재업로드 → 일치 확인")
    ca: dict = {"status": "SKIP"}
    if all_tasks:
        task_a = all_tasks[0]
        sku_a  = task_a.sku_id
        with Session(engine) as s:
            t = s.get(ReplenishConfirmedTask, task_a.task_id)
            t.task_status = "DONE"
            s.commit()
        replenished_skus.add(sku_a)

        # 재업로드: replenished_skus에 있으므로 가용 높게 생성
        _upload("22:30", elapsed=13.0)
        wd_next = _wave("REGULAR")
        reappears = False
        if wd_next:
            rc2 = client.get(f"/api/v1/waves/{wd_next['wave_id']}/candidates")
            if rc2.status_code == 200:
                reappears = any(c.get("sku_id") == sku_a for c in rc2.json())

        ca = {
            "sku": sku_a,
            "done_marked": True,
            "reappears_in_next_wave": reappears,
            "status": "PASS" if not reappears else "WARN",
        }
        tag = "재등장 없음 (정상)" if not reappears else "재등장 있음"
        print(f"  SKU {sku_a}: DONE 후 다음 웨이브 → {tag}  [{ca['status']}]")
    r["cases"]["5A_done_consistent"] = ca

    # 5-B: DONE 미표시 + 재고 재업로드 → 재추천 (정상 동작)
    print("\n  [5-B] DONE 미표시 + 재고 재업로드 → 재추천 여부 (정상 동작)")
    cb: dict = {"status": "SKIP"}
    if len(all_tasks) > 1:
        task_b = all_tasks[1]
        sku_b  = task_b.sku_id
        # DONE 미표시, replenished_skus 추가 안 함
        _upload("22:45", elapsed=13.25, force_shortage=5)
        wd_b = _wave("REGULAR")
        reappears_b = False
        if wd_b:
            rc2 = client.get(f"/api/v1/waves/{wd_b['wave_id']}/candidates")
            if rc2.status_code == 200:
                reappears_b = any(c.get("sku_id") == sku_b for c in rc2.json())
        cb = {
            "sku": sku_b, "done_marked": False,
            "reappears_in_next_wave": reappears_b,
            "status": "INFO",
            "note": "미완료 SKU 재추천은 정상 동작 (버그 아님)",
        }
        tag = "재등장 (정상)" if reappears_b else "미재등장"
        print(f"  SKU {sku_b}: DONE 미표시 → {tag}")
    r["cases"]["5B_undone_reappears"] = cb

    # 5-C: DONE 표시 O + 재고 여전히 가용=0 → 불일치 감지 여부 (GAP)
    print("\n  [5-C] DONE 표시 O + 재고 여전히 가용=0 → 불일치 감지 (GAP 예상)")
    cc: dict = {"status": "SKIP"}
    if len(all_tasks) > 2:
        task_c = all_tasks[2]
        sku_c  = task_c.sku_id
        with Session(engine) as s:
            t = s.get(ReplenishConfirmedTask, task_c.task_id)
            t.task_status = "DONE"
            s.commit()
        # replenished_skus에 추가 안 함 → 다음 CSV에서 가용=0 유지

        _upload("23:05", elapsed=13.5, force_shortage=30)
        wd_c = _wave("URGENT")
        mismatch_found = False
        if wd_c:
            _approve_confirm(wd_c["wave_id"])
            rc2 = client.get(f"/api/v1/waves/{wd_c['wave_id']}/candidates")
            if rc2.status_code == 200:
                mismatch_found = any(c.get("sku_id") == sku_c for c in rc2.json())

        cc = {
            "sku": sku_c, "done_marked": True, "stock_still_zero": True,
            "mismatch_detected": mismatch_found,
            "status": "GAP",
        }
        if not mismatch_found:
            print(f"  SKU {sku_c}: DONE이지만 재고=0 → 프로그램 감지 안됨 [GAP]")
            _gap("GAP-04b", "MEDIUM", "완료 이중검증 미연동",
                 "task_status=DONE으로 표시해도 재고 CSV에서 가용=0이면 불일치를 자동 감지하지 않음. "
                 "재고 재업로드 시 DONE 태스크와 재고를 대조하는 로직 필요.")
        else:
            print(f"  SKU {sku_c}: DONE이지만 재고=0 → 재추천됨 (불일치 확인 가능)")
            cc["status"] = "INFO"
    r["cases"]["5C_mismatch"] = cc

    # 추가 GAP 기록
    _gap("GAP-02", "MEDIUM", "인당 리스트 분할 없음",
         "section_seq 필드가 모델에 있으나 작업자별 리스트 분할 로직 미구현. "
         "존 단위(R존/P존)까지만 분리, 인당 배분은 관리자가 수동 처리.")
    _gap("GAP-05", "HIGH", "서버 다운 복구 프로세스 없음",
         "서버 다운 시 현장 fallback 절차(인쇄본, 수기 리스트) 없음. "
         "최소한 오프라인 뷰어 또는 마지막 웨이브 인쇄 가이드 필요.")
    _gap("GAP-07", "LOW", "신규/초보 작업자 구분 없음",
         "신규 일용직 대상 단순 SKU·소량·가까운 지번 우선 배분 로직 없음.")

    return r


# ══════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print("  원데이 운영 시뮬레이션 고도화 — 6-Phase")
    print("=" * 60)

    n_workers = _setup()
    print(f"  작업자 {n_workers}명 출근 처리 완료")

    # 피벗 CSV 1회 업로드 (300-SKU 기준)
    piv = gen_pivot()
    with open(piv, "rb") as f:
        rp = client.post(
            "/api/v1/upload/pivot-sales",
            files={"file": ("pivot.csv", f, "text/csv")},
            data={"center_cd": "GGH1"},
        )
    print(f"  피벗 CSV 업로드: {'OK' if rp.status_code == 200 else 'FAIL'}")

    # Phase 2~5 먼저 실행 (300-SKU 규모, 빠름)
    # Phase 1은 마지막 — 25k SKU가 DB에 쌓이면 이후 알고리즘이 느려지므로 격리
    report["phases"]["phase2_critical"]     = phase2_critical()
    report["phases"]["phase3_timeline"]     = phase3_timeline()
    report["phases"]["phase4_exceptions"]   = phase4_exceptions()
    report["phases"]["phase5_verification"] = phase5_verification()
    # Phase 1: 실규모 성능 측정 (25k SKU 로드, 알고리즘 포함)
    report["phases"]["phase1_perf"]         = phase1_perf()

    # Phase 6: GAP 리포트 출력
    print("\n" + "=" * 60)
    print("  [Phase 6] GAP 리포트")
    print("=" * 60)
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for g in sorted(gaps, key=lambda x: severity_order[x["severity"]]):
        print(f"  [{g['severity']:6s}] {g['gap_id']}: {g['title']}")
        print(f"          {g['detail'][:90]}")

    # 요약
    statuses = {k: v.get("status", "UNKNOWN") for k, v in report["phases"].items()}
    report["summary"] = {
        "phase_statuses": statuses,
        "total_gaps":  len(gaps),
        "high_gaps":   sum(1 for g in gaps if g["severity"] == "HIGH"),
        "medium_gaps": sum(1 for g in gaps if g["severity"] == "MEDIUM"),
        "low_gaps":    sum(1 for g in gaps if g["severity"] == "LOW"),
    }

    out = Path("tests/oneday/operational_sim_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print("  최종 요약")
    print("=" * 60)
    icons = {"PASS": "OK", "WARN": "!!", "FAIL": "XX", "SKIP": "--", "UNKNOWN": "??"}
    for phase, status in statuses.items():
        print(f"  [{icons.get(status, '??')}] {phase}: {status}")
    s = report["summary"]
    print(f"\n  GAP: {s['total_gaps']}개  (HIGH:{s['high_gaps']} MEDIUM:{s['medium_gaps']} LOW:{s['low_gaps']})")
    print(f"  보고서: {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
