import json
from typing import Any

from sqlmodel import Session, select

from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation, ReplenishTaskQueue


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
