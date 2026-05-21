"use client";
import { useEffect, useState } from "react";
import { Plus, Pencil, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { Worker, WorkerInput } from "@/types";

const EMPTY: WorkerInput = { worker_name: "", worker_type: "FORKLIFT", is_active: true, is_sub_worker: false, max_tasks: 3 };

export default function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<WorkerInput>(EMPTY);

  const load = () => api.getWorkers().then(setWorkers).catch(console.error).finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      if (editing === "new") await api.createWorker(form);
      else await api.updateWorker(editing as number, form);
      toast({ title: "저장 완료" });
      setEditing(null); setForm(EMPTY); load();
    } catch (e) { toast({ title: "오류", description: (e as Error).message, variant: "destructive" }); }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">작업자 관리</h1>
        <Button size="sm" onClick={() => { setEditing("new"); setForm(EMPTY); }} disabled={editing !== null}>
          <Plus size={14} />추가
        </Button>
      </div>
      {loading ? <div className="h-40 animate-pulse rounded-lg bg-gray-100" /> : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3 text-left">이름</th>
                <th className="px-4 py-3 text-left">유형</th>
                <th className="px-4 py-3 text-left">최대 태스크</th>
                <th className="px-4 py-3 text-center">서브</th>
                <th className="px-4 py-3 text-center">활성</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {editing === "new" && (
                <tr className="bg-blue-50">
                  <td className="px-4 py-2"><input value={form.worker_name} onChange={(e) => setForm((p) => ({ ...p, worker_name: e.target.value }))} placeholder="이름" className="w-full rounded border px-2 py-1 text-sm" /></td>
                  <td className="px-4 py-2"><select value={form.worker_type} onChange={(e) => setForm((p) => ({ ...p, worker_type: e.target.value as "FORKLIFT" | "WALKING" }))} className="rounded border px-2 py-1 text-sm"><option value="FORKLIFT">지게차</option><option value="WALKING">도보</option></select></td>
                  <td className="px-4 py-2"><input type="number" min={1} value={form.max_tasks} onChange={(e) => setForm((p) => ({ ...p, max_tasks: +e.target.value }))} className="w-16 rounded border px-2 py-1 text-sm" /></td>
                  <td className="px-4 py-2 text-center"><input type="checkbox" checked={form.is_sub_worker} onChange={(e) => setForm((p) => ({ ...p, is_sub_worker: e.target.checked }))} /></td>
                  <td className="px-4 py-2 text-center"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm((p) => ({ ...p, is_active: e.target.checked }))} /></td>
                  <td className="px-4 py-2"><div className="flex gap-1"><button onClick={save} className="text-green-600"><Check size={16} /></button><button onClick={() => { setEditing(null); setForm(EMPTY); }} className="text-muted-foreground"><X size={16} /></button></div></td>
                </tr>
              )}
              {workers.map((w) => (
                <tr key={w.worker_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium">{w.worker_name}</td>
                  <td className="px-4 py-3"><Badge variant="outline">{w.worker_type === "FORKLIFT" ? "지게차" : "도보"}</Badge></td>
                  <td className="px-4 py-3 text-muted-foreground">{w.max_tasks}</td>
                  <td className="px-4 py-3 text-center">{w.is_sub_worker ? "✅" : "—"}</td>
                  <td className="px-4 py-3 text-center">{w.is_active ? "✅" : "❌"}</td>
                  <td className="px-4 py-3"><button onClick={() => { setEditing(w.worker_id); setForm({ worker_name: w.worker_name, worker_type: w.worker_type, is_active: w.is_active, is_sub_worker: w.is_sub_worker, max_tasks: w.max_tasks }); }} className="text-muted-foreground hover:text-blue-600"><Pencil size={14} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
