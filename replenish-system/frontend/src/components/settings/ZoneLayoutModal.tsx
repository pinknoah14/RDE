"use client";
import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { ZoneLayout } from "@/types";

export function ZoneLayoutModal({ zoneCode, open, onClose }: { zoneCode: string; open: boolean; onClose: () => void }) {
  const [form, setForm] = useState<ZoneLayout>({
    floor: 0, is_scattered: false, origin_x: 0, origin_y: 0,
    aisle_direction: "y", aisle_gap: 3.0, bay_gap: 1.5,
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !zoneCode) return;
    setLoading(true);
    api.getZoneLayout(zoneCode)
      .then((d) => setForm({ ...d, origin_x: d.origin_x ?? 0, origin_y: d.origin_y ?? 0 }))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, zoneCode]);

  const save = async () => {
    setSaving(true);
    try {
      await api.putZoneLayout(zoneCode, form);
      toast({ title: `${zoneCode} 존 위치 설정 저장 완료` });
      onClose();
    } catch (e) {
      toast({ title: "저장 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setSaving(false); }
  };

  const field = (label: string, node: React.ReactNode, hint?: string) => (
    <div className="flex items-center justify-between gap-4">
      <div>
        <span className="text-sm font-medium">{label}</span>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {node}
    </div>
  );

  const numInput = (val: number | null | undefined, onChange: (v: number) => void, step = 0.5) => (
    <input type="number" step={step} value={val ?? 0}
      onChange={(e) => onChange(+e.target.value)}
      className="w-24 rounded border px-2 py-1 text-sm text-right" />
  );

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{zoneCode} 존 — 위치 설정 (연속)</DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-8"><div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" /></div>
        ) : (
          <div className="space-y-4">
            {field("층",
              <div className="flex gap-3 text-sm">
                {[{ label: "1층", val: 0 }, { label: "메자닌", val: 1 }].map(({ label, val }) => (
                  <label key={val} className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" checked={form.floor === val} onChange={() => setForm((p) => ({ ...p, floor: val }))} />
                    {label}
                  </label>
                ))}
              </div>
            )}

            {field("원점 X (m)", numInput(form.origin_x, (v) => setForm((p) => ({ ...p, origin_x: v }))))}
            {field("원점 Y (m)", numInput(form.origin_y, (v) => setForm((p) => ({ ...p, origin_y: v }))))}

            {field("통로 방향",
              <div className="flex gap-3 text-sm">
                {[{ label: "세로(Y)", val: "y" }, { label: "가로(X)", val: "x" }].map(({ label, val }) => (
                  <label key={val} className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" checked={form.aisle_direction === val} onChange={() => setForm((p) => ({ ...p, aisle_direction: val as "x" | "y" }))} />
                    {label}
                  </label>
                ))}
              </div>
            )}

            {field("통로 간격 (m)", numInput(form.aisle_gap, (v) => setForm((p) => ({ ...p, aisle_gap: v }))))}
            {field("베이 간격 (m)", numInput(form.bay_gap, (v) => setForm((p) => ({ ...p, bay_gap: v }))))}

            <p className="rounded-md bg-blue-50 p-2 text-xs text-blue-700">
              ℹ️ 미설정 시 존 코드 비교 폴백 동작 | 적용 범위: 다음 웨이브부터
            </p>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={onClose}>취소</Button>
              <Button onClick={save} disabled={saving}>{saving ? "저장 중..." : "저장"}</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
