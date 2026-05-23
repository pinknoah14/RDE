"use client";
import { useEffect, useState } from "react";
import { Plus, Trash2, X, Check, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { PickingZone } from "@/types";

export default function PickingZonesPage() {
  const [zones, setZones] = useState<PickingZone[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [newBin, setNewBin] = useState("");
  const [newZone, setNewZone] = useState("");
  const [newMemo, setNewMemo] = useState("");
  const [saving, setSaving] = useState(false);

  const load = (q?: string) => {
    setLoading(true);
    api.getPickingZones(q).then(setZones).catch(console.error).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    load(query || undefined);
  };

  const add = async () => {
    if (!newBin || !newZone) {
      toast({ title: "지번·존은 필수입니다", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      await api.createPickingZone({ bin_id: newBin.trim(), zone: newZone.trim(), memo: newMemo || undefined });
      toast({ title: "등록 완료" });
      setAdding(false);
      setNewBin(""); setNewZone(""); setNewMemo("");
      load(query || undefined);
    } catch (e) {
      toast({ title: "등록 실패", description: (e as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (pz: PickingZone) => {
    try {
      await api.updatePickingZone(pz.bin_id, { is_active: !pz.is_active });
      load(query || undefined);
    } catch (e) {
      toast({ title: "오류", description: (e as Error).message, variant: "destructive" });
    }
  };

  const remove = async (pz: PickingZone) => {
    if (!confirm(`'${pz.bin_id}'을(를) 삭제하시겠습니까?`)) return;
    try {
      await api.deletePickingZone(pz.bin_id);
      toast({ title: "삭제 완료" });
      load(query || undefined);
    } catch (e) {
      toast({ title: "삭제 실패", description: (e as Error).message, variant: "destructive" });
    }
  };

  const inp = "rounded border px-2 py-1 text-sm";

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between gap-4">
        <h1 className="text-xl font-bold">피킹지번 관리</h1>
        <div className="flex items-center gap-2">
          <form onSubmit={search} className="flex items-center gap-1">
            <input className={`${inp} w-48`} placeholder="지번 검색 (예: 15RA)"
              value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button type="submit" size="sm" variant="outline" className="gap-1">
              <Search size={14} />검색
            </Button>
          </form>
          <Button size="sm" className="gap-1" onClick={() => setAdding(true)} disabled={adding}>
            <Plus size={14} />추가
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="h-40 animate-pulse rounded-lg bg-gray-100" />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3 text-left">지번</th>
                <th className="px-4 py-3 text-left">존</th>
                <th className="px-4 py-3 text-center">활성</th>
                <th className="px-4 py-3 text-left">비고</th>
                <th className="px-4 py-3 text-right">관리</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {adding && (
                <tr className="bg-purple-50">
                  <td className="px-2 py-2">
                    <input className={`${inp} w-full`} placeholder="15RA0010101"
                      value={newBin} onChange={(e) => setNewBin(e.target.value)} />
                  </td>
                  <td className="px-2 py-2">
                    <input className={`${inp} w-full`} placeholder="RA"
                      value={newZone} onChange={(e) => setNewZone(e.target.value)} />
                  </td>
                  <td className="px-2 py-2 text-center text-muted-foreground">—</td>
                  <td className="px-2 py-2">
                    <input className={`${inp} w-full`} placeholder="비고"
                      value={newMemo} onChange={(e) => setNewMemo(e.target.value)} />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <button onClick={add} className="text-green-600" disabled={saving}>
                        <Check size={16} />
                      </button>
                      <button onClick={() => { setAdding(false); setNewBin(""); setNewZone(""); setNewMemo(""); }} className="text-muted-foreground">
                        <X size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              )}
              {zones.map((z) => (
                <tr key={z.bin_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono font-medium">{z.bin_id}</td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{z.zone}</Badge>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <input type="checkbox" checked={z.is_active}
                      onChange={() => toggleActive(z)} className="cursor-pointer" />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{z.memo ?? "-"}</td>
                  <td className="px-4 py-3 text-right">
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-red-500 hover:text-red-600"
                      onClick={() => remove(z)}>
                      <Trash2 size={13} />
                    </Button>
                  </td>
                </tr>
              ))}
              {zones.length === 0 && !adding && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-sm text-muted-foreground">
                    {query ? "검색 결과 없음" : "등록된 지번이 없습니다"}
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
