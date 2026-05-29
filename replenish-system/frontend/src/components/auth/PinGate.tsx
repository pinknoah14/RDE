"use client";
import { useEffect, useState } from "react";
import { api, auth } from "@/lib/api";

export function PinGate({ children }: { children: React.ReactNode }) {
  const [verified, setVerified] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [attempts, setAttempts] = useState(0);
  const [locked, setLocked] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // 저장된 토큰이 있으면 통과 (만료/위조 시 백엔드 401 → api.ts 가 자동 로그아웃)
    if (auth.get()) {
      setVerified(true);
      setChecking(false);
      return;
    }
    // 빈 PIN 호출 → admin_pin 미설정이면 auth_required=false (인증 비활성, 통과)
    api.verifyPin("")
      .then((res) => {
        if (res.ok && res.auth_required === false) {
          setVerified(true);
        }
      })
      .catch(() => {
        /* PIN 설정됨 → 입력 화면 */
      })
      .finally(() => setChecking(false));
  }, []);

  const handleVerify = async () => {
    if (locked) return;
    setError("");
    try {
      const res = await api.verifyPin(pin);
      if (res.ok) {
        if (res.token) auth.set(res.token);
        setVerified(true);
      } else {
        throw new Error("INVALID");
      }
    } catch {
      const next = attempts + 1;
      setAttempts(next);
      setPin("");
      if (next >= 3) {
        setLocked(true);
        setError("3회 실패. 30초 후 다시 시도하세요.");
        setTimeout(() => {
          setLocked(false);
          setAttempts(0);
          setError("");
        }, 30000);
      } else {
        setError(`PIN이 올바르지 않습니다. (${next}/3)`);
      }
    }
  };

  if (checking) return null;
  if (verified) return <>{children}</>;

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white p-8 rounded-xl shadow w-80">
        <h1 className="text-xl font-bold mb-6 text-center">보충 운영 시스템</h1>
        <input
          type="password"
          inputMode="numeric"
          maxLength={8}
          placeholder="PIN 입력"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, ""))}
          onKeyDown={(e) => e.key === "Enter" && handleVerify()}
          disabled={locked}
          className="w-full border rounded px-3 py-2 text-center text-2xl tracking-widest mb-3"
          autoFocus
        />
        {error && (
          <p className="text-red-500 text-sm text-center mb-3">{error}</p>
        )}
        <button
          onClick={handleVerify}
          disabled={locked || pin.length < 4}
          className="w-full bg-black text-white py-2 rounded disabled:opacity-40"
        >
          {locked ? "잠금 중..." : "확인"}
        </button>
      </div>
    </div>
  );
}
