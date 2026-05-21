import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(iso: string) {
  return new Date(iso).toLocaleString("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export function formatDateOnly(iso: string) {
  return new Date(iso).toLocaleDateString("ko-KR");
}

export function riskColor(level: string) {
  switch (level) {
    case "CRITICAL": return "bg-red-100 text-red-800 border-red-200";
    case "HIGH":     return "bg-orange-100 text-orange-800 border-orange-200";
    case "MEDIUM":   return "bg-yellow-100 text-yellow-800 border-yellow-200";
    default:         return "bg-green-100 text-green-800 border-green-200";
  }
}

export function riskLabel(level: string) {
  switch (level) {
    case "CRITICAL": return "위급";
    case "HIGH":     return "높음";
    case "MEDIUM":   return "보통";
    default:         return "낮음";
  }
}

export function proximityDot(score: number) {
  switch (score) {
    case 4: return "🟢";
    case 3: return "🟠";
    case 2: return "🟡";
    default: return "⚪";
  }
}

export function statusLabel(status: string) {
  const map: Record<string, string> = {
    DRAFT: "초안", CONFIRMED: "확정", SENT: "전송됨",
    COMPLETED: "완료", CANCELLED: "취소",
    PENDING: "검토중", APPROVED: "승인", REJECTED: "거절", MODIFIED: "수정됨",
    READY: "준비", QUEUED: "대기", BLOCKED: "차단", DONE: "완료",
  };
  return map[status] ?? status;
}
