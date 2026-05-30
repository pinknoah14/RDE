"""서버 다운 대비 인쇄용 웨이브 리스트 HTML 생성 (GAP-05)."""
from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, select

from app.models.task import ReplenishConfirmedTask, ReplenishTaskLocation
from app.models.wave import Wave
from app.models.worker import Worker


_CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
       font-size: 11pt; color: #000; padding: 16px; }
h1 { font-size: 14pt; border-bottom: 2px solid #000;
     padding-bottom: 6px; margin-bottom: 8px; }
h2 { font-size: 12pt; background: #ddd; padding: 4px 8px;
     margin: 16px 0 4px; }
h3 { font-size: 11pt; padding: 3px 0 3px 8px;
     border-left: 3px solid #555; margin: 8px 0 3px; color: #333; }
p.meta { font-size: 9.5pt; color: #555; margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px;
        font-size: 10pt; }
th { background: #f0f0f0; border: 1px solid #999;
     padding: 4px 6px; text-align: center; }
td { border: 1px solid #ccc; padding: 3px 6px; }
td.c { text-align: center; }
td.nb { white-space: nowrap; }
.print-btn { position: fixed; top: 10px; right: 10px; padding: 8px 16px;
             background: #333; color: #fff; border: none; cursor: pointer;
             font-size: 12pt; border-radius: 4px; }
@media print {
  .print-btn { display: none; }
  h2 { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  @page { margin: 15mm; size: A4; }
}
</style>
"""


def generate_print_html(wave_id: int, session: Session) -> str:
    wave = session.get(Wave, wave_id)
    if not wave:
        return (
            "<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'></head>"
            "<body><p>웨이브를 찾을 수 없습니다.</p></body></html>"
        )

    tasks = session.exec(
        select(ReplenishConfirmedTask)
        .where(
            ReplenishConfirmedTask.wave_id == wave_id,
            ReplenishConfirmedTask.task_status != "CANCELLED",
        )
        .order_by(
            ReplenishConfirmedTask.list_section,
            ReplenishConfirmedTask.section_seq,
            ReplenishConfirmedTask.list_seq,
            ReplenishConfirmedTask.task_id,
        )
    ).all()

    # task → locations
    locations_map: dict[int, list[ReplenishTaskLocation]] = {}
    if tasks:
        locs = session.exec(
            select(ReplenishTaskLocation)
            .where(ReplenishTaskLocation.task_id.in_([t.task_id for t in tasks]))
            .order_by(ReplenishTaskLocation.task_id, ReplenishTaskLocation.seq)
        ).all()
        for loc in locs:
            locations_map.setdefault(loc.task_id, []).append(loc)

    # worker lookup
    worker_map: dict[int, Worker] = {
        w.worker_id: w
        for w in session.exec(select(Worker)).all()
    }

    # channel → section_seq → tasks
    channel_sections: dict[str, dict[int, list[ReplenishConfirmedTask]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for t in tasks:
        channel_sections[t.slack_channel][t.section_seq or 0].append(t)

    # ---- wave header ----
    _type = {"REGULAR": "정기", "URGENT": "긴급", "PRESTOCK": "선보충"}.get(
        wave.wave_type, wave.wave_type
    )
    _status = {"DRAFT": "초안", "CONFIRMED": "확정", "SENT": "전송됨", "COMPLETED": "완료"}.get(
        wave.wave_status, wave.wave_status
    )
    created = wave.created_at.strftime("%Y-%m-%d %H:%M") if wave.created_at else "-"
    confirmed = wave.confirmed_at.strftime("%Y-%m-%d %H:%M") if wave.confirmed_at else "미확정"
    printed_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ---- build sections HTML ----
    sections_html = ""
    for channel in sorted(channel_sections.keys()):
        sections_html += f"<h2>{channel}</h2>"
        for sec_idx in sorted(channel_sections[channel].keys()):
            sec_tasks = channel_sections[channel][sec_idx]
            worker = None
            for t in sec_tasks:
                if t.worker_id and t.worker_id in worker_map:
                    worker = worker_map[t.worker_id]
                    break

            if sec_idx > 0:
                label = f"섹션 {sec_idx}"
                if worker:
                    label += f" — {worker.worker_name}"
                sections_html += f"<h3>{label}</h3>"

            rows = ""
            for i, t in enumerate(sec_tasks, 1):
                locs = locations_map.get(t.task_id, [])
                bins_str = "<br>".join(
                    f"{loc.replenish_bin}&nbsp;({loc.allocated_qty}개)"
                    for loc in locs
                ) or "-"
                rows += (
                    f"<tr>"
                    f"<td class='c nb'>{i}</td>"
                    f"<td class='nb'>{t.picking_bin}</td>"
                    f"<td class='nb'>{t.sku_id}</td>"
                    f"<td>{t.sku_name}</td>"
                    f"<td class='c'>{t.total_qty}</td>"
                    f"<td class='nb'>{bins_str}</td>"
                    f"<td class='c'>☐</td>"
                    f"</tr>"
                )

            sections_html += (
                "<table>"
                "<thead><tr>"
                "<th>#</th><th>피킹지번</th><th>상품코드</th><th>상품명</th>"
                "<th>수량</th><th>보충지번</th><th>완료</th>"
                "</tr></thead>"
                f"<tbody>{rows}</tbody>"
                "</table>"
            )

    return (
        "<!DOCTYPE html>"
        "<html lang='ko'>"
        "<head><meta charset='UTF-8'>"
        f"<title>Wave {wave_id} — {wave.wave_name}</title>"
        f"{_CSS}"
        "</head><body>"
        "<button class='print-btn' onclick='window.print()'>🖨️ 인쇄</button>"
        f"<h1>Wave #{wave_id} — {wave.wave_name}</h1>"
        f"<p class='meta'>유형: {_type} | 상태: {_status} | 생성: {created} | 확정: {confirmed} | 총 {len(tasks)}건</p>"
        f"<p class='meta' style='color:#aaa;font-size:9pt'>출력: {printed_at}</p>"
        f"{sections_html}"
        "</body></html>"
    )
