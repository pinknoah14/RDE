import json
from collections import defaultdict
from typing import Any

from sqlmodel import Session, select

from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation, ReplenishTaskQueue
from app.models.worker import Worker
from app.models.wave import Wave
from app.models.zone import ZoneConfig


PROXIMITY_ICON = {4: "🟢", 3: "🟠", 2: "🟡", 1: "⚪"}


def build_task_block(task: ReplenishConfirmedTask, locations: list) -> list[dict]:
    """단일 태스크 Block Kit 블록 생성."""
    loc_lines = []
    for loc in locations:
        days_str = f"D-{loc.sales_deadline_days}" if loc.sales_deadline_days is not None else ""
        score_icon = PROXIMITY_ICON.get(loc.proximity_score or 0, "")
        line = f"• `{loc.replenish_bin}` {loc.allocated_qty}개"
        if days_str:
            line += f" {days_str}"
        if score_icon:
            line += f" {score_icon}"
        loc_lines.append(line)

    locations_text = "\n".join(loc_lines) if loc_lines else "⚠️ 보충지번 정보 없음"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{task.sku_name}*\n"
                    f"피킹: `{task.picking_bin}` → 보충:\n"
                    f"{locations_text}"
                ),
            },
        },
        {"type": "divider"},
    ]


def build_wave_messages(wave_id: int, session: Session) -> dict[str, list]:
    """웨이브 태스크를 채널별 Block Kit 메시지로 구성."""
    tasks = session.exec(
        select(ReplenishConfirmedTask).where(
            ReplenishConfirmedTask.wave_id == wave_id,
            ReplenishConfirmedTask.task_status.in_(["READY", "QUEUED"]),
        )
    ).all()

    channel_blocks: dict[str, list] = {}
    for task in tasks:
        locations = session.exec(
            select(ReplenishTaskLocation).where(
                ReplenishTaskLocation.task_id == task.task_id
            ).order_by(ReplenishTaskLocation.seq)
        ).all()
        ch = task.slack_channel or "unknown"
        channel_blocks.setdefault(ch, [])
        channel_blocks[ch].extend(build_task_block(task, locations))

    return channel_blocks


def send_wave_messages(wave_id: int, session: Session) -> dict[str, Any]:
    """Slack으로 웨이브 메시지 전송. bot_token 미설정 시 queue에만 저장."""
    from app.core.config import get_config

    try:
        bot_token = get_config("slack_bot_token", session)
    except KeyError:
        bot_token = ""

    channel_blocks = build_wave_messages(wave_id, session)
    results: dict[str, Any] = {"sent": [], "queued": [], "failed": []}

    for channel, blocks in channel_blocks.items():
        queue_entry = ReplenishTaskQueue(
            wave_id=wave_id,
            slack_channel=channel,
            message_text=json.dumps(blocks, ensure_ascii=False)[:4000],
            blocks_json=json.dumps(blocks, ensure_ascii=False),
        )

        if bot_token:
            try:
                from slack_sdk import WebClient
                client = WebClient(token=bot_token)
                resp = client.chat_postMessage(
                    channel=channel,
                    blocks=blocks,
                    text=f"보충 웨이브 #{wave_id}",
                )
                queue_entry.queue_status = "SENT"
                queue_entry.slack_ts = resp.get("ts")
                from datetime import datetime
                queue_entry.sent_at = datetime.utcnow()
                results["sent"].append(channel)
            except Exception as exc:
                queue_entry.queue_status = "FAILED"
                queue_entry.error_message = str(exc)[:500]
                results["failed"].append({"channel": channel, "error": str(exc)})
        else:
            queue_entry.queue_status = "WAITING"
            results["queued"].append(channel)

        session.add(queue_entry)

    session.commit()
    return results


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
    items_per_msg 단위로 메시지 분할.
    """
    header = f"*{channel_label} {wave_name}*\n\n"
    footer = "\n\n최대한 보충 부탁드립니다. <!here>"

    def _replen_bin(task) -> str:
        locs = locations_map.get(task["task_id"], [])
        if not locs:
            return "-"
        first = locs[0]
        return getattr(first, "replenish_bin", first.get("replenish_bin", "-") if isinstance(first, dict) else "-")

    lines: list[str] = []

    if worker_type == "FORKLIFT":
        batched = [t for t in tasks if t.get("batch_tag")]
        singles = [t for t in tasks if not t.get("batch_tag")]

        batched_sorted = sorted(
            batched,
            key=lambda t: (t.get("batch_tag") or "", t.get("batch_seq") or 0),
        )

        seq = 1
        current_tag = None
        for t in batched_sorted:
            tag = t.get("batch_tag")
            if tag != current_tag:
                lines.append(f"[📦 {tag}]")
                current_tag = tag
            lines.append(
                f"  {seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            )
            seq += 1

        for t in singles:
            lines.append(
                f"{seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            )
            seq += 1
    else:
        # WALKING: 보충지번 기준 정렬
        sorted_tasks = sorted(tasks, key=lambda t: _replen_bin(t))
        for seq, t in enumerate(sorted_tasks, 1):
            lines.append(
                f"{seq}. {t['picking_bin']}  {t['sku_id']}  {t['sku_name']}  {_replen_bin(t)}"
            )

    # items_per_msg 단위 분할 (배치 헤더는 카운트 제외)
    messages: list[str] = []
    chunk: list[str] = []
    real_count = 0
    for line in lines:
        chunk.append(line)
        if not line.startswith("[📦"):
            real_count += 1
        if real_count >= items_per_msg:
            messages.append(header + "\n".join(chunk) + footer)
            chunk = []
            real_count = 0
    if chunk:
        messages.append(header + "\n".join(chunk) + footer)

    return messages if messages else [header + "(태스크 없음)" + footer]


def build_wave_messages_v2(wave_id: int, session: Session) -> dict[str, list[str]]:
    """
    v2.0 채널 라우팅: 채널 × 작업유형(FORKLIFT/WALKING) × 숙련도(JUNIOR 분리).

    반환 키 형식: "{channel}_{group}"
      group ∈ {"forklift", "walking", "junior"}
    """
    from app.core.config import get_config

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
    from app.models.task import ReplenishCandidate
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
    from app.core.config import get_config

    try:
        bot_token = get_config("slack_bot_token", session)
    except KeyError:
        bot_token = ""

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
                from slack_sdk import WebClient
                client = WebClient(token=bot_token)
                client.chat_delete(channel=q.slack_channel, ts=q.slack_ts)
                deleted.append(q.slack_channel)
            except Exception as exc:
                failed.append({"channel": q.slack_channel, "error": str(exc)})

    return {"deleted": deleted, "failed": failed}
