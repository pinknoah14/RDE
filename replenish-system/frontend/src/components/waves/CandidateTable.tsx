"use client";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { riskColor, riskLabel, proximityDot, statusLabel } from "@/lib/utils";
import type { Candidate } from "@/types";

interface Props {
  candidates: Candidate[];
  onApprove?: (id: number) => void;
  onReject?: (id: number) => void;
}

export function CandidateTable({ candidates, onApprove, onReject }: Props) {
  if (candidates.length === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">후보 없음</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-xs font-medium text-muted-foreground">
          <tr>
            <th className="px-4 py-3">SKU</th>
            <th className="px-4 py-3">위험도</th>
            <th className="px-4 py-3">피킹 → 보충</th>
            <th className="px-4 py-3 text-right">수량</th>
            <th className="px-4 py-3">상태</th>
            {(onApprove || onReject) && <th className="px-4 py-3">액션</th>}
          </tr>
        </thead>
        <tbody className="divide-y">
          {candidates.map((c) => (
            <tr key={c.candidate_id} className="hover:bg-gray-50">
              <td className="max-w-[180px] px-4 py-3">
                <div className="truncate font-medium">{c.sku_name}</div>
                <div className="text-xs text-muted-foreground">{c.sku_id}</div>
              </td>
              <td className="px-4 py-3">
                <Badge className={riskColor(c.risk_level)} variant="outline">
                  {riskLabel(c.risk_level)} {c.risk_score}
                </Badge>
              </td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-1 text-xs">
                  <span className="font-mono bg-gray-100 px-1 py-0.5 rounded">{c.picking_bin || "-"}</span>
                  <ArrowRight size={10} />
                  <span className="font-mono bg-purple-50 px-1 py-0.5 rounded text-[#5F0080]">
                    {c.replenish_bin ?? c.zone}
                  </span>
                  {c.proximity_score !== undefined && (
                    <span>{proximityDot(c.proximity_score)}</span>
                  )}
                </div>
              </td>
              <td className="px-4 py-3 text-right">
                {c.modified_qty ?? c.recommended_qty}
              </td>
              <td className="px-4 py-3">
                <span className="text-xs text-muted-foreground">{statusLabel(c.candidate_status)}</span>
              </td>
              {(onApprove || onReject) && (
                <td className="px-4 py-3">
                  <div className="flex gap-1">
                    {onApprove && c.candidate_status === "PENDING" && (
                      <button onClick={() => onApprove(c.candidate_id)}
                        className="rounded bg-purple-50 px-2 py-1 text-xs text-[#5F0080] hover:bg-purple-100">승인</button>
                    )}
                    {onReject && c.candidate_status === "PENDING" && (
                      <button onClick={() => onReject(c.candidate_id)}
                        className="rounded bg-red-50 px-2 py-1 text-xs text-red-700 hover:bg-red-100">거절</button>
                    )}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
