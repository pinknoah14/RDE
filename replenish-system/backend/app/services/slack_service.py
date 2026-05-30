import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from slack_sdk import WebClient
from sqlmodel import Session, select

from app.core.config import get_config
from app.core.logging_config import get_logger
from app.models.task import ReplenishCandidate, ReplenishConfirmedTask, ReplenishTaskLocation, ReplenishTaskQueue
from app.models.worker import Worker
from app.models.wave import Wave


logger = get_logger("slack")


def _get_bot_token(session: Session) -> str:
    try:
        return get_config("slack_bot_token", session)
    except KeyError:
        return ""


def _cfg_int(key: str, session: Session, default: int) -> int:
    try:
        return int(get_config(key, session))
    except (KeyError, ValueError, TypeError):
        return default


def _post_with_retry(
    client: WebClient,
    channel: str,
    text: str,
    max_attempts: int,
    base_delay: float,
) -> tuple[bool, str | None, str | None, int]:
    """Slack 메시지 전송 + 지수 백오프 재시도.

    반환: (성공여부, slack_ts, error_message, 시도횟수)
    """
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.chat_postMessage(channel=channel, text=text)
            return True, resp.get("ts"), None, attempt
        except Exception as exc:  # noqa: BLE001 — Slack SDK 다양한 예외 포괄
            last_error = str(exc)
            if attempt < max_attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))  # 1s, 2s, 4s ...
    return False, None, last_error, max_attempts


def send_wave_messages(wave_id: int, session: Session) -> dict[str, Any]:
    """Slack으로 웨이브 메시지 전송 (v2.0 현장 형식).

    bot_token 미설정 시 queue에만 저장.
    v2.3: build_wave_messages_v2 (text 라인 형식) 사용. 배치 그룹 절단 방지 포함.
    v2.4 (GAP-06): 전송 실패 시 지수 백오프 재시도(slack_max_retries회).
    """
    bot_token = _get_bot_token(session)
    max_retries = _cfg_int("slack_max_retries", session, 3)
    retry_base = _cfg_int("slack_retry_base_sec", session, 1)
    channel_messages = build_wave_messages_v2(wave_id, session)
    results: dict[str, Any] = {"sent": [], "queued": [], "failed": []}

    client = WebClient(token=bot_token) if bot_token else None

    for channel, messages in channel_messages.items():
        for msg_idx, text in enumerate(messages):
            queue_entry = ReplenishTaskQueue(
                wave_id=wave_id,
                slack_channel=channel,
                message_text=text[:4000],
                blocks_json=None,
            )

            if client:
                ok, ts, err, attempts = _post_with_retry(
                    client, channel, text, max_retries, retry_base
                )
                queue_entry.retry_count = attempts - 1
                if ok:
                    queue_entry.queue_status = "SENT"
                    queue_entry.slack_ts = ts
                    queue_entry.sent_at = datetime.utcnow()
                    results["sent"].append(channel)
                    logger.info("Slack 전송", wave_id=wave_id, channel=channel, ts=ts)
                else:
                    queue_entry.queue_status = "FAILED"
                    queue_entry.error_message = (err or "")[:500]
                    results["failed"].append({"channel": channel, "error": err})
                    logger.error(
                        "Slack 전송 실패", wave_id=wave_id, channel=channel,
                        error=err, attempts=attempts,
                    )
            else:
                queue_entry.queue_status = "WAITING"
                if msg_idx == 0:
                    results["queued"].append(channel)

            session.add(queue_entry)

    session.commit()
    return results


def retry_failed_messages(wave_id: int, session: Session) -> dict[str, Any]:
    """FAILED 상태 큐 항목을 재전송 (GAP-06 수동 재시도).

    bot_token 미설정 시 아무 동작 안 함.
    """
    bot_token = _get_bot_token(session)
    results: dict[str, Any] = {"retried": [], "still_failed": [], "skipped": 0}

    if not bot_token:
        results["skipped"] = -1  # 토큰 없음 신호
        return results

    max_retries = _cfg_int("slack_max_retries", session, 3)
    retry_base = _cfg_int("slack_retry_base_sec", session, 1)
    client = WebClient(token=bot_token)

    failed = session.exec(
        select(ReplenishTaskQueue).where(
            ReplenishTaskQueue.wave_id == wave_id,
            ReplenishTaskQueue.queue_status == "FAILED",
        )
    ).all()

    for q in failed:
        ok, ts, err, attempts = _post_with_retry(
            client, q.slack_channel, q.message_text, max_retries, retry_base
        )
        q.retry_count += attempts
        if ok:
            q.queue_status = "SENT"
            q.slack_ts = ts
            q.sent_at = datetime.utcnow()
            q.error_message = None
            results["retried"].append(q.slack_channel)
            logger.info("Slack 재전송 성공", wave_id=wave_id, channel=q.slack_channel, ts=ts)
        else:
            q.error_message = (err or "")[:500]
            results["still_failed"].append({"channel": q.slack_channel, "error": err})
            logger.error("Slack 재전송 실패", wave_id=wave_id, channel=q.slack_channel, error=err)
        session.add(q)

    session.commit()
    return results


def _count_real_items(lines: list[str]) -> int:
    """[📦 헤더] 같은 장식 라인 제외한 실제 항목 수"""
    return sum(1 for line in lines if line.strip() and not line.startswith("[📦"))


def _chunk_preserving_batches(
    line_groups: list[list[str]],
    items_per_msg: int,
) -> list[list[str]]:
    """
    배치 그룹(동일 파렛트 묶음)이 다른 메시지로 찢어지지 않도록 분할.

    규칙:
      - 현재 N개 + 다음 그룹 M개 > items_per_msg 이면 → 다음 메시지로 통째 이동
      - 단일 그룹이 items_per_msg×2 초과 시 예외적 분할 허용
        (파렛트 1개에 12개+ SKU 혼적인 극단적 케이스)
    """
    chunks: list[list[str]] = []
    current: list[str] = []

    for group in line_groups:
        group_size   = _count_real_items(group)
        current_size = _count_real_items(current)

        if current_size > 0 and current_size + group_size > items_per_msg:
            chunks.append(current)
            current = list(group)
        else:
            current.extend(group)

        # 단일 그룹이 items_per_msg×2 초과 → 예외 분할
        if _count_real_items(current) > items_per_msg * 2:
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)

    return chunks if chunks else [[]]


def build_wave_message_v2(
    tasks: list,
    locations_map: dict,
    wave_name: str,
    channel_label: str,
    worker_type: str = "FORKLIFT",
    items_per_msg: int = 6,
) -> list[str]:
    """
    현장 형식 Slack 메시지 생성.
    형식: (순번). (피킹지번)  (상품코드)  (상품명)  (보충지번)

    FORKLIFT: 배치 태그 그룹 [📦 보충지번] 헤더 시각화
    WALKING:  보충지번(1순위) 기준 정렬 (L카트 동선 최적화)

    v2.1: 배치 그룹이 메시지 사이에서 절단되지 않도록 그룹 단위로 분할.
    """
    header = f"*{channel_label} {wave_name}*\n\n"
    footer = "\n\n최대한 보충 부탁드립니다. <!here>"

    def _replen_bin(task) -> str:
        locs = locations_map.get(task["task_id"], [])
        if not locs:
            return "-"
        first = locs[0]
        if isinstance(first, dict):
            return first.get("replenish_bin", "-")
        return getattr(first, "replenish_bin", "-")

    line_groups: list[list[str]] = []

    if worker_type == "FORKLIFT":
        batched = [t for t in tasks if t.get("batch_tag")]
        singles = [t for t in tasks if not t.get("batch_tag")]

        batched_sorted = sorted(
            batched,
            key=lambda t: (t.get("batch_tag") or "", t.get("batch_seq") or 0),
        )

        # 배치 그룹별 묶음 (헤더 + 라인들)
        batch_groups: dict[str, list[str]] = {}
        seq = 1
        for t in batched_sorted:
            tag = t.get("batch_tag")
            if tag not in batch_groups:
                batch_groups[tag] = [f"[📦 {tag}]"]
            batch_groups[tag].append(
                f"  {seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            )
            seq += 1

        line_groups.extend(batch_groups.values())

        # 단독 건 각각 개별 그룹 (1개짜리 그룹)
        for t in singles:
            line_groups.append([
                f"{seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            ])
            seq += 1
    else:
        # WALKING: 보충지번 기준 정렬, 각 항목 개별 그룹
        sorted_tasks = sorted(tasks, key=lambda t: _replen_bin(t))
        for seq, t in enumerate(sorted_tasks, 1):
            line_groups.append([
                f"{seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            ])

    # v2.1: 배치 그룹 절단 방지 분할
    chunks = _chunk_preserving_batches(line_groups, items_per_msg)

    messages = [header + "\n".join(c) + footer for c in chunks if c]
    return messages if messages else [header + "(태스크 없음)" + footer]


def build_wave_messages_v2(wave_id: int, session: Session) -> dict[str, list[str]]:
    """
    v2.0 채널 라우팅: 채널 × 작업유형(FORKLIFT/WALKING) × 숙련도(JUNIOR 분리).

    반환 키 형식: "{channel}_{group}"
      group ∈ {"forklift", "walking", "junior"}
    """
    try:
        items_per_msg = int(get_config("slack_items_per_message", session) or 6)
    except KeyError:
        items_per_msg = 6

    tasks = session.exec(
        select(ReplenishConfirmedTask).where(
            ReplenishConfirmedTask.wave_id == wave_id,
            ReplenishConfirmedTask.task_status.in_(["READY", "QUEUED", "SENT"]),
        )
    ).all()

    # task_id → locations 맵
    locations_map: dict[int, list] = {}
    for t in tasks:
        locations_map[t.task_id] = session.exec(
            select(ReplenishTaskLocation)
            .where(ReplenishTaskLocation.task_id == t.task_id)
            .order_by(ReplenishTaskLocation.seq)
        ).all()

    # candidate_id → batch_tag/batch_seq 맵
    cand_ids = [t.candidate_id for t in tasks if t.candidate_id]
    cand_tags: dict[int, dict] = {}
    if cand_ids:
        cands = session.exec(
            select(ReplenishCandidate).where(ReplenishCandidate.candidate_id.in_(cand_ids))
        ).all()
        for c in cands:
            cand_tags[c.candidate_id] = {
                "batch_tag": c.batch_tag,
                "batch_seq": c.batch_seq,
            }

    # 작업자 정보 (claimed_by 기준)
    workers = session.exec(select(Worker)).all()
    worker_map = {str(w.worker_id): w for w in workers}

    wave = session.get(Wave, wave_id)
    wave_name = wave.wave_name if wave else f"웨이브 {wave_id}"

    # 그룹 분류
    groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        channel = task.slack_channel or "unknown"
        tags = cand_tags.get(task.candidate_id, {})

        worker = worker_map.get(task.claimed_by) if task.claimed_by else None
        work_type = worker.work_type if worker else "FORKLIFT"
        skill_level = worker.skill_level if worker else "NORMAL"

        if skill_level == "JUNIOR":
            group = "junior"
        elif work_type == "WALKING":
            group = "walking"
        else:
            group = "forklift"

        groups[f"{channel}_{group}"].append({
            "task_id": task.task_id,
            "picking_bin": task.picking_bin,
            "sku_id": task.sku_id,
            "sku_name": task.sku_name,
            "batch_tag": tags.get("batch_tag"),
            "batch_seq": tags.get("batch_seq"),
            "section_seq": task.section_seq,
            "worker_id": task.worker_id,
        })

    result: dict[str, list[str]] = {}
    for key, task_list in groups.items():
        channel_label, group = key.rsplit("_", 1)
        wt = "WALKING" if group in ("walking", "junior") else "FORKLIFT"
        suffix = ""
        if group == "walking":
            suffix = " (도보)"
        elif group == "junior":
            suffix = " (미숙련 후순위)"

        # section_seq가 설정된 경우 섹션별로 분리하여 각각 메시지 생성
        section_ids = sorted({
            t["section_seq"] for t in task_list if t.get("section_seq") is not None
        })

        if len(section_ids) > 1:
            all_messages: list[str] = []
            for sec in section_ids:
                sec_tasks = [t for t in task_list if t.get("section_seq") == sec]
                wid = next((t["worker_id"] for t in sec_tasks if t.get("worker_id")), None)
                assigned_worker = worker_map.get(str(wid)) if wid else None
                sec_label = f" [섹션{sec}"
                if assigned_worker:
                    sec_label += f"/{assigned_worker.worker_name}"
                sec_label += "]"
                all_messages.extend(build_wave_message_v2(
                    tasks=sec_tasks,
                    locations_map=locations_map,
                    wave_name=wave_name + suffix + sec_label,
                    channel_label=channel_label,
                    worker_type=wt,
                    items_per_msg=items_per_msg,
                ))
            # section_seq 미설정 태스크가 섞여 있는 경우 별도 처리
            unsectioned = [t for t in task_list if t.get("section_seq") is None]
            if unsectioned:
                all_messages.extend(build_wave_message_v2(
                    tasks=unsectioned,
                    locations_map=locations_map,
                    wave_name=wave_name + suffix,
                    channel_label=channel_label,
                    worker_type=wt,
                    items_per_msg=items_per_msg,
                ))
            result[key] = all_messages
        else:
            result[key] = build_wave_message_v2(
                tasks=task_list,
                locations_map=locations_map,
                wave_name=wave_name + suffix,
                channel_label=channel_label,
                worker_type=wt,
                items_per_msg=items_per_msg,
            )

    return result


def delete_wave_messages(wave_id: int, session: Session) -> dict[str, Any]:
    """전송된 메시지 삭제."""
    bot_token = _get_bot_token(session)

    queues = session.exec(
        select(ReplenishTaskQueue).where(
            ReplenishTaskQueue.wave_id == wave_id,
            ReplenishTaskQueue.queue_status == "SENT",
            ReplenishTaskQueue.slack_ts.is_not(None),
        )
    ).all()

    deleted, failed = [], []
    for q in queues:
        if bot_token and q.slack_ts:
            try:
                client = WebClient(token=bot_token)
                client.chat_delete(channel=q.slack_channel, ts=q.slack_ts)
                deleted.append(q.slack_channel)
                logger.info("Slack 메시지 삭제", wave_id=wave_id, channel=q.slack_channel, ts=q.slack_ts)
            except Exception as exc:
                failed.append({"channel": q.slack_channel, "error": str(exc)})
                logger.error("Slack 삭제 실패", wave_id=wave_id, channel=q.slack_channel, error=str(exc))

    return {"deleted": deleted, "failed": failed}
