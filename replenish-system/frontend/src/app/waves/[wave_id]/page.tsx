"use client";
import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import { CandidateCard } from "@/components/waves/CandidateCard";
import type { Candidate, Wave } from "@/types";

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
      .catch((e) => toast({ title: "로드 실패", description: (e as Error).message, variant: "destructive" }))
      .finally(() => setLoading(false));
  }, [waveId]);

  const refresh = () =>
    api.getCandidates(waveId).then(setCandidates)
      .catch((e) => toast({ title: "새로고침 실패", description: (e as Error).message, variant: "destructive" }));

  const withAction = async (fn: () => Promise<unknown>, successMsg: string) => {
    try {
      await fn();
      if (successMsg) toast({ title: successMsg });
      refresh().catch(() => {});
    } catch (e) { toast({ title: "오류", description: (e as Error).message, variant: "destructive" }); }
  };

  const handleApprove = (cid: number) => withAction(() => api.approveCandidate(waveId, cid), "승인 완료");
  const handleReject = (cid: number) => withAction(() => api.rejectCandidate(waveId, cid), "거절 완료");
  const handleModify = (cid: number, qty: number) => withAction(() => api.modifyCandidate(waveId, cid, qty), "수량 수정 완료");
  const handleMoveSection = (cid: number, current: "MAIN" | "SUB") => {
    const target = current === "MAIN" ? "SUB" : "MAIN";
    withAction(() => api.modifyCandidate(waveId, cid, undefined, target), "");
  };

  const handleConfirm = async () => {
    if (!confirm("웨이브를 확정하시겠습니까? 승인된 후보가 태스크로 생성됩니다.")) return;
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
  const approvedCount = candidates.filter(
    (c) => c.candidate_status === "APPROVED" || c.candidate_status === "MODIFIED"
  ).length;

  if (loading) {
    return (
      <div className="p-6">
        <div className="mb-4 h-8 w-48 animate-pulse rounded bg-gray-100" />
        <div className="grid grid-cols-2 gap-4">
          {[...Array(6)].map((_, i) => <div key={i} className="h-40 animate-pulse rounded-lg bg-gray-100" />)}
        </div>
      </div>
    );
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
                onMoveSection={() => handleMoveSection(c.candidate_id, c.list_section)} />
            ))}
            {main.length === 0 && (
              <p className="py-4 text-center text-sm text-muted-foreground">항목 없음</p>
            )}
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
                onMoveSection={() => handleMoveSection(c.candidate_id, c.list_section)} />
            ))}
            {sub.length === 0 && (
              <p className="py-4 text-center text-sm text-muted-foreground">항목 없음</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
