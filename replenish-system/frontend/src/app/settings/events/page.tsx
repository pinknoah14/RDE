"use client";
import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, X, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { EventItem } from "@/types";

type EditForm = {
  sku_id: string;
  event_name: string;
  event_type: string;
  start_date: string;
  end_date: string;
  memo: string;
};

const BLANK: EditForm = {
  sku_id: "",
  event_name: "",
  event_type: "EVENT",
  start_date: "",
  end_date: "",
  memo: "",
};

export default function EventsPage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<EditForm>(BLANK);
  const [saving, setSaving] = useState(false);

  const load = () =>
    api.getEvents().then(setEvents).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  const startNew = () => {
    setEditing("new");
    setForm(BLANK);
  };

  const startEdit = (e: EventItem) => {
    setEditing(e.event_id);
    setForm({
      sku_id: e.sku_id,
      event_name: e.event_name ?? "",
      event_type: e.event_type ?? "EVENT",
      start_date: e.start_date,
      end_date: e.end_date,
      memo: e.memo ?? "",
    });
  };

  const save = async () => {
    if (!form.sku_id || !form.start_date || !form.end_date) {
      toast({ title: "SKU·시작일·종료일은 필수입니다", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      const payload = {
        sku_id: form.sku_id,
        event_name: form.event_name || undefined,
        event_type: form.event_type || "EVENT",
        start_date: form.start_date,
        end_date: form.end_date,
        memo: form.memo || undefined,
      };
      if (editing === "new") {
        await api.createEvent(payload);
      } else if (typeof editing === "number") {
        await api.updateEvent(editing, payload);
      }
      toast({ title: "저장 완료" });
      setEditing(null);
      setForm(BLANK);
      load();
    } catch (e) {
      toast({ title: "오류", description: (e as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (e: EventItem) => {
    if (!confirm(`'${e.sku_id}' 이벤트를 삭제하시겠습니까?`)) return;
    try {
      await api.deleteEvent(e.event_id);
      toast({ title: "삭제 완료" });
      load();
    } catch (err) {
      toast({ title: "삭제 실패", description: (err as Error).message, variant: "destructive" });
    }
  };

  const inp = "w-full rounded border px-2 py-1 text-sm";

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">이벤트 관리</h1>
        <Button size="sm" className="gap-1" onClick={startNew} disabled={editing !== null}>
          <Plus size={14} />이벤트 추가
        </Button>
      </div>
      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-gray-100" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3 text-left">SKU코드</th>
                <th className="px-4 py-3 text-left">이벤트명</th>
                <th className="px-4 py-3 text-left">유형</th>
                <th className="px-4 py-3 text-left">시작일</th>
                <th className="px-4 py-3 text-left">종료일</th>
                <th className="px-4 py-3 text-left">비고</th>
                <th className="px-4 py-3 text-right">관리</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {editing === "new" && (
                <tr className="bg-purple-50">
                  <td className="px-2 py-2">
                    <input className={inp} placeholder="SKU000001"
                      value={form.sku_id} onChange={(e) => setForm({ ...form, sku_id: e.target.value })} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={inp} placeholder="이벤트명"
                      value={form.event_name} onChange={(e) => setForm({ ...form, event_name: e.target.value })} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={inp} placeholder="EVENT"
                      value={form.event_type} onChange={(e) => setForm({ ...form, event_type: e.target.value })} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={inp} type="date"
                      value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={inp} type="date"
                      value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={inp} placeholder="비고"
                      value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <button onClick={save} className="text-green-600" disabled={saving}>
                        <Check size={16} />
                      </button>
                      <button onClick={() => { setEditing(null); setForm(BLANK); }} className="text-muted-foreground">
                        <X size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              )}
              {events.map((e) =>
                editing === e.event_id ? (
                  <tr key={e.event_id} className="bg-purple-50">
                    <td className="px-2 py-2 font-mono">{e.sku_id}</td>
                    <td className="px-2 py-2">
                      <input className={inp} value={form.event_name}
                        onChange={(ev) => setForm({ ...form, event_name: ev.target.value })} />
                    </td>
                    <td className="px-2 py-2">
                      <input className={inp} value={form.event_type}
                        onChange={(ev) => setForm({ ...form, event_type: ev.target.value })} />
                    </td>
                    <td className="px-2 py-2">
                      <input className={inp} type="date" value={form.start_date}
                        onChange={(ev) => setForm({ ...form, start_date: ev.target.value })} />
                    </td>
                    <td className="px-2 py-2">
                      <input className={inp} type="date" value={form.end_date}
                        onChange={(ev) => setForm({ ...form, end_date: ev.target.value })} />
                    </td>
                    <td className="px-2 py-2">
                      <input className={inp} value={form.memo}
                        onChange={(ev) => setForm({ ...form, memo: ev.target.value })} />
                    </td>
                    <td className="px-2 py-2 text-right">
                      <div className="flex justify-end gap-1">
                        <button onClick={save} className="text-green-600" disabled={saving}>
                          <Check size={16} />
                        </button>
                        <button onClick={() => { setEditing(null); setForm(BLANK); }} className="text-muted-foreground">
                          <X size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={e.event_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono">{e.sku_id}</td>
                    <td className="px-4 py-3">{e.event_name ?? "-"}</td>
                    <td className="px-4 py-3 text-muted-foreground">{e.event_type}</td>
                    <td className="px-4 py-3 text-muted-foreground">{e.start_date}</td>
                    <td className="px-4 py-3 text-muted-foreground">{e.end_date}</td>
                    <td className="px-4 py-3 text-muted-foreground">{e.memo ?? "-"}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
                          onClick={() => startEdit(e)} disabled={editing !== null}>
                          <Pencil size={13} />
                        </Button>
                        <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-red-500 hover:text-red-600"
                          onClick={() => remove(e)} disabled={editing !== null}>
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {events.length === 0 && editing !== "new" && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                    등록된 이벤트가 없습니다
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
