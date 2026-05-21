"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Upload, Waves, Settings, ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";

interface MenuItem {
  label: string;
  href: string;
  icon?: React.ReactNode;
  children?: { label: string; href: string }[];
}

const MENU: MenuItem[] = [
  { label: "대시보드",  href: "/dashboard",  icon: <LayoutDashboard size={16} /> },
  { label: "업로드",   href: "/upload",      icon: <Upload size={16} /> },
  {
    label: "웨이브", href: "/waves", icon: <Waves size={16} />,
    children: [
      { label: "웨이브 생성", href: "/waves/new" },
      { label: "웨이브 이력", href: "/waves" },
    ],
  },
  {
    label: "설정", href: "/settings", icon: <Settings size={16} />,
    children: [
      { label: "작업자 관리",   href: "/settings/workers",        },
      { label: "피킹지번 관리", href: "/settings/picking-zones",  },
      { label: "이벤트 관리",   href: "/settings/events",         },
      { label: "존 설정",       href: "/settings/zones",          },
      { label: "계단/리프트",   href: "/settings/access-points",  },
      { label: "시스템 설정",   href: "/settings/system",         },
      { label: "데이터 관리",   href: "/settings/data",           },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState<Record<string, boolean>>({ "/waves": true, "/settings": true });

  const toggle = (href: string) => setOpen((p) => ({ ...p, [href]: !p[href] }));

  return (
    <aside className="flex h-screen w-52 flex-col border-r bg-gray-50">
      <div className="flex h-14 items-center border-b px-4">
        <span className="text-sm font-bold text-blue-600">보충 운영 시스템</span>
      </div>

      <nav className="flex-1 overflow-y-auto p-2 text-sm">
        {MENU.map((item) => (
          <div key={item.href} className="mb-0.5">
            {item.children ? (
              <>
                <button
                  onClick={() => toggle(item.href)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left hover:bg-gray-200",
                    pathname.startsWith(item.href) && "text-blue-600 font-medium"
                  )}
                >
                  {item.icon}
                  <span className="flex-1">{item.label}</span>
                  <ChevronDown size={12} className={cn("transition-transform", open[item.href] ? "rotate-0" : "-rotate-90")} />
                </button>
                {open[item.href] && (
                  <div className="ml-4 mt-0.5 space-y-0.5">
                    {item.children.map((c) => (
                      <Link
                        key={c.href}
                        href={c.href}
                        className={cn(
                          "block rounded-md px-3 py-1.5 hover:bg-gray-200",
                          pathname === c.href && "bg-blue-100 text-blue-700 font-medium"
                        )}
                      >
                        {c.label}
                      </Link>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <Link
                href={item.href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 hover:bg-gray-200",
                  pathname === item.href && "bg-blue-100 text-blue-700 font-medium"
                )}
              >
                {item.icon}
                {item.label}
              </Link>
            )}
          </div>
        ))}
      </nav>

      <div className="border-t p-3 text-xs text-muted-foreground">
        <p className="font-medium">v1.7.0</p>
      </div>
    </aside>
  );
}
