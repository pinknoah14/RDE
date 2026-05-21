"use client";
import { useRef, useState } from "react";
import { Download, Upload, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";

export default function DataPage() {
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await api.exportDb();
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `replenish_backup_${new Date().toISOString().slice(0, 10)}.db`;
      a.click();
      URL.revokeObjectURL(url);
      toast({ title: "DB 내보내기 완료" });
    } catch (e) {
      toast({ title: "내보내기 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setExporting(false); }
  };

  const handleImport = async (file: File) => {
    if (!confirm("현재 데이터가 대체됩니다. 계속하시겠습니까?")) return;
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
            <p className="mb-4 text-sm text-muted-foreground">현재 DB를 파일로 다운로드합니다. 마감 후 다음 관리자에게 전달하세요.</p>
            <Button onClick={handleExport} disabled={exporting} className="gap-2">
              <Download size={14} />{exporting ? "내보내는 중..." : "DB 내보내기"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">DB 가져오기</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-sm text-muted-foreground">이전 관리자로부터 받은 DB 파일을 적용합니다.</p>
            <div className="mb-4 flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-700">
              <AlertTriangle size={14} />⚠️ 현재 데이터가 대체됩니다.
            </div>
            <input ref={fileRef} type="file" accept=".db" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleImport(f); e.target.value = ""; }} />
            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={() => fileRef.current?.click()}>파일 선택...</Button>
              <Button onClick={() => fileRef.current?.click()} disabled={importing}>
                <Upload size={14} />{importing ? "가져오는 중..." : "DB 가져오기"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
