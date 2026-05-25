"use client";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AccessPointTable } from "@/components/settings/AccessPointTable";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { FloorAccessPoint, FloorAccessPointInput } from "@/types";

const EMPTY: FloorAccessPointInput = { name: "", x: 0, y: 0, access_type: "STAIRS", is_active: true };

export default function AccessPointsPage() {
  const [points, setPoints] = useState<FloorAccessPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<FloorAccessPointInput>(EMPTY);

  const load = () => api.getAccessPoints()
    .then(setPoints)
    .catch((e) => toast({ title: "접근점 로드 실패", description: (e as Error).message, variant: "destructive" }))
    .finally(() => setLoading(false));
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
        <AccessPointTable
          points={points}
          editing={editing}
          form={form}
          onChange={(patch) => setForm((p) => ({ ...p, ...patch }))}
          onSave={save}
          onCancel={() => { setEditing(null); setForm(EMPTY); }}
          onEdit={startEdit}
          onDelete={remove}
        />
      )}
    </div>
  );
}
