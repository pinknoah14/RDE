"use client";
import { useState, useRef } from "react";
import { Upload, CheckCircle, AlertTriangle, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { UploadResult } from "@/types";
import { cn } from "@/lib/utils";

interface UploadZoneProps {
  label: string;
  accept?: string;
  onUpload: (file: File) => Promise<UploadResult>;
}

function UploadZone({ label, accept = ".csv", onUpload }: UploadZoneProps) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handle = async (file: File) => {
    setLoading(true);
    setError(null);
    setFileName(file.name);
    try {
      const res = await onUpload(file);
      setResult(res);
      toast({ title: `${label} 업로드 완료` });
    } catch (e) {
      setError((e as Error).message);
      toast({ title: "업로드 실패", description: (e as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div
          className={cn(
            "flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors cursor-pointer",
            dragging ? "border-blue-400 bg-blue-50" : "border-gray-300 hover:border-gray-400"
          )}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) handle(f); }}
          onClick={() => inputRef.current?.click()}
        >
          <input ref={inputRef} type="file" accept={accept} className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handle(f); e.target.value = ""; }} />
          {loading ? (
            <div className="flex flex-col items-center gap-2">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
              <span className="text-sm text-muted-foreground">업로드 중...</span>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <Upload className="h-8 w-8 text-gray-400" />
              <span className="text-sm text-muted-foreground">파일을 드래그하거나 클릭하여 선택</span>
              <span className="text-xs text-gray-400">CSV 파일 (UTF-8 / CP949)</span>
            </div>
          )}
        </div>

        {fileName && !loading && (
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <FileText size={14} />
            <span>{fileName}</span>
          </div>
        )}

        {result && !loading && (
          <div className="mt-3 space-y-1.5 rounded-md bg-green-50 p-3 text-sm">
            <div className="flex items-center gap-2 font-medium text-green-700">
              <CheckCircle size={14} />
              업로드 완료
            </div>
            {result.record_count !== undefined && (
              <p className="text-green-700">
                총 {result.record_count}행
                {result.picking_count !== undefined &&
                  ` — 피킹존 ${result.picking_count} / 보충존 ${result.replenish_count ?? 0} / 제외 ${result.hold_count ?? 0}`}
              </p>
            )}
            {result.sku_count !== undefined && (
              <p className="text-green-700">SKU 판매요약 갱신: {result.sku_count}개</p>
            )}
            {result.unknown_zones && result.unknown_zones.length > 0 && (
              <p className="flex items-center gap-1 text-amber-600">
                <AlertTriangle size={12} />
                미등록 존 감지: {result.unknown_zones.join(", ")}
              </p>
            )}
            {result.multi_bin_skus !== undefined && result.multi_bin_skus > 0 && (
              <p className="text-blue-600">🔵 다중 피킹지번 감지: {result.multi_bin_skus} SKU</p>
            )}
          </div>
        )}

        {error && !loading && (
          <div className="mt-3 flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-700">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function UploadPage() {
  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-bold">파일 업로드</h1>
      <div className="grid gap-4 md:grid-cols-3">
        <UploadZone label="재고현황 CSV" onUpload={(f) => api.uploadInventory(f)} />
        <UploadZone label="출고현황 CSV" onUpload={(f) => api.uploadOutbound(f)} />
        <UploadZone label="피벗테이블 CSV (판매)" onUpload={(f) => api.uploadPivot(f)} />
      </div>
    </div>
  );
}
