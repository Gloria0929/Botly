"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BellRing, CheckCheck, CircleAlert, CircleCheck, CircleHelp, Loader2, Trash2 } from "lucide-react";
import AppShell from "@/components/AppShell";
import PageHeader from "@/components/ui/PageHeader";
import { useConfirm } from "@/components/ui/Confirm";
import { api, toastError } from "@/lib/api";
import type { Notification } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

const ICON: Record<string, any> = {
  approval: CircleAlert,
  approval_expired: CircleAlert,
  question: CircleHelp,
  run_completed: CircleCheck,
  run_failed: CircleAlert,
};

export default function NotificationsPage() {
  const router = useRouter();
  const confirm = useConfirm();
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [marking, setMarking] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .notifications()
      .then((d) => {
        setItems(d.items);
        setUnread(d.unread_count);
      })
      .catch((e) => toastError(e, "通知加载失败"))
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
    setMarking(true);
    try {
      await api.readNotifications({ all: true });
      setItems((prev) => prev.map((item) => ({ ...item, read: true })));
      setUnread(0);
      load();
    } catch (e) {
      toastError(e, "标记已读失败");
    } finally {
      setMarking(false);
    }
  };

  /** 清空通知：只删通知记录，执行记录与群聊消息不受影响 */
  const clearAll = async () => {
    const ok = await confirm({
      title: "清空全部通知？",
      description: "已读和未读的通知都会被删除，无法恢复；群聊消息与执行记录不受影响。",
      confirmText: "清空",
      danger: true,
    });
    if (!ok) return;
    setClearing(true);
    try {
      const { count } = await api.clearNotifications();
      setItems([]);
      setUnread(0);
      if (count > 0) toast(`已清空 ${count} 条通知`, "ok");
    } catch {
      /* 失败提示由 api 统一弹出 */
    } finally {
      setClearing(false);
    }
  };

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <PageHeader
            eyebrow="INBOX / NOTIFICATIONS"
            title="通知"
            description="审批、提问、执行完成与失败都会出现在这里。"
            actions={
              <>
                <button className="btn-ghost" onClick={markAll} disabled={!unread || marking || clearing}>
                  {marking ? <Loader2 size={13} className="animate-spin" /> : <CheckCheck size={13} />} 全部已读
                </button>
                <button
                  className="btn-ghost hover:border-danger/40 hover:text-danger"
                  onClick={clearAll}
                  disabled={!items.length || clearing || marking}
                  title="清空全部通知"
                >
                  {clearing ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />} 清空
                </button>
              </>
            }
          />

          {loading && <Loader2 size={15} className="mt-8 animate-spin text-faint" />}

          {!loading && !items.length && (
            <div className="empty-state mt-10">
              <div className="empty-icon">
                <BellRing size={20} />
              </div>
              <div className="mt-3 text-[13px] font-medium">暂无通知</div>
              <p className="mx-auto mt-1 max-w-xs text-[12px] leading-relaxed text-muted">
                审批、提问或执行状态变化时，提醒会出现在这里。
              </p>
            </div>
          )}

          <div className="mt-5 grid gap-2 lg:grid-cols-2">
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
                  className={`flex w-full items-start gap-3 rounded-lg border px-3.5 py-3 text-left transition-all ${
                    n.read
                      ? "border-line bg-panel/40 hover:border-line2 hover:bg-panel"
                      : "border-accent/25 bg-accent/[0.05] hover:border-accent/40 hover:bg-accent/[0.08]"
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
