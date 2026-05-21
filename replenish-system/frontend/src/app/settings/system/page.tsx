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
  { key: "SLACK",     label: "Slack" },
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
    configs.forEach((c) => { map[c.config_key] = c.config_value; });
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
            <p className="text-sm font-medium">{c.display_name || c.config_key}</p>
            {c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}
            {group === "ALGORITHM" && ALGO_V17_KEYS.includes(c.config_key) && (
              <span className="mt-0.5 inline-block rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">v1.7</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <input
              type={c.config_type === "SECRET" ? "password" : "text"}
              value={values[c.config_key] ?? ""}
              onChange={(e) => setValues((p) => ({ ...p, [c.config_key]: e.target.value }))}
              className="w-36 rounded border px-2 py-1.5 text-sm font-mono"
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
    api.getSystemConfig().then(setConfigs).catch(console.error).finally(() => setLoading(false));
  }, []);

  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-bold">시스템 설정</h1>

      {loading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <div key={i} className="h-14 animate-pulse rounded-lg bg-gray-100" />)}</div>
      ) : (
        <Tabs defaultValue="ALGORITHM">
          <TabsList>
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
