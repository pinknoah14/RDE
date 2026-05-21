"use client";
import { useRef, useState } from "react";
import { Download, Upload, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import { formatDate } from "@/lib/utils";

const VERIFY_ITEMS = [
  "zone_config 테이블 존재 및 데이터 유효",
  "system_config 필수 키 존재",
  "replenish_candidates 무결성 확인",
  "wave 상태 정합성 확인",
];

export default function DataPage() {
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [lastExport, setLastExport] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await api.exportDb();
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const now = new Date();
      a.download = `replenish_backup_${now.toISOString().slice(0, 10)}.db`;
      a.click();
      URL.revokeObjectURL(url);
      setLastExport(now.toISOString());
      toast({ title: "DB 내보내기 완료" });
    } catch (e) {
      toast({ title: "내보내기 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setExporting(false); }
  };

  const handleImport = async (file: File) => {
    if (!confirm("현재 데이터가 대체됩니다. 계속하시겠습니까?")) return;
    if (!confirm("최종 확인: 이 작업은 되돌릴 수 없습니다. 진행하시겠습니까?")) return;
    setImporting(true);
    try {
      await api.importDb(file);
      toast({ title: "DB 가져오기 완료" });
    } catch (e) {
      toast({ title: "가져오기 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setImporting(false); }
  };

  return (
    <div className="p-6 max-w-lg">
      <h1 className="mb-6 text-xl font-bold">데이터 관리</h1>
      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-base">DB 내보내기</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-sm text-muted-foreground">현재 DB를 파일로 다운로드합니다. 마감 후 다음 관리자에게 전달하세요.</p>
            {lastExport && (
              <p className="mb-3 text-xs text-muted-foreground">
                마지막 내보내기: <strong>{formatDate(lastExport)}</strong>
              </p>
            )}
            <Button onClick={handleExport} disabled={exporting} className="gap-2">
              <Download size={14} />{exporting ? "내보내는 중..." : "DB 내보내기"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">DB 가져오기</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-sm text-muted-foreground">이전 관리자로부터 받은 DB 파일을 적용합니다.</p>
            <div className="mb-3 flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-700">
              <AlertTriangle size={14} />⚠️ 현재 데이터가 대체됩니다. 2단계 확인 후 적용됩니다.
            </div>

            {/* 자동 검증 체크리스트 */}
            <div className="mb-4 rounded-md border bg-gray-50 p-3">
              <p className="mb-2 text-xs font-medium text-muted-foreground">가져오기 시 자동 검증 항목</p>
              <ul className="space-y-1">
                {VERIFY_ITEMS.map((item) => (
                  <li key={item} className="flex items-center gap-2 text-xs text-muted-foreground">
                    <CheckCircle2 size={12} className="text-green-500 shrink-0" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            <input ref={fileRef} type="file" accept=".db" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleImport(f); e.target.value = ""; }} />
            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={() => fileRef.current?.click()}>파일 선택...</Button>
              <Button onClick={() => fileRef.current?.click()} disabled={importing} className="gap-2">
                <Upload size={14} />{importing ? "가져오는 중..." : "DB 가져오기"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
