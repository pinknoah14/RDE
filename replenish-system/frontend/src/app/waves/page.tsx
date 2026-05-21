"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { formatDate, statusLabel } from "@/lib/utils";
import type { Wave } from "@/types";

export default function WavesPage() {
  const [waves, setWaves] = useState<Wave[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getWaves().then(setWaves).catch(console.error).finally(() => setLoading(false));
  }, []);

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">웨이브 이력</h1>
        <Link href="/waves/new"><Button size="sm"><Plus size={14} />웨이브 생성</Button></Link>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-md bg-gray-100" />
          ))}
        </div>
      ) : waves.length === 0 ? (
        <p className="text-muted-foreground">웨이브 이력이 없습니다.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3">웨이브명</th>
                <th className="px-4 py-3">유형</th>
                <th className="px-4 py-3">상태</th>
                <th className="px-4 py-3">목표 SKU</th>
                <th className="px-4 py-3">생성일시</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {waves.map((w) => (
                <tr key={w.wave_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium">{w.wave_name}</td>
                  <td className="px-4 py-3">
                    <Badge variant={w.wave_type === "URGENT" ? "destructive" : "secondary"}>
                      {w.wave_type === "URGENT" ? "긴급" : "정기"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={w.wave_status === "SENT" || w.wave_status === "COMPLETED" ? "default" : "outline"}>
                      {statusLabel(w.wave_status)}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{w.target_sku_count}</td>
                  <td className="px-4 py-3 text-muted-foreground">{formatDate(w.created_at)}</td>
                  <td className="px-4 py-3">
                    <Link href={`/waves/${w.wave_id}`} className="text-blue-600 hover:underline text-xs">
                      {w.wave_status === "DRAFT" ? "검수하기" : "보기"}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
