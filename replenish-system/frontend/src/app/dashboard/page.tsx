"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, RefreshCw, Users, Zap } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import type { DashboardSummary } from "@/types";

const RISK_CONFIG = [
  { level: "CRITICAL", label: "위급",  bg: "bg-red-50",    text: "text-red-700",    border: "border-red-200" },
  { level: "HIGH",     label: "높음",  bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200" },
  { level: "MEDIUM",   label: "보통",  bg: "bg-yellow-50", text: "text-yellow-700", border: "border-yellow-200" },
  { level: "LOW",      label: "낮음",  bg: "bg-green-50",  text: "text-green-700",  border: "border-green-200" },
];

const POLL_INTERVAL_MS = 5 * 60 * 1000;

export default function DashboardPage() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [urgentLoading, setUrgentLoading] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const router = useRouter();

  const loadDashboard = useCallback(async () => {
    try {
      const res = await api.getDashboard();
      setData(res);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDashboard();
    intervalRef.current = setInterval(loadDashboard, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [loadDashboard]);

  const counts = data?.risk_counts ?? { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
  const hasAlerts = data
    ? (data.new_skus ?? 0) + (data.stale_bins ?? 0) + (data.unknown_zones?.length ?? 0) + (data.unclaimed_tasks ?? 0) + (data.multi_bin_skus ?? 0) > 0
    : false;

  const handleUrgentWave = async () => {
    const total = counts.CRITICAL;
    if (total === 0) return;
    if (!confirm(`CRITICAL ${total}개로 긴급 웨이브를 즉시 생성합니다.`)) return;
    setUrgentLoading(true);
    try {
      const res = await api.createUrgentWaveFromDashboard({
        auto_confirm: true,
        min_risk_level: "CRITICAL",
      });
      toast({
        title: `⚡ 긴급 웨이브 생성됨`,
        description: `${res.candidates}개 후보 → ${res.wave_name}`,
      });
      router.push(`/waves/${res.wave_id}`);
    } catch (e) {
      toast({
        title: "긴급 웨이브 생성 실패",
        description: (e as Error).message,
        variant: "destructive",
      });
    } finally {
      setUrgentLoading(false);
    }
  };

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

  if (error && !data) {
    return (
      <div className="p-6">
        <h1 className="mb-6 text-xl font-bold">대시보드</h1>
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertTriangle className="mb-1 inline-block" size={14} /> 대시보드 데이터를 불러올 수 없습니다: {error}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">대시보드</h1>
        <p className="text-xs text-muted-foreground">
          마지막 갱신: {lastUpdated?.toLocaleTimeString("ko-KR") ?? "-"}
          <button
            onClick={loadDashboard}
            className="ml-2 inline-flex items-center gap-1 underline hover:text-foreground"
          >
            <RefreshCw size={11} /> 지금 갱신
          </button>
        </p>
      </div>

      {/* 위험도 카드 */}
      <div className="mb-3 grid gap-3 md:grid-cols-4">
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

      {/* 긴급 웨이브 즉시 생성 */}
      <div className="mb-6 flex justify-end">
        <Button
          size="sm"
          variant="destructive"
          onClick={handleUrgentWave}
          disabled={counts.CRITICAL === 0 || urgentLoading}
          className="gap-1"
        >
          <Zap size={14} />
          {urgentLoading ? "생성 중..." : `긴급 웨이브 즉시 생성 (CRITICAL ${counts.CRITICAL})`}
        </Button>
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
                  <Badge variant="secondary">{data?.new_skus}</Badge>
                </div>
              )}
              {(data?.stale_bins ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>⚠️ 지번 확인 필요</span>
                  <Badge variant="secondary">{data?.stale_bins}</Badge>
                </div>
              )}
              {(data?.unknown_zones?.length ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🔴 미등록 존</span>
                  <Badge variant="destructive">{data?.unknown_zones?.length}</Badge>
                </div>
              )}
              {(data?.unclaimed_tasks ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🟡 미선점 경고</span>
                  <Badge variant="secondary">{data?.unclaimed_tasks}</Badge>
                </div>
              )}
              {(data?.multi_bin_skus ?? 0) > 0 && (
                <div className="flex items-center justify-between">
                  <span>🔵 다중 피킹지번</span>
                  <Badge variant="secondary">{data?.multi_bin_skus}</Badge>
                </div>
              )}
              {!hasAlerts && (
                <p className="text-muted-foreground text-xs">알림 없음</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
