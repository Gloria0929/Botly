"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { acquireChatEvents, acquireNotificationEvents } from "@/lib/chat-events";
import type { Chat, ChatSummary } from "@/lib/types";
import type { RefObject, Dispatch, SetStateAction } from "react";

type Options = {
  chatId: string | null;
  eventStart: { chatId: string; threadId: string; after: number; } | null;
  me: any;
  setChats: Dispatch<SetStateAction<ChatSummary[]>>;
  chatRef: RefObject<Chat | null>;
  meRef: RefObject<any>;
  chatIdRef: RefObject<string | null>;
  openTarget: (target: { chatId: string; kind: string; id: string; }) => Promise<void>;
  pendingTarget: RefObject<{ chatId: string; kind: string; id: string; } | null>;
  setChatId: Dispatch<SetStateAction<string | null>>;
};

export function useChatNotifications({ chatId, eventStart, me, setChats, chatRef, meRef, chatIdRef, openTarget, pendingTarget, setChatId }: Options) {
  useEffect(() => {
    if (chatId && eventStart?.chatId === chatId) {
      const lease = acquireChatEvents(chatId, eventStart.after);
      const es = lease.source;
      const onNotification = (ev: Event) => handleNotification(ev as MessageEvent);
      es.addEventListener("notification.created", onNotification);
      return () => {
        es.removeEventListener("notification.created", onNotification);
        lease.release();
      };
    }
    if (chatId) return;
    if (!me?.id) return;
    const lease = acquireNotificationEvents(me.id);
    const es = lease.source;
    const onNotification = (ev: Event) => handleNotification(ev as MessageEvent);
    es.addEventListener("notification.created", onNotification);
    return () => {
      es.removeEventListener("notification.created", onNotification);
      lease.release();
    };

    function handleNotification(ev: MessageEvent) {
      const p = JSON.parse((ev as MessageEvent).data);
      if (p.type === "run_completed") {
        api.chats().then(setChats).catch(() => {});
      }
      // 审批提醒只走邮件；审批卡片由群聊事件刷新。
      if (p.type === "approval" || p.type === "approval_expired") return;
      // 当前正在查看的群聊不弹浏览器通知（NTF-05）
      const viewing = chatRef.current?.id === p.chat_id && document.visibilityState === "visible";
      if (viewing) return;
      if (meRef.current?.settings?.notifications_enabled !== false && typeof Notification !== "undefined" && Notification.permission === "granted") {
        try {
          const n = new Notification(p.title || "Botly", {
            body: p.body || "",
            tag: p.id,
          });
          n.onclick = () => {
            window.focus();
            if (p.chat_id) {
              const target = p.target_type && p.target_id ? { chatId: p.chat_id, kind: p.target_type, id: p.target_id } : null;
              if (chatIdRef.current === p.chat_id && target) openTarget(target);
              else { pendingTarget.current = target; setChatId(p.chat_id); }
            }
          };
        } catch {
          /* ignore */
        }
      }
    }
  }, [chatId, eventStart, openTarget, me?.id]);
}
