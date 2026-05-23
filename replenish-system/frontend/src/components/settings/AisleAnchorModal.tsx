"use client";
import { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { AisleAnchor } from "@/types";

export function AisleAnchorModal({ zoneCode, open, onClose }: { zoneCode: string; open: boolean; onClose: () => void }) {
  const [rows, setRows] = useState<AisleAnchor[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !zoneCode) return;
    setLoading(true);
    api.getAisleAnchors(zoneCode)
      .then((d) => setRows(d.length ? d : [{ aisle_no: 1, anchor_x: 0, anchor_y: 0, floor: 0 }]))
      .catch(() => setRows([{ aisle_no: 1, anchor_x: 0, anchor_y: 0, floor: 0 }]))
      .finally(() => setLoading(false));
  }, [open, zoneCode]);

  const addRow = () => {
    const next = rows.length ? Math.max(...rows.map((r) => r.aisle_no)) + 1 : 1;
    setRows((p) => [...p, { aisle_no: next, anchor_x: 0, anchor_y: 0, floor: 0 }]);
  };

  const removeRow = (i: number) => setRows((p) => p.filter((_, idx) => idx !== i));

  const update = (i: number, field: keyof AisleAnchor, val: number) =>
    setRows((p) => p.map((r, idx) => idx === i ? { ...r, [field]: val } : r));

  const save = async () => {
    setSaving(true);
    try {
      await api.putAisleAnchors(zoneCode, rows);
      toast({ title: `${zoneCode} 통로 앵커 저장 완료` });
      onClose();
    } catch (e) {
      toast({ title: "저장 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{zoneCode} 존 — 통로별 위치 설정 (산재)</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-8"><div className="h-5 w-5 animate-spin rounded-full border-2 border-t-transparent" style={{ borderColor: "#5F0080", borderTopColor: "transparent" }} /></div>
        ) : (
          <div className="space-y-4">
            <div className="overflow-hidden rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-left">통로</th>
                    <th className="px-3 py-2 text-left">층</th>
                    <th className="px-3 py-2 text-left">X (m)</th>
                    <th className="px-3 py-2 text-left">Y (m)</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows.map((row, i) => (
                    <tr key={i}>
                      <td className="px-3 py-2">
                        <input type="number" value={row.aisle_no} min={1}
                          onChange={(e) => update(i, "aisle_no", +e.target.value)}
                          className="w-14 rounded border px-2 py-1 text-center text-sm" />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex gap-2 text-xs">
                          {[{ label: "1층", val: 0 }, { label: "메자닌", val: 1 }].map(({ label, val }) => (
                            <label key={val} className="flex items-center gap-1 cursor-pointer">
                              <input type="radio" checked={row.floor === val}
                                onChange={() => update(i, "floor", val)} />
                              {label}
                            </label>
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <input type="number" step={0.5} value={row.anchor_x}
                          onChange={(e) => update(i, "anchor_x", +e.target.value)}
                          className="w-20 rounded border px-2 py-1 text-right text-sm" />
                      </td>
                      <td className="px-3 py-2">
                        <input type="number" step={0.5} value={row.anchor_y}
                          onChange={(e) => update(i, "anchor_y", +e.target.value)}
                          className="w-20 rounded border px-2 py-1 text-right text-sm" />
                      </td>
                      <td className="px-3 py-2">
                        <button onClick={() => removeRow(i)} className="text-muted-foreground hover:text-red-600">
                          <X size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Button variant="outline" size="sm" onClick={addRow} className="gap-1">
              <Plus size={12} />통로 추가
            </Button>

            <p className="rounded-md bg-purple-50 p-2 text-xs text-[#5F0080]">
              ℹ️ 미설정 통로: 존 코드 비교 폴백 적용
            </p>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={onClose}>취소</Button>
              <Button onClick={save} disabled={saving}>{saving ? "저장 중..." : "저장"}</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
