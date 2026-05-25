"use client";
import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { SystemConfig } from "@/types";

const GROUPS = [
  { key: "WAVE",      label: "웨이브" },
  { key: "ALGORITHM", label: "알고리즘" },
  { key: "PICKING",   label: "피킹" },
  { key: "SLACK",     label: "Slack" },
  { key: "WORKER",    label: "작업자" },
  { key: "SYSTEM",    label: "시스템" },
];

const ALGO_V17_KEYS = [
  "floor_change_penalty",
  "proximity_score_threshold_near",
  "proximity_score_threshold_mid",
  "proximity_score_threshold_far",
  "expiry_critical_days",
  "weight_expiry_critical",
  "weight_expiry",
  "weight_unassigned",
  "weight_new_sku",
  "weight_event_active",
  "weight_prev_blocked",
];

function ConfigGroup({ configs, group }: { configs: SystemConfig[]; group: string }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    const map: Record<string, string> = {};
    configs.forEach((c) => {
      // SECRET 필드는 빈 문자열로 초기화 — 백엔드가 "***"를 반환하므로
      // 값을 변경하지 않고 저장하면 "***"가 실제 값으로 덮어씌워지는 버그 방지
      map[c.config_key] = c.config_type === "SECRET" ? "" : c.config_value;
    });
    setValues(map);
  }, [configs]);

  const save = async (key: string) => {
    setSaving(key);
    try {
      await api.updateSystemConfig(key, values[key]);
      toast({ title: "저장 완료", description: key });
    } catch (e) {
      toast({ title: "저장 실패", description: (e as Error).message, variant: "destructive" });
    } finally { setSaving(null); }
  };

  const groupConfigs = configs.filter((c) => c.config_group === group);

  return (
    <div className="space-y-3">
      {groupConfigs.length === 0 && (
        <p className="text-sm text-muted-foreground py-4">설정 항목 없음</p>
      )}
      {groupConfigs.map((c) => (
        <div key={c.config_key} className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <div className="flex-1">
            <p className="text-sm font-medium">{c.label || c.config_key}</p>
            {c.description && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                  {c.description.split("\n")[0]}
                  {c.description.includes("\n") && " …"}
                </summary>
                <pre className="mt-1 whitespace-pre-line rounded bg-gray-50 p-2 text-xs text-muted-foreground font-sans">{c.description}</pre>
              </details>
            )}
            {group === "ALGORITHM" && ALGO_V17_KEYS.includes(c.config_key) && (
              <span className="mt-0.5 inline-block rounded-full bg-purple-100 px-2 py-0.5 text-xs text-primary">v1.7</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <input
              type={c.config_type === "SECRET" ? "password" : "text"}
              value={values[c.config_key] ?? ""}
              onChange={(e) => setValues((p) => ({ ...p, [c.config_key]: e.target.value }))}
              placeholder={c.config_type === "SECRET" ? "새 값 입력 (비우면 PIN 해제)" : ""}
              className="w-48 rounded border px-2 py-1.5 text-sm font-mono"
            />
            <Button size="sm" variant="outline" className="h-8 gap-1"
              onClick={() => save(c.config_key)} disabled={saving === c.config_key}>
              <Save size={12} />
              {saving === c.config_key ? "..." : "저장"}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SystemConfigPage() {
  const [configs, setConfigs] = useState<SystemConfig[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getSystemConfig().then(setConfigs)
      .catch((e) => toast({ title: "설정 로드 실패", description: (e as Error).message, variant: "destructive" }))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-bold">시스템 설정</h1>

      {loading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <div key={i} className="h-14 animate-pulse rounded-lg bg-gray-100" />)}</div>
      ) : (
        <Tabs defaultValue="ALGORITHM">
          <TabsList className="flex-wrap h-auto gap-1">
            {GROUPS.map((g) => <TabsTrigger key={g.key} value={g.key}>{g.label}</TabsTrigger>)}
          </TabsList>
          {GROUPS.map((g) => (
            <TabsContent key={g.key} value={g.key} className="mt-4">
              <ConfigGroup configs={configs} group={g.key} />
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  );
}
