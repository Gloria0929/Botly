"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect, useState } from "react";
import {
  BarChart3,
  Bell,
  Blocks,
  Brain,
  LogOut,
  MessagesSquare,
  Moon,
  Settings,
  ScrollText,
  Sun,
  UserRound,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

const NAV = [
  { href: "/chat", label: "群聊", icon: MessagesSquare },
  { href: "/bots", label: "Bot", icon: Blocks },
  { href: "/memory", label: "记忆", icon: Brain },
  { href: "/usage", label: "用量", icon: BarChart3 },
  { href: "/logs", label: "日志", icon: ScrollText },
  { href: "/notifications", label: "通知", icon: Bell },
  { href: "/settings", label: "设置", icon: Settings },
];

function useTheme() {
  const [light, setLight] = useState(false);
  useEffect(() => {
    setLight(document.documentElement.classList.contains("light"));
  }, []);
  const toggle = () => {
    const next = !document.documentElement.classList.contains("light");
    document.documentElement.classList.toggle("light", next);
    localStorage.setItem("botly-theme", next ? "light" : "dark");
    setLight(next);
  };
  return { light, toggle };
}

export default function AppShell({
  children,
  bare = false,
}: {
  children: React.ReactNode;
  bare?: boolean;
}) {
  const path = usePathname();
  const router = useRouter();
  const { light, toggle } = useTheme();
  const [me, setMe] = useState<Me | null>(null);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    const load = () => api.notifications().then((d) => setUnread(d.unread_count)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  // 全局系统通知：聊天页自己订阅（还能判断是否正在查看该群聊），
  // 其他页面也必须能收到审批/提问/完成提醒（NTF-03）。
  useEffect(() => {
    if (path.startsWith("/chat")) return;
    const es = new EventSource("/api/notifications/events");
    const onCreated = (ev: Event) => {
      const p = JSON.parse((ev as MessageEvent).data);
      setUnread((n) => n + 1);
      if (me?.settings?.notifications_enabled === false) return;
      if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
      try {
        const n = new Notification(p.title || "Botly", { body: p.body || "", tag: p.id });
        n.onclick = () => {
          window.focus();
          const q = new URLSearchParams();
          if (p.chat_id) q.set("chat", p.chat_id);
          if (p.target_type && p.target_id) {
            q.set("target", p.target_type);
            q.set("id", p.target_id);
          }
          router.push(`/chat?${q.toString()}`);
          n.close();
        };
      } catch {
        /* 浏览器不支持或权限已撤销，页面内通知中心兜底 */
      }
    };
    es.addEventListener("notification.created", onCreated);
    return () => es.close();
  }, [path, me?.settings?.notifications_enabled, router]);

  if (bare) return <>{children}</>;

  return (
    <div className="flex h-screen w-full overflow-hidden bg-canvas">
      <nav className="flex w-[68px] shrink-0 flex-col items-center gap-1 border-r border-line bg-panel py-3 max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:z-40 max-lg:h-[56px] max-lg:w-full max-lg:flex-row max-lg:justify-around max-lg:border-r-0 max-lg:border-t max-lg:py-0">
        <Link
          href="/chat"
          className="mb-2 flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-[15px] font-bold text-accentink max-lg:hidden"
          title="Botly"
        >
          B
        </Link>
        {NAV.map((n) => {
          const active = path.startsWith(n.href);
          const Icon = n.icon;
          return (
            <Link
              key={n.href}
              href={n.href}
              title={n.label}
              className={`relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors ${
                active
                  ? "bg-accent/15 text-accent"
                  : "text-faint hover:bg-panel2 hover:text-ink"
              }`}
            >
              <Icon size={18} strokeWidth={1.9} />
              {n.href === "/notifications" && unread > 0 && (
                <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-danger" />
              )}
            </Link>
          );
        })}
        <div className="mt-auto flex flex-col items-center gap-1 max-lg:mt-0 max-lg:flex-row max-lg:gap-1">
          <button
            onClick={toggle}
            title={light ? "切换到深色" : "切换到浅色"}
            className="flex h-10 w-10 items-center justify-center rounded-xl text-faint hover:bg-panel2 hover:text-ink"
          >
            {light ? <Moon size={17} /> : <Sun size={17} />}
          </button>
          <div className="group relative">
            <button className="flex h-9 w-9 items-center justify-center rounded-full border border-line bg-panel2 text-[12px] font-medium text-muted">
              {me?.name?.slice(0, 1)?.toUpperCase() || <UserRound size={15} />}
            </button>
            <div className="invisible absolute bottom-11 left-0 z-50 w-52 rounded-xl border border-line bg-panel p-2 opacity-0 shadow-card transition-all group-hover:visible group-hover:opacity-100">
              <div className="px-2 py-1.5 text-[12px] text-muted">
                {me?.email || "未登录"}
              </div>
              <button
                onClick={async () => {
                  await api.logout().catch(() => {});
                  router.push("/login");
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-muted hover:bg-panel2 hover:text-ink"
              >
                <LogOut size={14} /> 退出登录
              </button>
            </div>
          </div>
        </div>
      </nav>
      <main className="min-w-0 flex-1 overflow-hidden max-lg:pb-[56px]">{children}</main>
    </div>
  );
}
