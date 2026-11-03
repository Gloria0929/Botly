"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useEffect, useState, useSyncExternalStore } from "react";
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
  ChevronDown,
  ChevronRight,
  Menu,
  X,

} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api, NOTIFICATIONS_CHANGED_EVENT, type NotificationCountChange } from "@/lib/api";
import { LogoMark } from "@/components/ui/Logo";
import { registerSW } from "@/lib/push";
import type { Me } from "@/lib/types";

type NavItem = { href: string; label: string; icon: LucideIcon };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "工作空间",
    items: [
      { href: "/chat", label: "团队会话", icon: MessagesSquare },
      { href: "/bots", label: "智能体", icon: Blocks },
      { href: "/skills", label: "技能库", icon: Sparkles },
      { href: "/memory", label: "团队记忆", icon: Brain },
      { href: "/mcp", label: "工具连接", icon: Plug },
    ],
  },
  {
    label: "监控与洞察",
    items: [
      { href: "/status", label: "运行状况", icon: Activity },
      { href: "/usage", label: "用量", icon: BarChart3 },
      { href: "/logs", label: "日志", icon: ScrollText },
    ],
  },
  {
    label: "管理",
    items: [
      { href: "/notifications", label: "通知", icon: Bell },
      { href: "/settings", label: "设置", icon: Settings },
    ],
  },
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

// AppShell is mounted separately by each page. Keep its navigation preference
// outside the component so client-side navigation never resets the sidebar.
let navCollapsed = false;
let navInitialized = false;
const navListeners = new Set<() => void>();

function getCollapsedSnapshot() {
  if (!navInitialized && typeof window !== "undefined") {
    try {
      navCollapsed = localStorage.getItem("botly-nav") === "collapsed";
    } catch {}
    navInitialized = true;
  }
  return navCollapsed;
}

function subscribeCollapsed(listener: () => void) {
  navListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== "botly-nav" && event.key !== null) return;
    navCollapsed = event.key !== null && event.newValue === "collapsed";
    navListeners.forEach(notify => notify());
  };
  window.addEventListener("storage", onStorage);
  return () => {
    navListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function setNavigationCollapsed(value: boolean) {
  if (getCollapsedSnapshot() === value) return;
  navCollapsed = value;
  try {
    localStorage.setItem("botly-nav", value ? "collapsed" : "expanded");
  } catch {}
  navListeners.forEach(notify => notify());
}

function useCollapsed() {
  const collapsed = useSyncExternalStore(subscribeCollapsed, getCollapsedSnapshot, () => false);
  const toggleCollapsed = () => setNavigationCollapsed(!getCollapsedSnapshot());
  return { collapsed, toggleCollapsed };
}

export default function AppShell({
  children,
  bare = false,
  rightPanelOpen = false,
}: {
  children: React.ReactNode;
  bare?: boolean;
  rightPanelOpen?: boolean;
}) {
  const path = usePathname();
  const router = useRouter();
  const { light, toggle } = useTheme();
  const { collapsed, toggleCollapsed } = useCollapsed();
  const [me, setMe] = useState<Me | null>(null);
  const [unread, setUnread] = useState(0);
  const [userOpen, setUserOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);

  useEffect(() => {
    if (rightPanelOpen && window.matchMedia("(min-width: 1024px)").matches) {
      setNavigationCollapsed(true);
    }
  }, [rightPanelOpen]);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    let active = true;
    let revision = 0;
    const load = () => {
      const requestRevision = ++revision;
      return api.notifications().then((d) => {
        if (active && requestRevision === revision) setUnread(d.unread_count);
      }).catch(() => {});
    };
    const onChanged = (event: Event) => {
      const { unreadCount, readCount } = (event as CustomEvent<NotificationCountChange>).detail;
      setUnread((count) => unreadCount ?? Math.max(0, count - (readCount || 0)));
      void load();
    };
    window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, onChanged);
    load();
    const t = setInterval(load, 15000);
    // NTF-06：提前注册 Service Worker，订阅在设置页开启推送时完成
    registerSW().catch(() => {});
    return () => {
      active = false;
      clearInterval(t);
      window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, onChanged);
    };
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

  useEffect(() => { setUserOpen(false); setMobileNav(false); }, [path]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMobileNav(false); setUserOpen(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (bare) return <>{children}</>;

  const current = NAV_GROUPS.flatMap(g => g.items).find(n => path.startsWith(n.href));
  const isChat = path.startsWith("/chat");
  const narrow = (collapsed || isChat) && !mobileNav;

  return (
    <div className="workspace-shell flex w-full overflow-hidden bg-canvas">
      <a className="skip-link" href="#main-content">跳到主内容</a>
      {mobileNav && <button className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
      <aside aria-label="工作空间导航" className={`workspace-sidebar flex shrink-0 flex-col border-r transition-[width] duration-200 max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:z-50 ${mobileNav ? "max-lg:flex" : "max-lg:hidden"} ${narrow ? "w-[64px]" : "w-[208px]"}`}>
        <div className={`flex h-[64px] shrink-0 items-center ${narrow ? "justify-center" : "gap-3 px-5"}`}>
          <Link href="/chat" className="flex items-center gap-3" title="Botly 工作空间">
            <LogoMark size={33} />
            {!narrow && <span className="text-[23px] font-semibold tracking-[-0.05em]">Botly</span>}
          </Link>
          {mobileNav && <button className="icon-btn ml-auto lg:hidden" aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={18} /></button>}
        </div>
        {!narrow && <div className="mx-3 mb-4 flex items-center gap-2.5 rounded-lg border border-line bg-panel2/40 px-3 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-panel2 text-accent"><Blocks size={16} /></span>
          <div className="min-w-0"><p className="truncate text-[12px] font-medium">我的工作空间</p><p className="mt-1 text-[10px] text-faint">AI 团队协作平台</p></div>
        </div>}
        <nav aria-label="主导航" className="min-h-0 flex-1 overflow-y-auto px-3">
          {NAV_GROUPS.map(group => <div key={group.label} className="mb-4">
            {!narrow && <p className="mb-2 px-3 text-[10px] font-medium tracking-wide text-faint">{group.label}</p>}
            {group.items.map(item => {
              const active = path.startsWith(item.href);
              const Icon = item.icon;
              return <Link key={item.href} href={item.href} title={item.label} aria-current={active ? "page" : undefined} className={`workspace-nav-link relative mb-1 flex items-center rounded-lg text-[12px] font-medium transition-colors ${narrow ? "justify-center px-0" : "gap-3 px-3"} ${active ? "text-ink" : "text-muted hover:bg-panel2 hover:text-ink"}`}>
                <Icon size={17} strokeWidth={1.7} />
                {!narrow && <span>{item.label}</span>}
                {item.href === "/notifications" && unread > 0 && <span className={`rounded bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent ${narrow ? "absolute right-0 top-0" : "ml-auto"}`}>{unread > 99 ? "99+" : unread}</span>}
              </Link>;
            })}
          </div>)}
        </nav>
        <div className="relative shrink-0 border-t border-line p-3">
          {isChat && <button className="icon-btn mb-2 hidden lg:inline-flex" onClick={toggle} aria-label={light ? "切换到深色" : "切换到浅色"}>{light ? <Moon size={17} /> : <Sun size={17} />}</button>}
          {userOpen && <>
            <button className="fixed inset-0 z-40 cursor-default" aria-label="关闭账户菜单" onClick={() => setUserOpen(false)} />
            <div role="menu" className="absolute bottom-full left-3 z-50 mb-2 w-56 rounded-xl border border-line bg-panel p-2 shadow-pop">
              <div className="mb-1 border-b border-line px-2 py-3"><p className="truncate text-[13px] font-medium">{me?.name || "账户"}</p><p className="mt-1 truncate text-[11px] text-faint">{me?.email || "—"}</p></div>
              <Link href="/settings" role="menuitem" className="flex items-center gap-2 rounded-lg px-3 py-2 text-[12px] text-muted hover:bg-panel2"><Settings size={15} />账户设置</Link>
              <button role="menuitem" onClick={async () => { setUserOpen(false); await api.logout().catch(() => {}); router.push("/login"); }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] text-danger hover:bg-danger/10"><LogOut size={15} />退出登录</button>
            </div>
          </>}
          <button onClick={() => setUserOpen(v => !v)} aria-label="账户菜单" aria-haspopup="menu" aria-expanded={userOpen} className={`relative flex w-full items-center rounded-lg py-1.5 hover:bg-panel2 ${narrow ? "justify-center" : "gap-2.5 px-2"}`}>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-accent/30 bg-accent/15 text-[12px] font-semibold text-accent">{me?.name?.slice(0, 1)?.toUpperCase() || <UserRound size={16} />}</span>
            {!narrow && <><span className="min-w-0 flex-1 text-left"><span className="block truncate text-[12px] font-medium">{me?.name || "我的账户"}</span><span className="mt-0.5 block truncate text-[10px] text-faint">{me?.email || "个人工作空间"}</span></span><ChevronDown size={14} className="text-faint" /></>}
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className={`workspace-topbar flex shrink-0 items-center gap-3 border-b border-line bg-panel px-4 sm:px-6 ${isChat ? "lg:hidden" : ""}`}>
          <button className="icon-btn lg:hidden" onClick={() => setMobileNav(true)} aria-label="打开主导航"><Menu size={19} /></button>
          <button className="icon-btn hidden lg:inline-flex" onClick={toggleCollapsed} aria-label={narrow ? "展开导航" : "收起导航"}>{narrow ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button>
          <span className="hidden h-4 w-px bg-line sm:block" />
          <nav aria-label="当前位置" className="flex min-w-0 items-center gap-2 text-[12px]">
            <span className="hidden text-faint sm:inline">工作空间</span><ChevronRight size={13} className="hidden text-faint sm:block" />
            <span className="truncate font-medium">{current?.label || "工作台"}</span>
          </nav>
          <div className="ml-auto flex items-center gap-1.5">
            <button className="icon-btn" onClick={toggle} aria-label={light ? "切换到深色" : "切换到浅色"}>{light ? <Moon size={17} /> : <Sun size={17} />}</button>
            <Link href="/notifications" aria-label={`通知${unread ? `，${unread} 条未读` : ""}`} className="icon-btn relative"><Bell size={17} />{unread > 0 && <span className="absolute right-2 top-1.5 h-1.5 w-1.5 rounded-full bg-accent ring-2 ring-panel" />}</Link>
            <Link href="/settings" aria-label="设置" className="icon-btn hidden sm:inline-flex"><Settings size={17} /></Link>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="workspace-main min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
