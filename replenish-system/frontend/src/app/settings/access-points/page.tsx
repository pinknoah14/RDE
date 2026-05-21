"use client";
import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { FloorAccessPoint, FloorAccessPointInput } from "@/types";

const EMPTY: FloorAccessPointInput = { name: "", x: 0, y: 0, access_type: "STAIRS", is_active: true };

export default function AccessPointsPage() {
  const [points, setPoints] = useState<FloorAccessPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<FloorAccessPointInput>(EMPTY);

  const load = () => api.getAccessPoints().then(setPoints).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  const startEdit = (p: FloorAccessPoint) => {
    setEditing(p.access_id);
    setForm({ name: p.name, x: p.x, y: p.y, access_type: p.access_type, is_active: p.is_active });
  };

  const save = async () => {
    try {
      if (editing === "new") {
        await api.createAccessPoint(form);
        toast({ title: "추가 완료" });
      } else {
        await api.updateAccessPoint(editing as number, form);
        toast({ title: "수정 완료" });
      }
      setEditing(null);
      setForm(EMPTY);
      load();
    } catch (e) {
      toast({ title: "저장 실패", description: (e as Error).message, variant: "destructive" });
    }
  };

  const remove = async (id: number) => {
    if (!confirm("삭제하시겠습니까?")) return;
    try {
      await api.deleteAccessPoint(id);
      toast({ title: "삭제 완료" });
      load();
    } catch (e) {
      toast({ title: "삭제 실패", description: (e as Error).message, variant: "destructive" });
    }
  };

  const FormRow = () => (
    <tr className="bg-blue-50">
      <td className="px-4 py-2">
        <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
          placeholder="이름" className="w-full rounded border px-2 py-1 text-sm" />
      </td>
      <td className="px-4 py-2">
        <select value={form.access_type} onChange={(e) => setForm((p) => ({ ...p, access_type: e.target.value as "STAIRS" | "LIFT" }))}
          className="rounded border px-2 py-1 text-sm">
          <option value="STAIRS">계단</option>
          <option value="LIFT">리프트</option>
        </select>
      </td>
      <td className="px-4 py-2">
        <input type="number" step={0.5} value={form.x} onChange={(e) => setForm((p) => ({ ...p, x: +e.target.value }))}
          className="w-20 rounded border px-2 py-1 text-sm text-right" />
      </td>
      <td className="px-4 py-2">
        <input type="number" step={0.5} value={form.y} onChange={(e) => setForm((p) => ({ ...p, y: +e.target.value }))}
          className="w-20 rounded border px-2 py-1 text-sm text-right" />
      </td>
      <td className="px-4 py-2 text-center">
        <input type="checkbox" checked={form.is_active}
          onChange={(e) => setForm((p) => ({ ...p, is_active: e.target.checked }))} />
      </td>
      <td className="px-4 py-2">
        <div className="flex gap-1">
          <button onClick={save} className="text-green-600 hover:text-green-700"><Check size={16} /></button>
          <button onClick={() => { setEditing(null); setForm(EMPTY); }} className="text-muted-foreground hover:text-red-600"><X size={16} /></button>
        </div>
      </td>
    </tr>
  );

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">계단 / 리프트 관리</h1>
          <p className="mt-1 text-sm text-muted-foreground">ℹ️ 미설정 시: 층 이동 패널티만 적용</p>
        </div>
        <Button size="sm" onClick={() => { setEditing("new"); setForm(EMPTY); }} disabled={editing !== null}>
          <Plus size={14} />추가
        </Button>
      </div>

      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-gray-100" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3 text-left">이름</th>
                <th className="px-4 py-3 text-left">유형</th>
                <th className="px-4 py-3 text-left">X (m)</th>
                <th className="px-4 py-3 text-left">Y (m)</th>
                <th className="px-4 py-3 text-center">활성</th>
                <th className="px-4 py-3 text-left">편집</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {editing === "new" && <FormRow />}
              {points.map((p) =>
                editing === p.access_id ? (
                  <FormRow key={p.access_id} />
                ) : (
                  <tr key={p.access_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium">{p.name}</td>
                    <td className="px-4 py-3">
                      <Badge variant="outline">{p.access_type === "STAIRS" ? "계단" : "리프트"}</Badge>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{p.x}</td>
                    <td className="px-4 py-3 text-muted-foreground">{p.y}</td>
                    <td className="px-4 py-3 text-center">{p.is_active ? "✅" : "❌"}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        <button onClick={() => startEdit(p)} className="text-muted-foreground hover:text-blue-600"><Pencil size={14} /></button>
                        <button onClick={() => remove(p.access_id)} className="text-muted-foreground hover:text-red-600"><Trash2 size={14} /></button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {points.length === 0 && editing !== "new" && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-muted-foreground text-sm">등록된 계단/리프트 없음</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
