"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Bell,
  Blocks,
  Brain,
  LogOut,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Settings,
  ScrollText,
  Sparkles,
  Sun,
  UserRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import { LogoMark } from "@/components/ui/Logo";
import { registerSW } from "@/lib/push";
import type { Me } from "@/lib/types";

type NavItem = { href: string; label: string; icon: LucideIcon };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { href: "/chat", label: "群聊", icon: MessagesSquare },
      { href: "/bots", label: "Bot", icon: Blocks },
      { href: "/skills", label: "技能", icon: Sparkles },
      { href: "/mcp", label: "MCP", icon: Plug },
    ],
  },
  {
    label: "Insight",
    items: [
      { href: "/memory", label: "记忆", icon: Brain },
      { href: "/status", label: "运行状况", icon: Activity },
      { href: "/usage", label: "用量", icon: BarChart3 },
      { href: "/logs", label: "日志", icon: ScrollText },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/notifications", label: "通知", icon: Bell },
      { href: "/settings", label: "设置", icon: Settings },
    ],
  },
];

const SECTIONS: Record<string, string> = {
  chat: "CHAT",
  bots: "AGENTS",
  skills: "SKILLS",
  mcp: "MCP",
  memory: "MEMORY",
  status: "HEALTH",
  usage: "USAGE",
  logs: "LOGS",
  notifications: "INBOX",
  settings: "SETTINGS",
};

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

/** 侧栏折叠状态：桌面端可收起为 68px 图标栏，记忆在 localStorage */
function useCollapsed() {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem("botly-nav") === "collapsed");
    } catch {}
  }, []);
  const toggleCollapsed = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("botly-nav", next ? "collapsed" : "expanded");
      } catch {}
      return next;
    });
  return { collapsed, toggleCollapsed };
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
  const { collapsed, toggleCollapsed } = useCollapsed();
  const [me, setMe] = useState<Me | null>(null);
  const [unread, setUnread] = useState(0);
  const [userOpen, setUserOpen] = useState(false);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    const load = () => api.notifications().then((d) => setUnread(d.unread_count)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    // NTF-06：提前注册 Service Worker，订阅在设置页开启推送时完成
    registerSW().catch(() => {});
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

  useEffect(() => setUserOpen(false), [path]);

  if (bare) return <>{children}</>;

  const section = SECTIONS[path.split("/")[1] || ""] || "CONSOLE";

  const navItemClass = (active: boolean, isCollapsed: boolean) =>
    `group relative flex shrink-0 items-center gap-2.5 rounded-md text-[12px] font-medium transition-colors duration-150 max-lg:h-10 max-lg:w-11 max-lg:justify-center max-lg:px-0 ${
      isCollapsed ? "h-9 w-9 justify-center px-0" : "h-9 px-2.5"
    } ${
      active
        ? "bg-accent/[0.11] text-accent ring-1 ring-inset ring-accent/25"
        : "text-muted hover:bg-panel2 hover:text-ink"
    }`;

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-canvas">
      {/* 顶栏：品牌 + 当前分区 + 全局状态 */}
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-3 sm:px-4">
        <Link href="/chat" className="flex min-w-0 items-center gap-2.5" title="Botly">
          <LogoMark size={26} />
          <span className="min-w-0">
            <span className="block font-mono text-[13px] font-semibold uppercase leading-none tracking-[0.1em]">
              Botly
            </span>
            <span className="mt-1 hidden truncate font-mono text-[8px] uppercase leading-none tracking-[0.16em] text-faint sm:block">
              Multi-agent console
            </span>
          </span>
        </Link>
        <span className="hidden font-mono text-[9px] uppercase tracking-[0.16em] text-faint lg:inline">
          / {section}
        </span>

        <div className="ml-auto flex items-center gap-1.5">
          <span className="hidden items-center gap-1.5 rounded-full border border-line bg-panel2 px-2.5 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            v1.0
          </span>
          <button
            onClick={toggle}
            title={light ? "切换到深色" : "切换到浅色"}
            className="icon-btn"
          >
            {light ? <Moon size={16} /> : <Sun size={16} />}
          </button>
          <div className="relative">
            <button
              onClick={() => setUserOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={userOpen}
              className={`flex h-8 items-center gap-2 rounded-md border border-transparent px-1.5 text-left transition-colors hover:border-line hover:bg-panel2 ${
                userOpen ? "border-line bg-panel2" : ""
              }`}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent/30 bg-accent/10 text-[10px] font-semibold text-accent">
                {me?.name?.slice(0, 1)?.toUpperCase() || <UserRound size={12} />}
              </span>
              <span className="hidden max-w-[110px] truncate text-[11.5px] text-muted md:block">
                {me?.name || "未登录"}
              </span>
            </button>

            {userOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setUserOpen(false)} />
                <div
                  role="menu"
                  className="absolute right-0 top-full z-50 mt-2 w-56 animate-rise rounded-lg border border-line bg-panel p-1.5 shadow-pop"
                >
                  <div className="border-b border-line/70 px-2.5 pb-2 pt-1.5">
                    <div className="truncate text-[12.5px] font-medium">{me?.name || "未登录"}</div>
                    <div className="mt-0.5 truncate font-mono text-[10px] text-faint">
                      {me?.email || "—"}
                    </div>
                  </div>
                  <button
                    onClick={async () => {
                      setUserOpen(false);
                      await api.logout().catch(() => {});
                      router.push("/login");
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px] text-muted transition-colors hover:bg-danger/10 hover:text-danger"
                    role="menuitem"
                  >
                    <LogOut size={14} /> 退出登录
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav
          className={`no-scrollbar relative flex shrink-0 flex-col border-r border-line bg-panel py-3 transition-[width] duration-200 max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:z-40 max-lg:h-[58px] max-lg:w-full max-lg:flex-row max-lg:items-center max-lg:gap-1 max-lg:overflow-x-auto max-lg:border-r-0 max-lg:border-t max-lg:px-1.5 max-lg:py-0 ${
            collapsed
              ? "w-[68px] items-center px-2.5 max-lg:w-full"
              : "w-[224px] px-3 max-lg:w-full"
          }`}
          style={{ scrollbarWidth: "none" }}
        >
          <div
            className={`mb-1 flex w-full items-center max-lg:hidden ${
              collapsed ? "justify-center" : "justify-end px-1"
            }`}
          >
            <button
              onClick={toggleCollapsed}
              title={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              className="icon-btn h-7 w-7"
            >
              {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
            </button>
          </div>

          <div className="flex min-h-0 w-full flex-1 flex-col gap-0.5 overflow-y-auto overflow-x-hidden max-lg:contents">
            {NAV_GROUPS.map((group) => (
              <div key={group.label} className="max-lg:contents">
                {!collapsed && (
                  <div className="mb-1 mt-4 px-2.5 font-mono text-[9px] font-medium uppercase tracking-[0.2em] text-faint/80 max-lg:hidden">
                    {group.label}
                  </div>
                )}
                {group.items.map((n) => {
                  const active = path.startsWith(n.href);
                  const Icon = n.icon;
                  return (
                    <Link
                      key={n.href}
                      href={n.href}
                      title={n.label}
                      aria-current={active ? "page" : undefined}
                      className={navItemClass(active, collapsed)}
                    >
                      {active && !collapsed && (
                        <span className="absolute inset-y-2 left-0 w-[2.5px] rounded-full bg-accent max-lg:hidden" />
                      )}
                      <Icon
                        size={16}
                        strokeWidth={active ? 2.1 : 1.9}
                        className="shrink-0 transition-transform duration-150 group-active:scale-90"
                      />
                      {!collapsed && <span className="truncate max-lg:hidden">{n.label}</span>}
                      {n.href === "/notifications" && unread > 0 && (
                        <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-accent ring-2 ring-panel max-lg:right-1 max-lg:top-1" />
                      )}
                    </Link>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="mt-auto hidden w-full border-t border-line/70 px-2.5 pt-3 max-lg:hidden">
            {!collapsed && (
              <div className="font-mono text-[8px] uppercase leading-relaxed tracking-[0.16em] text-faint/70">
                v1.0 · self-hosted
              </div>
            )}
          </div>
        </nav>

        <main className="min-w-0 flex-1 overflow-hidden max-lg:pb-[58px]">{children}</main>
      </div>
    </div>
  );
}
