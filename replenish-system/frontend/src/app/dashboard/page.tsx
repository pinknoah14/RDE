"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Users, TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { DashboardSummary } from "@/types";

const RISK_CONFIG = [
  { level: "CRITICAL", label: "위급",  bg: "bg-red-50",    text: "text-red-700",    border: "border-red-200" },
  { level: "HIGH",     label: "높음",  bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200" },
  { level: "MEDIUM",   label: "보통",  bg: "bg-yellow-50", text: "text-yellow-700", border: "border-yellow-200" },
  { level: "LOW",      label: "낮음",  bg: "bg-green-50",  text: "text-green-700",  border: "border-green-200" },
];

export default function DashboardPage() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    api.getDashboard()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="p-6">
        <h1 className="mb-6 text-xl font-bold">대시보드</h1>
        <div className="grid gap-4 md:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-lg bg-gray-100" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <h1 className="mb-6 text-xl font-bold">대시보드</h1>
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertTriangle className="mb-1 inline-block" size={14} /> 대시보드 데이터를 불러올 수 없습니다: {error}
        </div>
      </div>
    );
  }

  const counts = data?.risk_counts ?? { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };

  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-bold">대시보드</h1>

      {/* 위험도 카드 */}
      <div className="mb-6 grid gap-3 md:grid-cols-4">
        {RISK_CONFIG.map(({ level, label, bg, text, border }) => (
          <button
            key={level}
            onClick={() => router.push(`/waves/new?min_risk=${level}`)}
            className={`rounded-lg border ${border} ${bg} p-4 text-left transition-shadow hover:shadow-md`}
          >
            <p className={`text-xs font-medium ${text}`}>{label}</p>
            <p className={`mt-1 text-3xl font-bold ${text}`}>
              {counts[level as keyof typeof counts]}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">SKU</p>
          </button>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {/* 긴급 SKU */}
        <Card className="md:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertTriangle size={14} className="text-red-500" />
              긴급 주의 SKU
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!data?.critical_skus?.length ? (
              <p className="text-sm text-muted-foreground">긴급 SKU 없음</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="pb-2 text-left">SKU명</th>
                    <th className="pb-2 text-left">위험도</th>
                    <th className="pb-2 text-left">소진</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.critical_skus.map((s) => (
                    <tr key={s.sku_id}>
                      <td className="py-2">
                        <span className="font-medium">{s.sku_name}</span>
                        <span className="ml-2 text-xs text-muted-foreground">{s.sku_id}</span>
                      </td>
                      <td className="py-2">
                        <Badge variant="critical">{s.risk_score}점</Badge>
                      </td>
                      <td className="py-2 text-muted-foreground">
                        {s.eta_hours !== undefined ? `${s.eta_hours.toFixed(1)}h` : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {/* 상태 패널 */}
        <div className="space-y-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Users size={14} />작업자 현황
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">
                {data?.active_workers ?? 0}
                <span className="text-base font-normal text-muted-foreground"> / {data?.total_workers ?? 0}명</span>
              </p>
              <p className="text-xs text-muted-foreground">현재 출근</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">알림</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {(data?.new_skus ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🆕 신규 입고 상품</span>
                  <Badge variant="secondary">{data!.new_skus}</Badge>
                </div>
              )}
              {(data?.stale_bins ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>⚠️ 지번 확인 필요</span>
                  <Badge variant="secondary">{data!.stale_bins}</Badge>
                </div>
              )}
              {(data?.unknown_zones?.length ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🔴 미등록 존</span>
                  <Badge variant="destructive">{data!.unknown_zones.length}</Badge>
                </div>
              )}
              {(data?.unclaimed_tasks ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🟡 미선점 경고</span>
                  <Badge variant="secondary">{data!.unclaimed_tasks}</Badge>
                </div>
              )}
              {(data?.multi_bin_skus ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🔵 다중 피킹지번</span>
                  <Badge variant="secondary">{data!.multi_bin_skus}</Badge>
                </div>
              )}
              {!data?.new_skus && !data?.stale_bins && !data?.unknown_zones?.length && !data?.unclaimed_tasks && !data?.multi_bin_skus && (
                <p className="text-muted-foreground text-xs">알림 없음</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
