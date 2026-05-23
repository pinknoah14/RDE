export interface Wave {
  wave_id: number;
  wave_name: string;
  wave_type: "REGULAR" | "URGENT";
  wave_status: "DRAFT" | "CONFIRMED" | "SENT" | "COMPLETED" | "CANCELLED";
  created_at: string;
  confirmed_at?: string;
  sent_at?: string;
  target_sku_count: number;
}

export interface MatchedBin {
  replenish_bin: string;
  allocated_qty: number;
  deadline_days?: number | null;
  receipt_date?: string | null;
  proximity_score?: number | null;
}

export interface Candidate {
  candidate_id: number;
  wave_id: number;
  sku_id: string;
  sku_name: string;
  risk_score: number;
  risk_level: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  picking_bin: string;
  picking_confidence: string;
  replenish_bin?: string;
  proximity_score?: number;
  recommended_qty: number;
  modified_qty?: number;
  today_sales: number;
  avg_daily_sales: number;
  eta_hours?: number;
  list_section: "MAIN" | "SUB";
  candidate_status: "PENDING" | "APPROVED" | "REJECTED" | "MODIFIED";
  reason_flags?: string;
  rejected_reason?: string;
  zone: string;
  slack_channel: string;
  matched_bins: MatchedBin[];
}

export interface EventItem {
  event_id: number;
  sku_id: string;
  event_type: string;
  event_name?: string;
  start_date: string;
  end_date: string;
  registered_by: string;
  memo?: string;
}

export interface PickingZone {
  bin_id: string;
  zone: string;
  is_active: boolean;
  memo?: string;
}

export interface ZoneConfig {
  zone_config_id: number;
  zone_prefix: string;
  zone_name: string;
  slack_channel: string;
  slack_channel_id?: string;
  access_type: "FORKLIFT" | "WALKING";
  list_section: "MAIN" | "SUB";
  floor: number;
  is_scattered: boolean;
  origin_x?: number;
  origin_y?: number;
  aisle_direction?: "x" | "y";
  aisle_gap?: number;
  bay_gap?: number;
  is_active: boolean;
  is_special_zone: boolean;
}

export interface ZoneLayout {
  floor: number;
  is_scattered: boolean;
  origin_x?: number | null;
  origin_y?: number | null;
  aisle_direction: "x" | "y";
  aisle_gap: number;
  bay_gap: number;
}

export interface AisleAnchor {
  aisle_no: number;
  anchor_x: number;
  anchor_y: number;
  floor: number;
}

export interface FloorAccessPoint {
  access_id: number;
  name: string;
  x: number;
  y: number;
  access_type: "STAIRS" | "LIFT";
  is_active: boolean;
}

export interface FloorAccessPointInput {
  name: string;
  x: number;
  y: number;
  access_type: "STAIRS" | "LIFT";
  is_active: boolean;
}

export interface SystemConfig {
  config_key: string;
  config_value: string;
  config_type: string;
  config_group: string;
  label: string;
  description?: string;
}

export interface DashboardSummary {
  risk_counts: { CRITICAL: number; HIGH: number; MEDIUM: number; LOW: number };
  critical_skus: Array<{ sku_id: string; sku_name: string; risk_score: number; eta_hours?: number }>;
  active_workers: number;
  total_workers: number;
  new_skus: number;
  stale_bins: number;
  unknown_zones: string[];
  unclaimed_tasks: number;
  multi_bin_skus: number;
}

export interface Worker {
  worker_id: number;
  worker_name: string;
  worker_type: "FORKLIFT" | "WALKING";
  is_active: boolean;
  is_sub_worker: boolean;
  max_tasks: number;
}

export interface WorkerInput {
  worker_name: string;
  worker_type: "FORKLIFT" | "WALKING";
  is_active: boolean;
  is_sub_worker: boolean;
  max_tasks: number;
}

export interface WaveCreateRequest {
  wave_name?: string;
  wave_type?: string;
  center_cd?: string;
  max_candidates?: number;
  urgent_only?: boolean;
  min_risk_score?: number;
  target_days?: number;
}

export interface UploadResult {
  upload_id?: number;
  message?: string;
  record_count?: number;
  picking_count?: number;
  replenish_count?: number;
  hold_count?: number;
  sku_count?: number;
  unknown_zones?: string[];
  multi_bin_skus?: number;
  bins_upserted?: number;
  zones_created?: number;
  zones_existing?: number;
}

export interface UploadSession {
  upload_id: number;
  upload_type: string;
  file_name: string;
  uploaded_by: string;
  uploaded_at: string;
  center_cd: string;
}

export interface QueueItem {
  task_id?: number;
  wave_id: number;
  sku_id: string;
  sku_name: string;
  zone: string;
  slack_channel: string;
  task_status: string;
  total_qty: number;
}

export interface UnknownZone {
  zone_prefix: string;
  seen_count: number;
  last_seen_at: string;
  is_resolved: boolean;
}
