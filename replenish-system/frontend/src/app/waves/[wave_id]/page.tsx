"use client";
import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, X, ArrowLeftRight, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import { riskColor, riskLabel, proximityDot } from "@/lib/utils";
import type { Candidate, Wave } from "@/types";

function CandidateCard({
  c,
  onApprove,
  onReject,
  onModifyQty,
  onMoveSection,
}: {
  c: Candidate;
  onApprove: () => void;
  onReject: () => void;
  onModifyQty: (qty: number) => void;
  onMoveSection: () => void;
}) {
  const [qty, setQty] = useState(c.modified_qty ?? c.recommended_qty);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const displayQty = c.modified_qty ?? c.recommended_qty;
  const isPending = c.candidate_status === "PENDING" || c.candidate_status === "MODIFIED";

  let flags: string[] = [];
  try { flags = JSON.parse(c.reason_flags ?? "[]"); } catch {}

  return (
    <Card className={`border-l-4 ${c.risk_level === "CRITICAL" ? "border-l-red-500" : c.risk_level === "HIGH" ? "border-l-orange-500" : c.risk_level === "MEDIUM" ? "border-l-yellow-500" : "border-l-green-500"}`}>
      <CardContent className="p-4">
        <div className="mb-2 flex items-start justify-between gap-2">
          <div>
            <span className="font-medium text-sm">{c.sku_name}</span>
            <span className="ml-2 text-xs text-muted-foreground">{c.sku_id}</span>
          </div>
          <Badge className={riskColor(c.risk_level)} variant="outline">
            {riskLabel(c.risk_level)} {c.risk_score}점
          </Badge>
        </div>

        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono bg-gray-100 px-1.5 py-0.5 rounded">{c.picking_bin}</span>
          <ArrowRight size={12} />
          <span className="font-mono bg-blue-50 px-1.5 py-0.5 rounded text-blue-700">보충존</span>
        </div>

        <div className="mb-2 flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">오늘 판매: <strong>{c.today_sales}</strong></span>
          <span className="text-muted-foreground">일평균: <strong>{c.avg_daily_sales.toFixed(1)}</strong></span>
          {c.eta_hours !== undefined && c.eta_hours !== null && (
            <span className="text-muted-foreground">소진: <strong>{c.eta_hours.toFixed(1)}h</strong></span>
          )}
        </div>

        {flags.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1">
            {flags.map((f) => (
              <span key={f} className="rounded-full bg-orange-100 px-2 py-0.5 text-xs text-orange-700">{f}</span>
            ))}
          </div>
        )}

        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs text-muted-foreground">보충 수량:</span>
          <input
            type="number" min={1} value={qty}
            onChange={(e) => setQty(+e.target.value)}
            className="w-20 rounded border px-2 py-1 text-sm text-center"
            disabled={!isPending}
          />
          {isPending && qty !== displayQty && (
            <Button size="sm" variant="outline" onClick={() => onModifyQty(qty)} className="h-7 text-xs">적용</Button>
          )}
        </div>

        {c.candidate_status === "APPROVED" && (
          <div className="mb-2 text-xs font-medium text-green-600">✅ 승인됨</div>
        )}
        {c.candidate_status === "REJECTED" && (
          <div className="mb-2 text-xs text-red-600">❌ 거절됨 {c.rejected_reason ? `(${c.rejected_reason})` : ""}</div>
        )}

        {rejecting && (
          <div className="mb-2 flex gap-2">
            <input
              type="text" placeholder="거절 사유 (선택)"
              value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
              className="flex-1 rounded border px-2 py-1 text-xs"
            />
            <Button size="sm" variant="destructive" className="h-7 text-xs"
              onClick={() => { onReject(); setRejecting(false); }}>확인</Button>
            <Button size="sm" variant="ghost" className="h-7 text-xs"
              onClick={() => setRejecting(false)}>취소</Button>
          </div>
        )}

        {isPending && !rejecting && (
          <div className="flex gap-2">
            <Button size="sm" className="h-7 flex-1 text-xs" onClick={onApprove}>
              <Check size={12} />승인
            </Button>
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onMoveSection}>
              <ArrowLeftRight size={12} />
              {c.list_section === "MAIN" ? "→ 서브" : "→ 메인"}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 text-xs text-red-600 hover:text-red-700"
              onClick={() => setRejecting(true)}>
              <X size={12} />거절
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function WaveReviewPage({ params }: { params: Promise<{ wave_id: string }> }) {
  const { wave_id } = use(params);
  const waveId = parseInt(wave_id);
  const router = useRouter();

  const [wave, setWave] = useState<Wave | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    Promise.all([api.getWave(waveId), api.getCandidates(waveId)])
      .then(([w, c]) => { setWave(w); setCandidates(c); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [waveId]);

  const refresh = () => api.getCandidates(waveId).then(setCandidates).catch(console.error);

  const handleApprove = async (cid: number) => {
    try { await api.approveCandidate(waveId, cid); await refresh(); toast({ title: "승인 완료" }); }
    catch (e) { toast({ title: "오류", description: (e as Error).message, variant: "destructive" }); }
  };

  const handleReject = async (cid: number) => {
    try { await api.rejectCandidate(waveId, cid); await refresh(); toast({ title: "거절 완료" }); }
    catch (e) { toast({ title: "오류", description: (e as Error).message, variant: "destructive" }); }
  };

  const handleModify = async (cid: number, qty: number) => {
    try { await api.modifyCandidate(waveId, cid, qty); await refresh(); toast({ title: "수량 수정 완료" }); }
    catch (e) { toast({ title: "오류", description: (e as Error).message, variant: "destructive" }); }
  };

  const handleConfirm = async () => {
    setConfirming(true);
    try {
      const res = await api.confirmWave(waveId);
      toast({ title: "웨이브 확정 완료", description: `태스크 ${res.tasks_created}건 생성` });
      router.push(`/waves/${waveId}/queue`);
    } catch (e) {
      toast({ title: "확정 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setConfirming(false); }
  };

  const main = candidates.filter((c) => c.list_section === "MAIN");
  const sub  = candidates.filter((c) => c.list_section === "SUB");
  const approvedCount = candidates.filter((c) => c.candidate_status === "APPROVED" || c.candidate_status === "MODIFIED").length;

  if (loading) {
    return <div className="flex h-40 items-center justify-center"><div className="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" /></div>;
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">{wave?.wave_name ?? `웨이브 #${waveId}`}</h1>
          <p className="text-sm text-muted-foreground">
            총 {candidates.length}건 | 승인됨 {approvedCount}건
          </p>
        </div>
        <Button onClick={handleConfirm} disabled={confirming || approvedCount === 0}>
          <Send size={14} />
          {confirming ? "처리 중..." : `웨이브 확정 (${approvedCount}건)`}
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <h2 className="mb-3 text-sm font-semibold text-muted-foreground">MAIN ({main.length})</h2>
          <div className="space-y-2">
            {main.map((c) => (
              <CandidateCard key={c.candidate_id} c={c}
                onApprove={() => handleApprove(c.candidate_id)}
                onReject={() => handleReject(c.candidate_id)}
                onModifyQty={(qty) => handleModify(c.candidate_id, qty)}
                onMoveSection={() => {}} />
            ))}
            {main.length === 0 && <p className="text-sm text-muted-foreground py-4 text-center">항목 없음</p>}
          </div>
        </div>
        <div>
          <h2 className="mb-3 text-sm font-semibold text-muted-foreground">SUB ({sub.length})</h2>
          <div className="space-y-2">
            {sub.map((c) => (
              <CandidateCard key={c.candidate_id} c={c}
                onApprove={() => handleApprove(c.candidate_id)}
                onReject={() => handleReject(c.candidate_id)}
                onModifyQty={(qty) => handleModify(c.candidate_id, qty)}
                onMoveSection={() => {}} />
            ))}
            {sub.length === 0 && <p className="text-sm text-muted-foreground py-4 text-center">항목 없음</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
