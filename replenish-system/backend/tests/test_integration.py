"""
Phase 4 — 전체 흐름 통합 테스트
CSV → DB 저장 → 알고리즘 실행 → 후보 검증
"""
import time
import pytest
from datetime import datetime
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import seed_system_config
from app.core.config import invalidate_cache
from app.core.dependencies import get_session
from app.models.zone import ZoneConfig
from app.models.wave import Wave
from app.models.task import ReplenishCandidate, ReplenishTaskLocation
from app.models.upload import UploadSession

FIXTURES = Path(__file__).parent / "fixtures"

# ── 모듈 전용 인메모리 DB (StaticPool: 모든 커넥션이 동일 DB 공유) ──────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _seed_zones(session: Session) -> None:
    zones = [
        ("RA", "R존 메인",   "R존",   "FORKLIFT", "MAIN"),
        ("RB", "R존 B구역",  "R존",   "FORKLIFT", "MAIN"),
        ("SF", "S존 1층",    "S존",   "WALKING",  "MAIN"),
        ("SM", "S존 메자닌", "S존",   "WALKING",  "MAIN"),
        ("PW", "P존 W구역",  "P존",   "FORKLIFT", "SUB"),
        ("NC", "NC존",       "NC존",  "FORKLIFT", "MAIN"),
        ("PA", "PA존",       "PA존",  "WALKING",  "SUB"),
        ("SC", "SC존",       "SC존",  "FORKLIFT", "SUB"),
    ]
    for prefix, name, ch, atype, section in zones:
        if not session.exec(
            select(ZoneConfig).where(ZoneConfig.zone_prefix == prefix)
        ).first():
            session.add(ZoneConfig(
                zone_prefix=prefix, zone_name=name, slack_channel=ch,
                access_type=atype, list_section=section, is_special_zone=False,
            ))
    session.commit()


def _load_csv_data(session: Session) -> None:
    """픽스처 CSV를 파싱해 DB에 저장."""
    from app.services.csv_parser import (
        load_inventory_csv, classify_inventory, update_picking_history,
    )
    from app.services.sales_service import upsert_daily_sales, update_all_sales_summaries
    from app.services.sales_parser import parse_outbound_csv
    from app.api.upload import save_replenish_snapshot

    inv_path   = FIXTURES / "inventory_sample.csv"
    sales_path = FIXTURES / "pivot_sample.csv"

    if inv_path.exists():
        inv_df     = load_inventory_csv(str(inv_path))
        classified = classify_inventory(inv_df, session)
        update_picking_history(classified["picking"], session)

        upload_rec = UploadSession(
            upload_type="INVENTORY", file_name="inventory_sample.csv",
            uploaded_by="테스트", uploaded_at=datetime.utcnow(),
            record_count=len(inv_df), center_cd="GGH1",
        )
        session.add(upload_rec)
        session.commit()
        session.refresh(upload_rec)
        save_replenish_snapshot(classified["replenish"], upload_rec.upload_id, "GGH1", session)

    if sales_path.exists():
        sales_df = parse_outbound_csv(sales_path.read_bytes())
        upsert_daily_sales("GGH1", sales_df, session)
        update_all_sales_summaries("GGH1", session)


@pytest.fixture(scope="module")
def full_session():
    if not (FIXTURES / "inventory_sample.csv").exists():
        pytest.skip("fixtures 없음 — 먼저 실행: python tests/fixtures/generate_fixtures.py")

    invalidate_cache()
    SQLModel.metadata.create_all(_engine)

    with Session(_engine) as s:
        seed_system_config(s)
        _seed_zones(s)
        _load_csv_data(s)
        yield s


def _make_wave(session: Session) -> int:
    wave = Wave(
        wave_name=f"통합테스트_{datetime.utcnow().strftime('%H%M%S%f')}",
        wave_status="DRAFT",
        target_sku_count=200,
        created_by="통합테스트",
    )
    session.add(wave)
    session.commit()
    session.refresh(wave)
    return wave.wave_id


# ── Step 2 통합 테스트 ─────────────────────────────────────────────────────


class TestFullPipeline:

    def test_full_pipeline_timing(self, full_session):
        """전체 파이프라인 실행 시간: 30초 이내"""
        from app.services.algorithm import run_algorithm

        wave_id = _make_wave(full_session)
        t0 = time.time()
        result = run_algorithm("GGH1", wave_id, full_session)
        elapsed = time.time() - t0

        print(f"\n알고리즘 실행: {elapsed:.1f}초 / 후보: {result.total_candidates}개")
        assert elapsed < 30.0, f"파이프라인 30초 초과: {elapsed:.1f}초"
        assert result.total_candidates >= 0

    def test_candidate_count_reasonable(self, full_session):
        """
        run_algorithm 자체는 max_candidates 제한 없이 모든 적격 SKU를 후보로 등록.
        등급별 분포가 합리적인지(CRITICAL이 전체 50% 이하) 확인.
        max_candidates 필터링은 API 레이어에서 수행.
        """
        from app.services.algorithm import run_algorithm
        from fastapi.testclient import TestClient

        wave_id = _make_wave(full_session)
        result  = run_algorithm("GGH1", wave_id, full_session)

        if result.total_candidates == 0:
            pytest.skip("재고/판매 데이터 부족으로 후보 없음 — 데이터 확인 필요")

        # 등급 분포 확인 (CRITICAL 과다 방지)
        critical_ratio = result.critical_count / result.total_candidates
        assert critical_ratio <= 0.5, f"CRITICAL 비율 과다: {critical_ratio:.0%}"
        print(f"\n후보 {result.total_candidates}개: "
              f"C={result.critical_count} H={result.high_count} "
              f"M={result.medium_count} L={result.low_count}")

    def test_fefo_order_in_replenish_locations(self, full_session):
        """확정 태스크의 보충지번 순서가 FEFO(판매마감일수 ASC)인지 검증"""
        candidates = full_session.exec(
            select(ReplenishCandidate).limit(50)
        ).all()

        violations = 0
        for c in candidates:
            locs = full_session.exec(
                select(ReplenishTaskLocation)
                .where(ReplenishTaskLocation.task_id == c.candidate_id)
                .order_by(ReplenishTaskLocation.seq)
            ).all()
            for i in range(len(locs) - 1):
                d_curr = locs[i].sales_deadline_days
                d_next = locs[i + 1].sales_deadline_days
                if d_curr is not None and d_next is not None and d_curr > d_next:
                    violations += 1

        assert violations == 0, f"FEFO 위반 {violations}건 발견"

    def test_no_duplicate_replenishment_within_wave(self, full_session):
        """단일 웨이브 내 동일 SKU 중복 추천 없음"""
        from app.services.algorithm import run_algorithm
        from sqlmodel import func

        wave_id = _make_wave(full_session)
        run_algorithm("GGH1", wave_id, full_session)

        stmt = (
            select(ReplenishCandidate.sku_id, func.count())
            .where(ReplenishCandidate.wave_id == wave_id)
            .group_by(ReplenishCandidate.sku_id)
            .having(func.count() > 1)
        )
        duplicates = full_session.exec(stmt).all()
        assert len(duplicates) == 0, \
            f"웨이브 내 중복 SKU: {[d[0] for d in duplicates[:5]]}"

    def test_expired_excluded_from_candidates(self, full_session):
        """만료 보충지번(판매마감일수 <= 0)이 알고리즘 추천에 미포함"""
        from app.models.inventory import ReplenishBinSnapshot
        from app.services.algorithm import run_algorithm

        wave_id = _make_wave(full_session)
        run_algorithm("GGH1", wave_id, full_session)

        # ReplenishBinSnapshot에서 만료 행이 후보 생성에 사용됐는지 간접 확인:
        # classify_inventory가 deadline_days <= 0 행을 필터링하므로
        # ReplenishBinSnapshot에 저장 자체가 안 돼야 함.
        expired_snapshots = full_session.exec(
            select(ReplenishBinSnapshot)
            .where(
                ReplenishBinSnapshot.deadline_days.is_not(None),
                ReplenishBinSnapshot.deadline_days <= 0,
            )
        ).all()
        # save_replenish_snapshot은 deadline_days > 0 필터 후 저장
        assert len(expired_snapshots) == 0, \
            f"만료 보충지번 스냅샷 {len(expired_snapshots)}건이 DB에 존재"

    def test_risk_score_range(self, full_session):
        """모든 후보의 risk_score가 0~100 범위인지 확인"""
        from app.services.algorithm import run_algorithm

        wave_id = _make_wave(full_session)
        run_algorithm("GGH1", wave_id, full_session)

        candidates = full_session.exec(
            select(ReplenishCandidate)
            .where(ReplenishCandidate.wave_id == wave_id)
        ).all()

        invalid = [c for c in candidates if not (0 <= c.risk_score <= 100)]
        assert len(invalid) == 0, \
            f"risk_score 범위 위반 {len(invalid)}건: {[(c.sku_id, c.risk_score) for c in invalid[:3]]}"
