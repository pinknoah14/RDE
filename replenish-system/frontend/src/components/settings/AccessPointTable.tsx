"use client";
import { Check, X, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { FloorAccessPoint, FloorAccessPointInput } from "@/types";

interface Props {
  points: FloorAccessPoint[];
  editing: number | "new" | null;
  form: FloorAccessPointInput;
  onChange: (patch: Partial<FloorAccessPointInput>) => void;
  onSave: () => void;
  onCancel: () => void;
  onEdit: (p: FloorAccessPoint) => void;
  onDelete: (id: number) => void;
}

export function AccessPointTable({
  points, editing, form, onChange, onSave, onCancel, onEdit, onDelete,
}: Props) {
  const FormRow = () => (
    <tr className="bg-purple-50">
      <td className="px-4 py-2">
        <input value={form.name} onChange={(e) => onChange({ name: e.target.value })}
          placeholder="이름" className="w-full rounded border px-2 py-1 text-sm" />
      </td>
      <td className="px-4 py-2">
        <select value={form.access_type}
          onChange={(e) => onChange({ access_type: e.target.value as "STAIRS" | "LIFT" })}
          className="rounded border px-2 py-1 text-sm">
          <option value="STAIRS">계단</option>
          <option value="LIFT">리프트</option>
        </select>
      </td>
      <td className="px-4 py-2">
        <input type="number" step={0.5} value={form.x}
          onChange={(e) => onChange({ x: +e.target.value })}
          className="w-20 rounded border px-2 py-1 text-sm text-right" />
      </td>
      <td className="px-4 py-2">
        <input type="number" step={0.5} value={form.y}
          onChange={(e) => onChange({ y: +e.target.value })}
          className="w-20 rounded border px-2 py-1 text-sm text-right" />
      </td>
      <td className="px-4 py-2 text-center">
        <input type="checkbox" checked={form.is_active}
          onChange={(e) => onChange({ is_active: e.target.checked })} />
      </td>
      <td className="px-4 py-2">
        <div className="flex gap-1">
          <button onClick={onSave} className="text-green-600 hover:text-green-700"><Check size={16} /></button>
          <button onClick={onCancel} className="text-muted-foreground hover:text-red-600"><X size={16} /></button>
        </div>
      </td>
    </tr>
  );

  return (
    <div className="overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-xs font-medium text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left">이름</th>
            <th className="px-4 py-3 text-left">유형</th>
            <th className="px-4 py-3 text-left">X (m)</th>
            <th className="px-4 py-3 text-left">Y (m)</th>
            <th className="px-4 py-3 text-center">활성</th>
            <th className="px-4 py-3 text-left">편집</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {editing === "new" && <FormRow />}
          {points.map((p) =>
            editing === p.access_id ? (
              <FormRow key={p.access_id} />
            ) : (
              <tr key={p.access_id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium">{p.name}</td>
                <td className="px-4 py-3">
                  <Badge variant="outline">{p.access_type === "STAIRS" ? "계단" : "리프트"}</Badge>
                </td>
                <td className="px-4 py-3 text-muted-foreground">{p.x}</td>
                <td className="px-4 py-3 text-muted-foreground">{p.y}</td>
                <td className="px-4 py-3 text-center">{p.is_active ? "✅" : "❌"}</td>
                <td className="px-4 py-3">
                  <div className="flex gap-1">
                    <button onClick={() => onEdit(p)} className="text-muted-foreground hover:text-[#5F0080]">
                      <Pencil size={14} />
                    </button>
                    <button onClick={() => onDelete(p.access_id)} className="text-muted-foreground hover:text-red-600">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            )
          )}
          {points.length === 0 && editing !== "new" && (
            <tr>
              <td colSpan={6} className="px-4 py-8 text-center text-sm text-muted-foreground">
                등록된 계단/리프트 없음
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
