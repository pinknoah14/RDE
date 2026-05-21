"use client";
import { useEffect, useState } from "react";
import { MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ZoneLayoutModal } from "@/components/settings/ZoneLayoutModal";
import { AisleAnchorModal } from "@/components/settings/AisleAnchorModal";
import { api } from "@/lib/api";
import { toast } from "@/components/ui/toast";
import type { ZoneConfig } from "@/types";

export default function ZonesPage() {
  const [zones, setZones] = useState<ZoneConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [layoutModal, setLayoutModal] = useState<string | null>(null);
  const [anchorModal, setAnchorModal] = useState<string | null>(null);

  const load = () => api.getZones().then(setZones).catch(console.error).finally(() => setLoading(false));
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

  if (loading) {
    return <div className="p-6"><div className="h-40 animate-pulse rounded-lg bg-gray-100" /></div>;
  }

  return (
    <div className="p-6">
      <h1 className="mb-6 text-xl font-bold">존 설정</h1>
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
            </tr>
          </thead>
          <tbody className="divide-y">
            {zones.map((z) => (
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
              </tr>
            ))}
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
