"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BellRing, CheckCheck, CircleAlert, CircleCheck, CircleHelp, Loader2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import type { Notification } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";

const ICON: Record<string, any> = {
  approval: CircleAlert,
  approval_expired: CircleAlert,
  question: CircleHelp,
  run_completed: CircleCheck,
  run_failed: CircleAlert,
};

export default function NotificationsPage() {
  const router = useRouter();
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api
      .notifications()
      .then((d) => {
        setItems(d.items);
        setUnread(d.unread_count);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // 打开通知中心时也要实时出现新审批/提醒（NTF-02）
  useEffect(() => {
    const es = new EventSource("/api/notifications/events");
    const refresh = () => {
      api
        .notifications()
        .then((d) => {
          setItems(d.items);
          setUnread(d.unread_count);
        })
        .catch(() => {});
    };
    es.addEventListener("notification.created", refresh);
    return () => es.close();
  }, []);

  const markAll = async () => {
    await api.readNotifications({ all: true }).catch(() => {});
    load();
  };

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-2xl px-6 py-8">
          <div className="flex items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">通知</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                审批、提问、执行完成与失败都会出现在这里。
              </p>
            </div>
            <button className="btn-ghost ml-auto" onClick={markAll} disabled={!unread}>
              <CheckCheck size={13} /> 全部已读
            </button>
          </div>

          {loading && <Loader2 size={15} className="mt-8 animate-spin text-faint" />}

          {!loading && !items.length && (
            <div className="mt-16 flex flex-col items-center text-faint">
              <BellRing size={26} strokeWidth={1.4} />
              <p className="mt-3 text-[12.5px]">暂无通知</p>
            </div>
          )}

          <div className="mt-5 space-y-1.5">
            {items.map((n) => {
              const Icon = ICON[n.type] || BellRing;
              return (
                <button
                  key={n.id}
                  onClick={async () => {
                    if (!n.read) await api.readNotifications({ ids: [n.id] }).catch(() => {});
                    if (n.chat_id) {
                      const params = new URLSearchParams({ chat: n.chat_id });
                      if (n.target_type && n.target_id) {
                        params.set("target", n.target_type);
                        params.set("id", n.target_id);
                      }
                      router.push(`/chat?${params}`);
                    }
                    load();
                  }}
                  className={`flex w-full items-start gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors ${
                    n.read
                      ? "border-line bg-transparent hover:bg-panel"
                      : "border-accent/25 bg-accent/[0.05] hover:bg-accent/[0.09]"
                  }`}
                >
                  <Icon
                    size={15}
                    className={`mt-0.5 shrink-0 ${
                      n.type === "run_failed" || n.type === "approval_expired"
                        ? "text-danger"
                        : n.type === "approval"
                          ? "text-warn"
                          : "text-ok"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[12.5px] font-medium">{n.title}</span>
                      {!n.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}
                      <span className="ml-auto shrink-0 text-[10.5px] text-faint">
                        {timeAgo(n.created_at)}
                      </span>
                    </div>
                    {n.body && (
                      <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-muted">
                        {n.body}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
