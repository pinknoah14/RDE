import type {
  Wave, WaveCreateRequest, Candidate,
  ZoneConfig, ZoneLayout, AisleAnchor,
  FloorAccessPoint, FloorAccessPointInput,
  SystemConfig, DashboardSummary,
  Worker, WorkerInput, UploadResult, UploadSession, QueueItem, UnknownZone,
} from "@/types";

const BASE = "/api/v1";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function upload<T>(path: string, file: File, extra?: Record<string, string>): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  if (extra) Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // 웨이브
  getWaves: () => request<Wave[]>("/waves"),
  createWave: (body: WaveCreateRequest) =>
    request<{ wave_id: number; wave_name: string; algorithm: { total_candidates: number; critical: number; high: number; medium: number; low: number; no_replen_skus: string[]; execution_ms: number } }>(
      "/waves", { method: "POST", body: JSON.stringify(body) }
    ),
  getWave: (id: number) => request<Wave>(`/waves/${id}`),
  confirmWave: (id: number, confirmedBy = "관리자") =>
    request<{ wave_id: number; tasks_created: number }>(
      `/waves/${id}/confirm?confirmed_by=${encodeURIComponent(confirmedBy)}`, { method: "POST" }
    ),
  sendWave: (id: number) => request<unknown>(`/waves/${id}/send`, { method: "POST" }),
  deleteWaveMessages: (id: number) => request<unknown>(`/waves/${id}/messages`, { method: "DELETE" }),

  // 추천 후보
  getCandidates: (waveId: number) => request<Candidate[]>(`/waves/${waveId}/candidates`),
  approveCandidate: (waveId: number, cid: number) =>
    request<Candidate>(`/waves/${waveId}/candidates/${cid}/approve`, { method: "POST" }),
  rejectCandidate: (waveId: number, cid: number, reason = "") =>
    request<Candidate>(`/waves/${waveId}/candidates/${cid}/reject?reason=${encodeURIComponent(reason)}`, { method: "POST" }),
  modifyCandidate: (waveId: number, cid: number, qty: number) =>
    request<Candidate>(`/waves/${waveId}/candidates/${cid}`, {
      method: "PATCH", body: JSON.stringify({ modified_qty: qty }),
    }),

  // 태스크
  getWaveTasks: (waveId: number) => request<QueueItem[]>(`/waves/${waveId}/tasks`),
  transitionTask: (waveId: number, taskId: number, status: string, extra?: Record<string, string>) => {
    const params = new URLSearchParams({ new_status: status, ...extra });
    return request<QueueItem>(`/waves/${waveId}/tasks/${taskId}/transition?${params}`, { method: "POST" });
  },

  // 존 설정
  getZones: () => request<ZoneConfig[]>("/zone-config"),
  createZone: (body: Partial<ZoneConfig>) =>
    request<ZoneConfig>("/zone-config", { method: "POST", body: JSON.stringify(body) }),
  updateZone: (id: number, body: Partial<ZoneConfig>) =>
    request<ZoneConfig>(`/zone-config/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteZone: (id: number) => request<{ deleted: number }>(`/zone-config/${id}`, { method: "DELETE" }),
  getZoneLayout: (code: string) => request<ZoneLayout>(`/zone-config/${code}/layout`),
  putZoneLayout: (code: string, body: ZoneLayout) =>
    request<ZoneConfig>(`/zone-config/${code}/layout`, { method: "PUT", body: JSON.stringify(body) }),
  getAisleAnchors: (code: string) => request<AisleAnchor[]>(`/zone-config/${code}/aisle-anchors`),
  putAisleAnchors: (code: string, body: AisleAnchor[]) =>
    request<AisleAnchor[]>(`/zone-config/${code}/aisle-anchors`, { method: "PUT", body: JSON.stringify(body) }),
  getUnknownZones: () => request<UnknownZone[]>("/zone-config/unknown-zones"),

  // 계단/리프트
  getAccessPoints: () => request<FloorAccessPoint[]>("/floor-access-points"),
  createAccessPoint: (body: FloorAccessPointInput) =>
    request<FloorAccessPoint>("/floor-access-points", { method: "POST", body: JSON.stringify(body) }),
  updateAccessPoint: (id: number, body: Partial<FloorAccessPointInput>) =>
    request<FloorAccessPoint>(`/floor-access-points/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteAccessPoint: (id: number) =>
    request<{ deleted: number }>(`/floor-access-points/${id}`, { method: "DELETE" }),

  // 업로드
  uploadInventory: (file: File, centerCd = "GGH1") =>
    upload<UploadResult>("/upload/inventory", file, { center_cd: centerCd }),
  uploadOutbound: (file: File, centerCd = "GGH1") =>
    upload<UploadResult>("/upload/outbound", file, { center_cd: centerCd }),
  uploadPivot: (file: File, centerCd = "GGH1") =>
    upload<UploadResult>("/upload/pivot-sales", file, { center_cd: centerCd }),
  getUploadSessions: () => request<UploadSession[]>("/upload/sessions"),

  // 대시보드
  getDashboard: () => request<DashboardSummary>("/dashboard"),

  // 시스템 설정
  getSystemConfig: (group?: string) =>
    request<SystemConfig[]>(`/system-config${group ? `?group=${group}` : ""}`),
  updateSystemConfig: (key: string, value: string) =>
    request<SystemConfig>(`/system-config/${key}`, {
      method: "PATCH", body: JSON.stringify({ config_value: value }),
    }),

  // 작업자
  getWorkers: () => request<Worker[]>("/workers"),
  createWorker: (body: WorkerInput) =>
    request<Worker>("/workers", { method: "POST", body: JSON.stringify(body) }),
  updateWorker: (id: number, body: Partial<WorkerInput>) =>
    request<Worker>(`/workers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  // DB 관리
  exportDb: () => fetch(`${BASE}/admin/db-export`),
  importDb: (file: File) => upload<{ message: string }>("/admin/db-import", file),
};
