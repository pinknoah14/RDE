"use client";
import { useState } from "react";
import { ArrowRight, Check, X, ArrowLeftRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { riskColor, riskLabel, proximityDot } from "@/lib/utils";
import type { Candidate } from "@/types";

interface Props {
  c: Candidate;
  onApprove: () => void;
  onReject: () => void;
  onModifyQty: (qty: number) => void;
  onMoveSection: () => void;
}

export function CandidateCard({ c, onApprove, onReject, onModifyQty, onMoveSection }: Props) {
  const [qty, setQty] = useState(c.modified_qty ?? c.recommended_qty);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  const displayQty = c.modified_qty ?? c.recommended_qty;
  const isPending = c.candidate_status === "PENDING" || c.candidate_status === "MODIFIED";

  let flags: string[] = [];
  try { flags = JSON.parse(c.reason_flags ?? "[]"); } catch {}

  const borderColor =
    c.risk_level === "CRITICAL" ? "border-l-red-500" :
    c.risk_level === "HIGH"     ? "border-l-orange-500" :
    c.risk_level === "MEDIUM"   ? "border-l-yellow-500" : "border-l-green-500";

  return (
    <Card className={`border-l-4 ${borderColor}`}>
      <CardContent className="p-4">
        {/* Header: SKU + risk badge */}
        <div className="mb-2 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <span className="block truncate font-medium text-sm">{c.sku_name}</span>
            <span className="text-xs text-muted-foreground">{c.sku_id}</span>
          </div>
          <Badge className={riskColor(c.risk_level)} variant="outline">
            {riskLabel(c.risk_level)} {c.risk_score}점
          </Badge>
        </div>

        {/* Bin route: picking → replenish with proximity */}
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono bg-gray-100 px-1.5 py-0.5 rounded">{c.picking_bin || "-"}</span>
          <ArrowRight size={12} />
          <span className="font-mono bg-blue-50 px-1.5 py-0.5 rounded text-blue-700">
            {c.replenish_bin ?? c.zone}
          </span>
          {c.proximity_score !== undefined && (
            <span title={`proximity_score: ${c.proximity_score}`}>
              {proximityDot(c.proximity_score)}
            </span>
          )}
        </div>

        {/* Sales stats */}
        <div className="mb-2 flex items-center gap-3 text-xs">
          <span className="text-muted-foreground">오늘 판매: <strong>{c.today_sales}</strong></span>
          <span className="text-muted-foreground">일평균: <strong>{c.avg_daily_sales.toFixed(1)}</strong></span>
          {c.eta_hours != null && (
            <span className="text-muted-foreground">소진: <strong>{c.eta_hours.toFixed(1)}h</strong></span>
          )}
        </div>

        {/* Reason flags */}
        {flags.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1">
            {flags.map((f) => (
              <span key={f} className="rounded-full bg-orange-100 px-2 py-0.5 text-xs text-orange-700">{f}</span>
            ))}
          </div>
        )}

        {/* Qty editor */}
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

        {/* Status badges */}
        {c.candidate_status === "APPROVED" && (
          <div className="mb-2 text-xs font-medium text-green-600">✅ 승인됨</div>
        )}
        {c.candidate_status === "REJECTED" && (
          <div className="mb-2 text-xs text-red-600">
            ❌ 거절됨 {c.rejected_reason ? `(${c.rejected_reason})` : ""}
          </div>
        )}

        {/* Reject form */}
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

        {/* Action buttons */}
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
