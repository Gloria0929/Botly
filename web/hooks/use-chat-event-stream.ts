"use client";

import { useEffect, useRef } from "react";
import { acceptMessageEvent, applyAgentUsage, mergeMessageDelta, mergeMessageUpdate } from "@/lib/message-sync";
import { acquireChatEvents, type ChatEventSource } from "@/lib/chat-events";
import { applyRunCompletion } from "@/lib/task-state";
import type { Chat, Message, Task, Branch } from "@/lib/types";
import type { RefObject, Dispatch, SetStateAction } from "react";
import type { MessageSync } from "@/lib/message-sync";

type Options = {
  hasActiveRuns?: boolean;
  invalidateChatSnapshot?: () => void;
  chatId: string | null;
  eventStart: { chatId: string; threadId: string; after: number; } | null;
  chatIdRef: RefObject<string | null>;
  refreshChat: (id: string) => void;
  reloadMessages: (id: string) => void;
  setConnection: Dispatch<SetStateAction<"connecting" | "connected" | "disconnected">>;
  branchRef: RefObject<Branch | null>;
  messageSync: RefObject<MessageSync>;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  setActiveTask: Dispatch<SetStateAction<Task | null>>;
  setChat: Dispatch<SetStateAction<Chat | null>>;
};

export function useChatEventStream({ chatId, eventStart, chatIdRef, refreshChat, reloadMessages, setConnection, branchRef, messageSync, setMessages, setActiveTask, setChat, hasActiveRuns = false, invalidateChatSnapshot }: Options) {
  const active = useRef(hasActiveRuns);
  active.current = hasActiveRuns;
  useEffect(() => {
    if (!chatId || eventStart?.chatId !== chatId) return;
    let es: ChatEventSource | null = null;
    let releaseEvents: (() => void) | null = null;
    let closed = false;
    let pending = false;
    let msgPending = false;
    let cursor = eventStart.after;

    const scheduleRefresh = () => {
      if (pending) return;
      pending = true;
      setTimeout(() => {
        pending = false;
        if (!closed && chatIdRef.current === chatId) refreshChat(chatId);
      }, 350);
    };

    // 卡片状态（计划/审批/提问）是在原消息上改 meta，必须重新拉一次消息才能反映
    const scheduleMessages = () => {
      if (msgPending) return;
      msgPending = true;
      setTimeout(() => {
        msgPending = false;
        if (!closed && chatIdRef.current === chatId) reloadMessages(chatId);
      }, 400);
    };

    const connect = () => {
      setConnection("connecting");
      const lease = acquireChatEvents(chatId, cursor);
      releaseEvents = lease.release;
      es = lease.source;
      es.onopen = () => {
        if (closed) return;
        setConnection("connected");
        // 首次连接和断线恢复时校准快照，避免握手期间的页面停留在旧状态。
        scheduleRefresh();
        scheduleMessages();
      };
      ["message.created", "message.delta", "message.completed", "message.deleted", "bot.typing", "run.status",
        "task.status", "approval.resolved", "files.updated", "usage.updated", "chat.always_allow", "chat.members"
      ].forEach((type) => es!.addEventListener(type, (ev) => {
        const id = Number((ev as MessageEvent).lastEventId);
        if (Number.isFinite(id)) cursor = Math.max(cursor, id);
      }));

      es.addEventListener("message.created", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        const mainId = branchRef.current?.id || eventStart.threadId;
        if (!mainId) {
          // 群聊详情还没加载完，先不动本地列表，等刷新补齐
          scheduleRefresh();
          return;
        }
        if (p.thread_id !== mainId) {
          scheduleRefresh();
          return;
        }
        if (!acceptMessageEvent(messageSync.current, p.id, Number((ev as MessageEvent).lastEventId))) return;
        setMessages((prev) => {
          const i = prev.findIndex((m) => m.id === p.id);
          if (i >= 0) {
            const next = [...prev];
            next[i] = mergeMessageUpdate(next[i], p);
            return next;
          }
          // 本机乐观插入的临时用户消息：SSE 先到时直接原位替换，
          // 避免“两条一模一样的消息”闪现一下再合并。
          const tempIndex = prev.findIndex(
            (m) =>
              m.id.startsWith("tmp-") &&
              m.sender_type === "user" &&
              m.content.trim() === (p.content || "").trim()
          );
          if (tempIndex >= 0) {
            const next = [...prev];
            next[tempIndex] = p as Message;
            return next.sort((a, b) => a.seq - b.seq);
          }
          return [...prev, p as Message].sort((a, b) => a.seq - b.seq);
        });
        if (p.type !== "text" && p.type !== "tool_call") scheduleRefresh();
      });

      es.addEventListener("message.delta", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        if (!acceptMessageEvent(messageSync.current, p.message_id, Number((ev as MessageEvent).lastEventId))) return;
        setMessages((prev) => {
          if (!prev.some((m) => m.id === p.message_id)) scheduleRefresh();
          return prev.map((m) => {
            if (m.id !== p.message_id) return m;
            const updated = mergeMessageDelta(m, p);
            if (!updated) scheduleRefresh();
            return updated || m;
          });
        });
      });

      es.addEventListener("message.completed", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        if (!acceptMessageEvent(messageSync.current, p.message_id, Number((ev as MessageEvent).lastEventId))) return;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === p.message_id
              ? mergeMessageUpdate(m, {
                  content: p.content,
                  status: p.status,
                  type: p.type || m.type,
                  meta: p.meta ? { ...m.meta, ...p.meta } : m.meta,
                })
              : m
          )
        );
      });

      es.addEventListener("message.deleted", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        if (!acceptMessageEvent(messageSync.current, p.id, Number((ev as MessageEvent).lastEventId), true)) return;
        setMessages((prev) => prev.filter((m) => m.id !== p.id));
      });

      es.addEventListener("run.status", (ev) => {
        const p = JSON.parse((ev as MessageEvent).data);
        if (closed || chatIdRef.current !== chatId) return;
        if (["completed", "failed", "stopped"].includes(p.status)) invalidateChatSnapshot?.();
        setMessages((prev) => prev.map((m) => m.run_id === p.run_id
          ? { ...m, meta: { ...m.meta, run_status: p.status } } : m));
        setChat((prev) => applyRunCompletion(prev, p));
        scheduleRefresh();
        scheduleMessages();
      });
      es.addEventListener("bot.typing", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setChat((prev) => {
          if (!prev || (prev.active_run?.id !== p.run_id && !prev.active_runs?.some((r) => r.id === p.run_id))) return prev;
          return { ...prev, members: prev.members.map((bot) => {
            if (bot.id !== p.bot_id) return bot;
            const ids = new Set(bot.typing_run_ids || []);
            if (p.typing) ids.add(p.run_id); else ids.delete(p.run_id);
            return { ...bot, typing_run_ids: Array.from(ids), status: ids.size ? "working" as const : "idle" as const };
          }) };
        });
      });
      es.addEventListener("task.status", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => prev.map((m) => m.meta?.task_id === p.task_id
          ? { ...m, meta: { ...m.meta, task_status: p.status } } : m));
        setActiveTask((t) => t && t.id === p.task_id ? { ...t, ...p, id: t.id } : t);
        // 群聊里挂着的任务详情直接读 chat.tasks，这里原地更新，token/状态不用等重拉
        setChat((prev) => prev ? {
          ...prev,
          tasks: prev.tasks.map((t) => t.id === p.task_id ? {
            ...t,
            status: p.status,
            tokens_used: p.tokens_used,
            attempt: p.attempt,
            started_at: p.started_at,
            ended_at: p.ended_at,
            result: p.result,
          } : t),
        } : prev);
      });
      es.addEventListener("approval.resolved", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => prev.map((m) => m.meta?.approval_id === p.approval_id
          ? { ...m, meta: { ...m.meta, status: p.status, closed_reason: p.closed_reason } } : m));
        scheduleRefresh();
        scheduleMessages();
      });
      es.addEventListener("usage.updated", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => applyAgentUsage(prev, p));
      });
      ["task.status", "files.updated", "usage.updated", "chat.always_allow"].forEach((t) =>
        es!.addEventListener(t, scheduleRefresh)
      );
      // 成员变化后历史消息里的「已删除」标记也要跟着更新，所以连消息一起重拉
      es.addEventListener("chat.members", () => {
        scheduleRefresh();
        scheduleMessages();
      });

      es.onerror = () => {
        if (closed) return;
        setConnection("disconnected");
      };
    };

    const resync = () => {
      scheduleRefresh();
      scheduleMessages();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") resync();
    };
    window.addEventListener("online", resync);
    document.addEventListener("visibilitychange", onVisible);
    const reconcile = setInterval(() => {
      if (active.current && document.visibilityState === "visible") resync();
    }, 5000);
    connect();
    return () => {
      closed = true;
      clearInterval(reconcile);
      releaseEvents?.();
      window.removeEventListener("online", resync);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [chatId, eventStart, refreshChat, reloadMessages]);
}
