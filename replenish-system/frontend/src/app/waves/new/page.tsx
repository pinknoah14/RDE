"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";

export default function WaveNewPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({
    wave_type: "REGULAR",
    min_risk_score: 40,
    max_candidates: 40,
    target_days: 1.5,
    urgent_only: false,
  });

  const handleCreate = async () => {
    setLoading(true);
    try {
      const res = await api.createWave({
        wave_type: form.wave_type,
        min_risk_score: form.min_risk_score,
        max_candidates: form.max_candidates,
        target_days: form.target_days,
        urgent_only: form.urgent_only,
      });
      toast({
        title: "웨이브 생성 완료",
        description: `후보 ${res.algorithm.total_candidates}건 (위급: ${res.algorithm.critical}, 높음: ${res.algorithm.high})`,
      });
      router.push(`/waves/${res.wave_id}`);
    } catch (e) {
      toast({ title: "웨이브 생성 실패", description: (e as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-lg">
      <h1 className="mb-6 text-xl font-bold">웨이브 생성</h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">웨이브 옵션</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div>
            <label className="mb-2 block text-sm font-medium">웨이브 유형</label>
            <div className="flex gap-4 text-sm">
              {(["REGULAR", "URGENT"] as const).map((t) => (
                <label key={t} className="flex items-center gap-1.5 cursor-pointer">
                  <input type="radio" name="type" checked={form.wave_type === t}
                    onChange={() => setForm((p) => ({ ...p, wave_type: t }))} />
                  {t === "REGULAR" ? "정기" : "긴급"}
                </label>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium">최소 위험도 점수</label>
            <div className="flex items-center gap-3">
              <input type="range" min={0} max={100} value={form.min_risk_score}
                onChange={(e) => setForm((p) => ({ ...p, min_risk_score: +e.target.value }))}
                className="flex-1" />
              <input type="number" min={0} max={100} value={form.min_risk_score}
                onChange={(e) => setForm((p) => ({ ...p, min_risk_score: +e.target.value }))}
                className="w-16 rounded-md border px-2 py-1 text-sm text-center" />
            </div>
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium">목표 보유 일수</label>
            <input type="number" min={0.5} max={7} step={0.5} value={form.target_days}
              onChange={(e) => setForm((p) => ({ ...p, target_days: +e.target.value }))}
              className="w-28 rounded-md border px-3 py-1.5 text-sm" />
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium">최대 SKU 수</label>
            <input type="number" min={1} max={200} value={form.max_candidates}
              onChange={(e) => setForm((p) => ({ ...p, max_candidates: +e.target.value }))}
              className="w-28 rounded-md border px-3 py-1.5 text-sm" />
          </div>

          <div className="flex items-center gap-2">
            <input type="checkbox" id="urgent_only" checked={form.urgent_only}
              onChange={(e) => setForm((p) => ({ ...p, urgent_only: e.target.checked }))} />
            <label htmlFor="urgent_only" className="text-sm cursor-pointer">긴급 SKU만 포함</label>
          </div>

          <Button onClick={handleCreate} disabled={loading} className="w-full">
            {loading ? (
              <span className="flex items-center gap-2">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                생성 중...
              </span>
            ) : "웨이브 생성"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
