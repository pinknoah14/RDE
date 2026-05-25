"use client";
import { useEffect, useState, use } from "react";
import { Send, Trash2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import { statusLabel } from "@/lib/utils";
import type { QueueItem, ZoneConfig } from "@/types";

export default function QueuePage({ params }: { params: Promise<{ wave_id: string }> }) {
  const { wave_id } = use(params);
  const waveId = parseInt(wave_id);

  const [tasks, setTasks] = useState<QueueItem[]>([]);
  const [zones, setZones] = useState<ZoneConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [deleted, setDeleted] = useState(false);

  const load = () => api.getWaveTasks(waveId)
    .then(setTasks)
    .catch((e) => toast({ title: "큐 로드 실패", description: (e as Error).message, variant: "destructive" }))
    .finally(() => setLoading(false));

  useEffect(() => {
    Promise.all([
      api.getWaveTasks(waveId),
      api.getZones().catch(() => [] as ZoneConfig[]),
    ]).then(([t, z]) => { setTasks(t); setZones(z); }).finally(() => setLoading(false));
  }, [waveId]);

  const handleSend = async (channel?: string) => {
    setSending(true);
    try {
      await api.sendWave(waveId, channel);
      toast({ title: "Slack 전송 완료" });
      setDeleted(false);
      load();
    } catch (e) {
      toast({ title: "전송 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setSending(false); }
  };

  const handleDelete = async () => {
    if (!confirm("Slack 메시지를 삭제하시겠습니까?")) return;
    try {
      await api.deleteWaveMessages(waveId);
      toast({ title: "메시지 삭제 완료" });
      setDeleted(true);
      load();
    } catch (e) {
      toast({ title: "삭제 실패", description: (e as Error).message, variant: "destructive" });
    }
  };

  const hasSent    = tasks.some((t) => t.task_status === "SENT" || t.task_status === "DONE");
  const hasWaiting = tasks.some((t) => t.task_status === "READY" || t.task_status === "QUEUED");

  const channels = Array.from(new Set(zones.map((z) => z.slack_channel).filter(Boolean)));

  if (loading) {
    return <div className="flex h-40 items-center justify-center"><div className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent" style={{ borderColor: "#5F0080", borderTopColor: "transparent" }} /></div>;
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">대기열 / Slack 전송</h1>
        <div className="flex items-center gap-2">
          {deleted && (
            <>
              <select
                value={selectedChannel}
                onChange={(e) => setSelectedChannel(e.target.value)}
                className="rounded border px-2 py-1.5 text-sm"
              >
                <option value="">채널 선택 (기본)</option>
                {channels.map((ch) => (
                  <option key={ch} value={ch}>{ch}</option>
                ))}
              </select>
              <Button size="sm" onClick={() => handleSend(selectedChannel || undefined)} disabled={sending}>
                <RefreshCw size={14} />{sending ? "재전송 중..." : "재전송"}
              </Button>
            </>
          )}
          {hasSent && !deleted && (
            <Button variant="outline" size="sm" onClick={handleDelete}>
              <Trash2 size={14} />메시지 삭제
            </Button>
          )}
          {(hasWaiting || !hasSent) && !deleted && (
            <Button size="sm" onClick={() => handleSend()} disabled={sending}>
              <Send size={14} />{sending ? "전송 중..." : "Slack 전송"}
            </Button>
          )}
        </div>
      </div>

      {tasks.length === 0 ? (
        <p className="text-muted-foreground">태스크가 없습니다. 웨이브를 먼저 확정하세요.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-3">SKU</th>
                <th className="px-4 py-3">채널</th>
                <th className="px-4 py-3">수량</th>
                <th className="px-4 py-3">상태</th>
                <th className="px-4 py-3">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {tasks.map((t, i) => (
                <tr key={t.task_id ?? i} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium">{t.sku_name}</div>
                    <div className="text-xs text-muted-foreground">{t.sku_id}</div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{t.slack_channel || t.zone}</td>
                  <td className="px-4 py-3">{t.total_qty}</td>
                  <td className="px-4 py-3">
                    <Badge variant={
                      t.task_status === "DONE"    ? "default" :
                      t.task_status === "BLOCKED" ? "destructive" :
                      t.task_status === "SENT"    ? "secondary" : "outline"
                    }>
                      {statusLabel(t.task_status)}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    {(t.task_status === "READY" || t.task_status === "QUEUED") && (
                      <Button size="sm" variant="outline" className="h-7 gap-1 text-xs"
                        onClick={() => handleSend()} disabled={sending}>
                        <Send size={10} />전송
                      </Button>
                    )}
                    {t.task_status === "SENT" && (
                      <Button size="sm" variant="outline" className="h-7 gap-1 text-xs"
                        onClick={handleDelete}>
                        <Trash2 size={10} />삭제
                      </Button>
                    )}
                    {t.task_status === "BLOCKED" && (
                      <Button size="sm" variant="outline" className="h-7 gap-1 text-xs"
                        onClick={() => handleSend()}>
                        <RefreshCw size={10} />재시도
                      </Button>
                    )}
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
