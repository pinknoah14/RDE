"use client";
import { useEffect, useState } from "react";
import { MapPin, Plus, Pencil, Trash2, X, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ZoneLayoutModal } from "@/components/settings/ZoneLayoutModal";
import { AisleAnchorModal } from "@/components/settings/AisleAnchorModal";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { ZoneConfig } from "@/types";

type EditRow = {
  zone_prefix: string;
  zone_name: string;
  slack_channel: string;
  slack_channel_id: string;
  access_type: "FORKLIFT" | "WALKING";
  list_section: "MAIN" | "SUB";
  is_special_zone: boolean;
};

const BLANK: EditRow = {
  zone_prefix: "",
  zone_name: "",
  slack_channel: "",
  slack_channel_id: "",
  access_type: "FORKLIFT",
  list_section: "MAIN",
  is_special_zone: false,
};

function ZoneForm({
  value,
  onChange,
  prefixDisabled,
}: {
  value: EditRow;
  onChange: (v: EditRow) => void;
  prefixDisabled: boolean;
}) {
  const set = (k: keyof EditRow, v: string | boolean) =>
    onChange({ ...value, [k]: v });

  const inp = "w-full rounded border px-2 py-1 text-sm";
  const sel = "rounded border px-2 py-1 text-sm";

  return (
    <tr className="bg-purple-50">
      <td className="px-2 py-2">
        <input className={inp} value={value.zone_prefix} disabled={prefixDisabled}
          placeholder="예) RA" onChange={(e) => set("zone_prefix", e.target.value)} />
      </td>
      <td className="px-2 py-2">
        <input className={inp} value={value.zone_name}
          placeholder="존 이름" onChange={(e) => set("zone_name", e.target.value)} />
      </td>
      <td className="px-2 py-2">
        <input className={inp} value={value.slack_channel}
          placeholder="#채널명" onChange={(e) => set("slack_channel", e.target.value)} />
      </td>
      <td className="px-2 py-2">
        <select className={sel} value={value.access_type}
          onChange={(e) => set("access_type", e.target.value as "FORKLIFT" | "WALKING")}>
          <option value="FORKLIFT">지게차</option>
          <option value="WALKING">도보</option>
        </select>
      </td>
      <td className="px-2 py-2">
        <select className={sel} value={value.list_section}
          onChange={(e) => set("list_section", e.target.value as "MAIN" | "SUB")}>
          <option value="MAIN">MAIN</option>
          <option value="SUB">SUB</option>
        </select>
      </td>
      <td className="px-2 py-2 text-center">—</td>
      <td className="px-2 py-2 text-center">
        <input type="checkbox" checked={value.is_special_zone}
          onChange={(e) => set("is_special_zone", e.target.checked)} />
      </td>
      <td className="px-2 py-2 text-center">—</td>
      <td className="px-2 py-2" />
    </tr>
  );
}

export default function ZonesPage() {
  const [zones, setZones] = useState<ZoneConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [layoutModal, setLayoutModal] = useState<string | null>(null);
  const [anchorModal, setAnchorModal] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<EditRow>(BLANK);
  const [editId, setEditId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditRow>(BLANK);
  const [saving, setSaving] = useState(false);

  const load = () =>
    api.getZones().then(setZones)
      .catch((e) => toast({ title: "데이터 로드 실패", description: (e as Error).message, variant: "destructive" }))
      .finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  const toggleScattered = async (zone: ZoneConfig) => {
    try {
      await api.updateZone(zone.zone_config_id, { is_scattered: !zone.is_scattered });
      toast({ title: `산재 존 ${!zone.is_scattered ? "활성" : "비활성"}` });
      load();
    } catch (e) {
      toast({ title: "오류", description: (e as Error).message, variant: "destructive" });
    }
  };

  const startEdit = (z: ZoneConfig) => {
    setEditId(z.zone_config_id);
    setEditForm({
      zone_prefix: z.zone_prefix,
      zone_name: z.zone_name,
      slack_channel: z.slack_channel,
      slack_channel_id: z.slack_channel_id ?? "",
      access_type: z.access_type,
      list_section: z.list_section,
      is_special_zone: z.is_special_zone,
    });
    setAdding(false);
  };

  const saveEdit = async (id: number) => {
    setSaving(true);
    try {
      await api.updateZone(id, {
        zone_name: editForm.zone_name,
        slack_channel: editForm.slack_channel,
        slack_channel_id: editForm.slack_channel_id || undefined,
        access_type: editForm.access_type,
        list_section: editForm.list_section,
        is_special_zone: editForm.is_special_zone,
      });
      toast({ title: "저장 완료" });
      setEditId(null);
      load();
    } catch (e) {
      toast({ title: "저장 실패", description: (e as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const saveAdd = async () => {
    if (!addForm.zone_prefix.trim()) {
      toast({ title: "존코드를 입력하세요", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      await api.createZone({
        zone_prefix: addForm.zone_prefix.trim(),
        zone_name: addForm.zone_name,
        slack_channel: addForm.slack_channel,
        slack_channel_id: addForm.slack_channel_id || undefined,
        access_type: addForm.access_type,
        list_section: addForm.list_section,
        is_special_zone: addForm.is_special_zone,
      });
      toast({ title: "존 추가 완료" });
      setAdding(false);
      setAddForm(BLANK);
      load();
    } catch (e) {
      toast({ title: "추가 실패", description: (e as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const deleteZone = async (z: ZoneConfig) => {
    if (!confirm(`'${z.zone_prefix}' 존을 삭제하시겠습니까?`)) return;
    try {
      await api.deleteZone(z.zone_config_id);
      toast({ title: "삭제 완료" });
      load();
    } catch (e) {
      toast({ title: "삭제 실패", description: (e as Error).message, variant: "destructive" });
    }
  };

  if (loading) {
    return <div className="p-6"><div className="h-40 animate-pulse rounded-lg bg-gray-100" /></div>;
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">존 설정</h1>
        <Button size="sm" className="gap-1" onClick={() => { setAdding(true); setEditId(null); setAddForm(BLANK); }}>
          <Plus size={14} />추가
        </Button>
      </div>
      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
            <tr>
              <th className="px-4 py-3 text-left">존코드</th>
              <th className="px-4 py-3 text-left">존이름</th>
              <th className="px-4 py-3 text-left">채널</th>
              <th className="px-4 py-3 text-left">접근유형</th>
              <th className="px-4 py-3 text-left">구분</th>
              <th className="px-4 py-3 text-left">층</th>
              <th className="px-4 py-3 text-center">산재</th>
              <th className="px-4 py-3 text-left">위치설정</th>
              <th className="px-4 py-3 text-right">관리</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {adding && (
              <ZoneForm value={addForm} onChange={setAddForm} prefixDisabled={false} />
            )}
            {adding && (
              <tr className="bg-purple-50">
                <td colSpan={9} className="px-4 pb-3 text-right">
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="outline" onClick={() => setAdding(false)}>
                      <X size={12} />취소
                    </Button>
                    <Button size="sm" disabled={saving} onClick={saveAdd}>
                      <Check size={12} />{saving ? "저장 중..." : "저장"}
                    </Button>
                  </div>
                </td>
              </tr>
            )}
            {zones.map((z) =>
              editId === z.zone_config_id ? (
                <>
                  <ZoneForm key={`form-${z.zone_config_id}`} value={editForm} onChange={setEditForm} prefixDisabled={true} />
                  <tr key={`actions-${z.zone_config_id}`} className="bg-purple-50">
                    <td colSpan={9} className="px-4 pb-3 text-right">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="outline" onClick={() => setEditId(null)}>
                          <X size={12} />취소
                        </Button>
                        <Button size="sm" disabled={saving} onClick={() => saveEdit(z.zone_config_id)}>
                          <Check size={12} />{saving ? "저장 중..." : "저장"}
                        </Button>
                      </div>
                    </td>
                  </tr>
                </>
              ) : (
                <tr key={z.zone_config_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono font-medium">{z.zone_prefix}</td>
                  <td className="px-4 py-3">{z.zone_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">{z.slack_channel}</td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{z.access_type === "FORKLIFT" ? "지게차" : "도보"}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={z.list_section === "MAIN" ? "default" : "secondary"}>
                      {z.list_section}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {z.floor === 0 ? "1층" : "메자닌"}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <input type="checkbox" checked={!!z.is_scattered}
                      onChange={() => toggleScattered(z)} className="cursor-pointer" />
                  </td>
                  <td className="px-4 py-3">
                    {z.is_scattered ? (
                      <Button size="sm" variant="outline" className="h-7 text-xs"
                        onClick={() => setAnchorModal(z.zone_prefix)}>
                        <MapPin size={10} />통로별 설정
                      </Button>
                    ) : (
                      <Button size="sm" variant="outline" className="h-7 text-xs"
                        onClick={() => setLayoutModal(z.zone_prefix)}>
                        <MapPin size={10} />위치 설정
                      </Button>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
                        onClick={() => startEdit(z)}>
                        <Pencil size={13} />
                      </Button>
                      <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-red-500 hover:text-red-600"
                        onClick={() => deleteZone(z)}>
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  </td>
                </tr>
              )
            )}
          </tbody>
        </table>
      </div>

      {layoutModal && (
        <ZoneLayoutModal zoneCode={layoutModal} open={true}
          onClose={() => { setLayoutModal(null); load(); }} />
      )}
      {anchorModal && (
        <AisleAnchorModal zoneCode={anchorModal} open={true}
          onClose={() => { setAnchorModal(null); load(); }} />
      )}
    </div>
  );
}
