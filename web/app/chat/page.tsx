"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useSearchParams } from "next/navigation";
import {
  Bell,
  BellOff,
  Columns2,
  Download,
  Loader2,
  Menu as MenuIcon,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Square,
  Trash2,
  Users,
  Workflow,
  Zap,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import ChatList from "@/components/chat/ChatList";
import ChatSettings from "@/components/chat/ChatSettings";
import Composer from "@/components/chat/Composer";
import MembersModal from "@/components/chat/MembersModal";
import MessageStream from "@/components/chat/MessageStream";
import SearchModal from "@/components/chat/SearchModal";
import SidePanel, { FilePreview } from "@/components/chat/SidePanel";
import Avatar from "@/components/ui/Avatar";
import ActionMenu from "@/components/ui/Menu";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api } from "@/lib/api";
import type { Chat, ChatFile, ChatSummary, Message, PlanStep, Run, Task } from "@/lib/types";
import { num } from "@/lib/fmt";
import { toast } from "@/lib/ui";

const ACTIVE = new Set(["queued", "running", "waiting"]);

export default function ChatPage() {
  return <Suspense fallback={<div className="flex h-screen items-center justify-center bg-canvas"><Loader2 className="animate-spin text-muted" size={20} /></div>}><ChatContent /></Suspense>;
}

function ChatContent() {
  const searchParams = useSearchParams();
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatsLoading, setChatsLoading] = useState(true);
  const [chatId, setChatId] = useState<string | null>(null);
  const [chat, setChat] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [eventStart, setEventStart] = useState<{ chatId: string; threadId: string; after: number } | null>(null);
  const [me, setMe] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState(true);
  const [mobilePanel, setMobilePanel] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [notifyPerm, setNotifyPerm] = useState<string>("default");
  const [seed, setSeed] = useState<{ text: string; k: number } | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [membersOpen, setMembersOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [previewFile, setPreviewFile] = useState<ChatFile | null>(null);
  const [stepNote, setStepNote] = useState("");
  const [hasEarlier, setHasEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [focusMessage, setFocusMessage] = useState<string | null>(null);
  const [connection, setConnection] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [panelWidth, setPanelWidth] = useState(320);
  const panelWidthRef = useRef(320);
  const panelResizing = useRef(false);
  const panelResizeStart = useRef({ x: 0, w: 320 });
  const confirm = useConfirm();

  useEffect(() => {
    panelWidthRef.current = panelWidth;
  }, [panelWidth]);

  const maxPanelWidth = useCallback(() => {
    if (typeof window === "undefined") return 720;
    // 左侧导航 68 + 群聊列表 248 + 中间至少 360，剩余空间给右侧面板
    return Math.max(280, Math.min(760, window.innerWidth - 68 - 248 - 360));
  }, []);

  const clampPanelWidth = useCallback(
    (w: number) => Math.min(maxPanelWidth(), Math.max(260, Math.round(w))),
    [maxPanelWidth]
  );

  useEffect(() => {
    const saved = Number(localStorage.getItem("botly-panel-width"));
    if (Number.isFinite(saved) && saved >= 260) {
      const w = clampPanelWidth(saved);
      panelWidthRef.current = w;
      setPanelWidth(w);
    }
    const onResize = () => setPanelWidth((w) => {
      const next = clampPanelWidth(w);
      panelWidthRef.current = next;
      return next;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clampPanelWidth]);

  const startPanelResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    panelResizing.current = true;
    panelResizeStart.current = { x: e.clientX, w: panelWidth };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  const movePanelResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!panelResizing.current) return;
    const next = panelResizeStart.current.w + (panelResizeStart.current.x - e.clientX);
    const w = clampPanelWidth(next);
    panelWidthRef.current = w;
    setPanelWidth(w);
  };

  const endPanelResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!panelResizing.current) return;
    panelResizing.current = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer 已经释放 */
    }
    localStorage.setItem("botly-panel-width", String(panelWidthRef.current));
  };

  const resetPanelWidth = () => {
    const w = clampPanelWidth(320);
    panelWidthRef.current = w;
    setPanelWidth(w);
    localStorage.setItem("botly-panel-width", String(w));
  };

  const onPanelKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    let next = panelWidth;
    if (e.key === "ArrowLeft") next += 16;
    else if (e.key === "ArrowRight") next -= 16;
    else if (e.key === "Home") next = 320;
    else return;
    e.preventDefault();
    const w = clampPanelWidth(next);
    panelWidthRef.current = w;
    setPanelWidth(w);
    localStorage.setItem("botly-panel-width", String(w));
  };

  const chatRef = useRef<Chat | null>(null);
  chatRef.current = chat;
  const chatIdRef = useRef<string | null>(chatId);
  chatIdRef.current = chatId;
  const meRef = useRef(me);
  meRef.current = me;
  const focusRef = useRef<string | null>(null);
  const pendingTarget = useRef<{ chatId: string; kind: string; id: string } | null>(null);
  const handledTarget = useRef("");
  const navigationVersion = useRef(0);

  /** 跳到某条消息：加载它附近的上下文并高亮。返回是否仍是当前导航。 */
  const loadAround = useCallback(async (messageId: string) => {
    const cid = chatIdRef.current;
    if (!cid) return false;
    const version = ++navigationVersion.current;
    focusRef.current = messageId;
    const history = await api.messages(cid, { around: messageId, limit: 80 });
    if (chatIdRef.current !== cid || version !== navigationVersion.current) return false;
    setMessages(history.messages);
    setHasEarlier(history.has_more);
    setFocusMessage(messageId);
    return true;
  }, []);

  const openTarget = useCallback(async (target: { chatId: string; kind: string; id: string }) => {
    const version = ++navigationVersion.current;
    setFocusMessage(null);
    try {
      const resolved = await api.messageTarget(target.chatId, target.kind, target.id);
      if (chatIdRef.current !== target.chatId || version !== navigationVersion.current) return;
      if (!(await loadAround(resolved.message_id))) return;
      if (resolved.task_id) {
        const current = navigationVersion.current;
        const task = await api.task(resolved.task_id);
        if (chatIdRef.current !== target.chatId || current !== navigationVersion.current) return;
        setActiveTask(task);
        setPanel(true);
        localStorage.setItem("botly-panel", "1");
        if (window.innerWidth < 1024) setMobilePanel(true);
      }
    } catch (error: any) {
      if (chatIdRef.current === target.chatId) {
        focusRef.current = null;
        toast(error.message, "error");
      }
    }
  }, [loadAround]);

  /** CHT-08：搜索结果点进来时跳到那条消息。 */
  const jumpToMessage = useCallback(
    async (messageId: string) => {
      try {
        await loadAround(messageId);
      } catch (error: any) {
        focusRef.current = null;
        toast(error.message, "error");
      }
    },
    [loadAround]
  );

  /* ---------------- 初始化 ---------------- */
  // 右侧面板的展开状态记住上次选择
  useEffect(() => {
    setPanel(localStorage.getItem("botly-panel") !== "0");
  }, []);

  const setPanelPersist = (v: boolean) => {
    setPanel(v);
    localStorage.setItem("botly-panel", v ? "1" : "0");
  };

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    if (typeof Notification !== "undefined") setNotifyPerm(Notification.permission);
    api
      .chats()
      .then((cs) => {
        setChats(cs);
        const saved = localStorage.getItem("botly-chat");
        const requested = new URLSearchParams(window.location.search).get("chat");
        const pick = cs.find((c) => c.id === requested)?.id || cs.find((c) => c.id === saved)?.id || cs[0]?.id || null;
        setChatId(pick);
      })
      .catch(() => {})
      .finally(() => setChatsLoading(false));
  }, []);

  const requestedChat = searchParams.get("chat");
  const requestedKind = searchParams.get("target");
  const requestedId = searchParams.get("id");
  useEffect(() => {
    if (!requestedChat || !chats.some((c) => c.id === requestedChat)) return;
    const key = `${requestedChat}:${requestedKind}:${requestedId}`;
    if (key === handledTarget.current) return;
    handledTarget.current = key;
    pendingTarget.current = requestedKind && requestedId ? { chatId: requestedChat, kind: requestedKind, id: requestedId } : null;
    if (chatIdRef.current !== requestedChat) setChatId(requestedChat);
    else if (chatRef.current?.id === requestedChat && pendingTarget.current) {
      const target = pendingTarget.current;
      pendingTarget.current = null;
      openTarget(target);
    }
  }, [requestedChat, requestedKind, requestedId, chats.length, openTarget]);

  const reloadMessages = useCallback((id: string) => {
    const anchor = focusRef.current;
    api
      .messages(id, { limit: 80, around: anchor || undefined })
      .then((d) => {
        if (chatIdRef.current !== id || anchor !== focusRef.current) return;
        if (anchor) setMessages(d.messages);
        else setMessages((prev) => {
          const first = d.messages[0]?.seq ?? 0;
          const older = prev.filter((m) => m.seq < first && m.thread_id === d.messages[0]?.thread_id);
          return [...older, ...d.messages];
        });
      })
      .catch(() => {});
  }, []);

  const refreshChat = useCallback((id: string) => {
    api
      .chat(id)
      .then((c) => {
        if (chatIdRef.current !== id) return;
        setChat(c);
      })
      .catch(() => {});
  }, []);

  /* ---------------- 切换群聊 ---------------- */
  useEffect(() => {
    if (!chatId) return;
    let canceled = false;
    localStorage.setItem("botly-chat", chatId);
    setChat(null);
    setMessages([]);
    setMessagesLoading(true);
    navigationVersion.current += 1;
    focusRef.current = null;
    setFocusMessage(null);
    setHasEarlier(false);
    setLoadingEarlier(false);
    setEventStart(null);
    setActiveTask(null);
    setPreviewFile(null);
    setStepNote("");
    setMembersOpen(false);
    setSettingsOpen(false);
    setSearchOpen(false);
    // 消息快照就绪后再订阅增量，避免 SSE 从头重放旧消息和旧流式片段。
    Promise.all([api.chat(chatId), api.messages(chatId, { limit: 80 })])
      .then(([currentChat, history]) => {
        if (canceled || chatIdRef.current !== chatId) return;
        setChat(currentChat);
        setMessages(history.messages);
        setHasEarlier(history.has_more);
        if (currentChat.main_thread_id) setEventStart({ chatId, threadId: currentChat.main_thread_id, after: history.event_cursor });
        if (pendingTarget.current?.chatId === chatId) {
          const target = pendingTarget.current;
          pendingTarget.current = null;
          openTarget(target);
        }
      })
      .catch((e) => {
        if (!canceled) toast(e.message, "error");
      })
      .finally(() => {
        if (!canceled && chatIdRef.current === chatId) setMessagesLoading(false);
      });
    return () => { canceled = true; };
  }, [chatId, openTarget]);

  /* ---------------- SSE ---------------- */
  useEffect(() => {
    if (!chatId || eventStart?.chatId !== chatId) return;
    let es: EventSource | null = null;
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
      es = new EventSource(`/api/chats/${chatId}/events?after=${cursor}`);
      es.onopen = () => {
        if (closed) return;
        setConnection("connected");
        // 首次连接和断线恢复时校准快照，避免握手期间的页面停留在旧状态。
        scheduleRefresh();
        scheduleMessages();
      };
      ["message.created", "message.delta", "message.completed", "message.deleted", "run.status",
        "task.status", "approval.resolved", "files.updated", "usage.updated", "chat.always_allow", "chat.members"
      ].forEach((type) => es!.addEventListener(type, (ev) => {
        const id = Number((ev as MessageEvent).lastEventId);
        if (Number.isFinite(id)) cursor = Math.max(cursor, id);
      }));

      es.addEventListener("message.created", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        const mainId = eventStart.threadId;
        if (!mainId) {
          // 群聊详情还没加载完，先不动本地列表，等刷新补齐
          scheduleRefresh();
          return;
        }
        if (p.thread_id !== mainId) {
          scheduleRefresh();
          return;
        }
        setMessages((prev) => {
          const i = prev.findIndex((m) => m.id === p.id);
          if (i >= 0) {
            const next = [...prev];
            next[i] = { ...next[i], ...p };
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
        setMessages((prev) =>
          prev.map((m) => (m.id === p.message_id ? { ...m, content: p.content } : m))
        );
      });

      es.addEventListener("message.completed", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === p.message_id
              ? {
                  ...m,
                  content: p.content,
                  status: p.status,
                  meta: p.meta ? { ...m.meta, ...p.meta } : m.meta,
                }
              : m
          )
        );
      });

      es.addEventListener("message.deleted", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => prev.filter((m) => m.id !== p.id));
      });

      es.addEventListener("run.status", (ev) => {
        const p = JSON.parse((ev as MessageEvent).data);
        if (closed || chatIdRef.current !== chatId) return;
        setMessages((prev) => prev.map((m) => m.run_id === p.run_id
          ? { ...m, meta: { ...m.meta, run_status: p.status } } : m));
        scheduleRefresh();
        scheduleMessages();
      });
      es.addEventListener("task.status", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => prev.map((m) => m.meta?.task_id === p.task_id
          ? { ...m, meta: { ...m.meta, task_status: p.status } } : m));
        setActiveTask((t) => t && t.id === p.task_id ? { ...t, ...p, id: t.id } : t);
      });
      es.addEventListener("approval.resolved", (ev) => {
        if (closed || chatIdRef.current !== chatId) return;
        const p = JSON.parse((ev as MessageEvent).data);
        setMessages((prev) => prev.map((m) => m.meta?.approval_id === p.approval_id
          ? { ...m, meta: { ...m.meta, status: p.status, closed_reason: p.closed_reason } } : m));
        scheduleRefresh();
        scheduleMessages();
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
        es?.close();
        setTimeout(() => !closed && connect(), 1500);
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
    connect();
    return () => {
      closed = true;
      es?.close();
      window.removeEventListener("online", resync);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [chatId, eventStart, refreshChat, reloadMessages]);

  /* ---------------- 用户级通知 ---------------- */
  useEffect(() => {
    const es = new EventSource("/api/notifications/events");
    es.addEventListener("notification.created", (ev) => {
      const p = JSON.parse((ev as MessageEvent).data);
      if (p.type === "run_completed") {
        api.chats().then(setChats).catch(() => {});
      }
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
      if (p.type === "approval") toast(p.title, "info");
    });
    return () => es.close();
  }, [openTarget]);

  /* ---------------- 操作 ---------------- */
  const run: Run | null = chat?.active_run || chat?.last_run || null;
  const running = !!run && ACTIVE.has(run.status);
  const waiting = run?.status === "waiting";
  const resumeKind = run?.resume?.kind;

  const doStop = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await api.stop(run.id);
      toast("已停止", "ok");
      refreshChat(run.chat_id);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const send = async (content: string) => {
    if (!chatId) return;
    const temp: Message = {
      id: `tmp-${Date.now()}`,
      thread_id: chat?.main_thread_id || "",
      run_id: null,
      seq: 1e9,
      sender_type: "user",
      sender_bot_id: null,
      type: "text",
      content,
      meta: {},
      status: "done",
      created_at: new Date().toISOString(),
    };
    setMessages((p) => [...p, temp]);
    try {
      const r = await api.send(chatId, content);
      if (chatIdRef.current !== chatId) return;
      // SSE 可能已经先推送了这条消息，替换临时消息时要去重，否则会出现重复 key
      setMessages((prev) => {
        const rest = prev.filter((m) => m.id !== temp.id && m.id !== r.message.id);
        return [...rest, r.message].sort((a, b) => a.seq - b.seq);
      });
      refreshChat(chatId);
      api.chats().then(setChats).catch(() => {});
    } catch (e: any) {
      setMessages((p) => p.filter((m) => m.id !== temp.id));
      toast(e.message, "error");
    }
  };

  /** RUN-13：执行中补充，不新建执行 */
  const interject = async (content: string) => {
    if (!chatId || !run) return;
    const temp: Message = {
      id: `tmp-i-${Date.now()}`,
      thread_id: chat?.main_thread_id || "",
      run_id: run.id,
      seq: 1e9,
      sender_type: "user",
      sender_bot_id: null,
      type: "text",
      content,
      meta: { kind: "interjection" },
      status: "done",
      created_at: new Date().toISOString(),
    };
    setMessages((p) => [...p, temp]);
    try {
      const r = await api.interject(chatId, content);
      if (chatIdRef.current !== chatId) return;
      setMessages((prev) => {
        const rest = prev.filter((m) => m.id !== temp.id && m.id !== r.message.id);
        return [...rest, r.message].sort((a, b) => a.seq - b.seq);
      });
      toast("已送达，总指挥会在下一步前读到", "ok");
    } catch (e: any) {
      setMessages((p) => p.filter((m) => m.id !== temp.id));
      toast(e.message, "error");
    }
  };

  const act = async (fn: () => Promise<any>, okMsg?: string) => {
    setBusy(true);
    try {
      await fn();
      if (okMsg) toast(okMsg, "ok");
      if (chatId) refreshChat(chatId);
      return true;
    } catch (e: any) {
      toast(e.message, "error");
      if (chatId) {
        refreshChat(chatId);
        reloadMessages(chatId);
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onPlanApprove = (msg: Message, steps: PlanStep[]) =>
    msg.run_id && act(() => api.approvePlan(msg.run_id!, steps), "已开始执行");

  const onPlanCancel = (msg: Message) => msg.run_id && act(() => api.cancelPlan(msg.run_id!), "已取消计划");

  const onApproval = (msg: Message, approve: boolean, reason?: string, alwaysAllow?: boolean) =>
    act(() =>
      approve
        ? api.approve(msg.meta.approval_id, alwaysAllow)
        : api.reject(msg.meta.approval_id, reason)
    );

  const onAnswer = (msg: Message, answer: string) =>
    act(() => api.answer(msg.meta.question_id, answer), "已回复");

  const onOpenTask = async (taskId: string, threadId: string) => {
    const selectedChat = chatId;
    const t = chat?.tasks.find((x) => x.id === taskId);
    try {
      const task = t || await api.task(taskId);
      if (chatIdRef.current !== selectedChat) return;
      setActiveTask(task);
      setPanelPersist(true);
      if (window.innerWidth < 1024) setMobilePanel(true);
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const dissolve = async () => {
    if (!chat) return;
    const ok = await confirm({
      title: `解散「${chat.name}」？`,
      description: "该群聊的全部消息、子任务、产物文件与运行记录都会被删除，且无法恢复。",
      confirmText: "解散",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteChat(chat.id);
      toast("群聊已解散", "ok");
      const rest = await api.chats();
      setChats(rest);
      setChatId(rest[0]?.id || null);
      if (!rest.length) {
        setChat(null);
        setMessages([]);
      }
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const exportChat = async () => {
    if (!chat) return;
    try {
      const blob = await api.exportChat(chat.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${chat.name}-${new Date().toISOString().slice(0, 10)}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("已导出 Markdown", "ok");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const doRename = async () => {
    if (!chat) return;
    const name = renameValue.trim();
    if (!name) return toast("名称不能为空", "error");
    try {
      await api.patchChat(chat.id, { name });
      setRenameOpen(false);
      refreshChat(chat.id);
      api.chats().then(setChats).catch(() => {});
      toast("已重命名", "ok");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const requestNotify = async () => {
    if (typeof Notification === "undefined") return toast("当前浏览器不支持系统通知", "error");
    const p = await Notification.requestPermission();
    setNotifyPerm(p);
    if (p === "granted") toast("已开启浏览器通知", "ok");
    else toast("已拒绝，审批会显示在页面内通知中心", "info");
  };

  const disabledReason = useMemo(() => {
    if (!chatId) return "还没有群聊，先在左侧新建一个";
    if (me && !me.has_provider) return "尚未配置模型接入，请先到设置页添加厂商并填写 API Key";
    return null;
  }, [me, chatId]);

  const activeTasks = chat?.tasks || [];
  const openApproval = (id: string) => {
    if (!chatId) return;
    setMobilePanel(false);
    openTarget({ chatId, kind: "approval", id });
  };
  const loadEarlier = async () => {
    if (!chatId || !messages.length || loadingEarlier) return;
    const id = chatId;
    setLoadingEarlier(true);
    try {
      const history = await api.messages(id, { before: messages[0].seq, limit: 50 });
      if (chatIdRef.current !== id) return;
      setMessages((prev) => [...history.messages.filter((m) => !prev.some((old) => old.id === m.id)), ...prev]);
      setHasEarlier(history.has_more);
    } catch (error: any) { toast(error.message, "error"); }
    finally { if (chatIdRef.current === id) setLoadingEarlier(false); }
  };
  const returnToLatest = async () => {
    if (!chatId) return;
    const id = chatId;
    navigationVersion.current += 1;
    focusRef.current = null;
    setFocusMessage(null);
    try {
      const history = await api.messages(id, { limit: 80 });
      if (chatIdRef.current !== id) return;
      setMessages(history.messages);
      setHasEarlier(history.has_more);
    } catch (error: any) { toast(error.message, "error"); }
  };
  const selectedTask = activeTask ? activeTasks.find((t) => t.id === activeTask.id) || activeTask : null;

  return (
    <AppShell>
      <div className="flex h-full min-h-0">
        {/* 左：群聊列表 */}
        <aside className="hidden w-[248px] shrink-0 lg:block">
          <ChatList
            chats={chats}
            loading={chatsLoading}
            activeId={chatId}
            onSelect={setChatId}
            onCreated={(id) => {
              setChats((c) => c);
              setChatId(id);
              api.chats().then(setChats).catch(() => {});
            }}
          />
        </aside>

        {/* 移动端抽屉 */}
        {listOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setListOpen(false)} />
            <div className="absolute left-0 top-0 h-full w-[280px]">
              <ChatList
                chats={chats}
                loading={chatsLoading}
                activeId={chatId}
                onSelect={(id) => {
                  setChatId(id);
                  setListOpen(false);
                }}
                onCreated={(id) => {
                  setChatId(id);
                  setListOpen(false);
                  api.chats().then(setChats).catch(() => {});
                }}
                onClose={() => setListOpen(false)}
              />
            </div>
          </div>
        )}

        {/* 中：消息流 */}
        <section className="flex min-w-0 flex-1 flex-col bg-canvas">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-panel/60 px-3 backdrop-blur sm:px-4">
            <button
              onClick={() => setListOpen(true)}
              className="p-1.5 text-muted hover:text-ink lg:hidden"
            >
              <MenuIcon size={17} />
            </button>
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium">{chat?.name || "…"}</div>
            </div>

            <button
              onClick={() => chat && setMembersOpen(true)}
              title="群聊成员"
              className="group ml-2 hidden shrink-0 items-center gap-1.5 rounded-lg px-1 py-1 transition-colors hover:bg-panel2 sm:flex"
            >
              {(chat?.members || []).slice(0, 5).map((m) => (
                <span
                  key={m.id}
                  title={`${m.name} · ${statusLabel(m.status)}`}
                  className="relative"
                >
                  <Avatar value={m.avatar} size={24} alt={m.name} />
                  <span
                    className={`absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full ring-2 ring-panel ${
                      m.status === "working"
                        ? "animate-pulseSoft bg-accent"
                        : m.status === "waiting"
                          ? "bg-warn"
                          : "bg-faint/50"
                    }`}
                  />
                </span>
              ))}
              {chat && chat.members.length > 5 && (
                <span className="text-[10.5px] text-faint">+{chat.members.length - 5}</span>
              )}
              {chat && (
                <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-dashed border-line text-faint transition-colors group-hover:border-accent/50 group-hover:text-accent">
                  <Plus size={12} />
                </span>
              )}
            </button>

            <div className="ml-auto flex items-center gap-1.5">
              {run && (
                <span className="hidden items-center gap-1 text-[10.5px] text-faint sm:flex">
                  <Zap size={10} /> {num(run.tokens_used)} tokens · {run.steps_used} 步
                </span>
              )}
              {chat && (
                <button
                  onClick={() =>
                    act(
                      () =>
                        api.patchChat(chat.id, {
                          run_mode: chat.run_mode === "auto" ? "step" : "auto",
                        }),
                      chat.run_mode === "auto" ? "已切换到逐步确认" : "已切换到自动模式"
                    )
                  }
                  title="切换运行模式"
                  className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[10.5px] ${
                    chat.run_mode === "step"
                      ? "border-warn/40 text-warn"
                      : "border-line text-muted"
                  }`}
                >
                  <Workflow size={11} />
                  {chat.run_mode === "step" ? "逐步" : "自动"}
                </button>
              )}
              {chat && (
                <ActionMenu
                  items={[
                    { label: "群聊成员", icon: Users, onClick: () => setMembersOpen(true) },
                    {
                      label: "群聊设置",
                      icon: SlidersHorizontal,
                      onClick: () => setSettingsOpen(true),
                    },
                    {
                      label: "重命名群聊",
                      icon: Pencil,
                      onClick: () => {
                        setRenameValue(chat.name);
                        setRenameOpen(true);
                      },
                    },
                    { label: "导出聊天记录", icon: Download, onClick: exportChat },
                    { label: "解散群聊", icon: Trash2, danger: true, onClick: dissolve },
                  ]}
                />
              )}
              <button
                onClick={() => setSearchOpen(true)}
                title="搜索消息"
                className="p-1.5 text-faint hover:text-ink"
              >
                <Search size={15} />
              </button>
              <button
                onClick={requestNotify}
                title={notifyPerm === "granted" ? "浏览器通知已开启" : "开启浏览器通知"}
                className={`p-1.5 ${notifyPerm === "granted" ? "text-ok" : "text-faint hover:text-ink"}`}
              >
                {notifyPerm === "granted" ? <Bell size={15} /> : <BellOff size={15} />}
              </button>
              <button
                onClick={() => setPanelPersist(!panel)}
                title={panel ? "收起任务与文件面板" : "展开任务与文件面板"}
                className={`hidden p-1.5 lg:block ${panel ? "text-accent" : "text-faint hover:text-ink"}`}
              >
                <Columns2 size={15} />
              </button>
              <button
                onClick={() => setMobilePanel(true)}
                title="任务与文件"
                className="relative p-1.5 text-faint hover:text-ink lg:hidden"
              >
                <Columns2 size={15} />
                {activeTasks.length > 0 && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-3 min-w-3 items-center justify-center rounded-full bg-accent px-0.5 text-[8px] font-medium text-accentink">
                    {activeTasks.length}
                  </span>
                )}
              </button>
            </div>
          </header>

          {chatId && connection === "disconnected" && <div className="border-b border-warn/20 bg-warn/5 px-4 py-2 text-[11.5px] text-warn">连接已断开，正在重连，恢复后会自动补齐消息。</div>}
          {focusMessage && <div className="flex items-center gap-2 border-b border-line bg-accent/5 px-4 py-1.5 text-[11.5px] text-muted">
            正在查看消息上下文<button className="ml-auto text-accent hover:underline" onClick={returnToLatest}>返回最新消息</button>
          </div>}
          {run?.status === "failed" && run.end_reason && (
            <div className="border-b border-danger/20 bg-danger/[0.06] px-4 py-1.5 text-[11.5px] text-danger/90">
              执行失败：{run.end_reason}
              {run.end_reason.includes("模型") && <a className="ml-2 underline" href="/settings">检查模型接入</a>}
            </div>
          )}

          <MessageStream
            chatId={chatId || ""}
            messages={messages}
            allMessages={messages}
            members={chat?.members || []}
            tasks={activeTasks}
            files={chat?.files || []}
            hasEarlier={hasEarlier}
            loadingEarlier={loadingEarlier}
            onLoadEarlier={loadEarlier}
            focusMessageId={focusMessage}
            me={me}
            handlers={{
              onPlanApprove,
              onPlanCancel,
              onApproval,
              onAnswer,
              onOpenTask,
              onOpenFile: setPreviewFile,
              busy,
            }}
            emptyState={
              !chatId ? (
                <NoChatState onOpenList={() => setListOpen(true)} />
              ) : messagesLoading ? (
                <div className="flex h-full items-center justify-center">
                  <Loader2 size={18} className="animate-spin text-faint" />
                </div>
              ) : (
                <EmptyState onPick={(t) => setSeed({ text: t, k: Date.now() })} />
              )
            }
          />

          {waiting && resumeKind === "step" && (
            <div className="border-t border-warn/25 bg-warn/[0.07] px-4 py-2">
              <div className="mx-auto max-w-[1100px]">
                <div className="mb-2 text-[12px] text-warn">已暂停，确认结果或补充调整意见后继续</div>
                <div className="flex items-end gap-2">
                  <textarea value={stepNote} onChange={(e) => setStepNote(e.target.value)} rows={2}
                    placeholder="调整意见（可选），例如：下一步重点检查兼容性"
                    className="field min-w-0 flex-1 resize-none text-[12px]" />
                  <button className="btn-primary shrink-0" disabled={busy} onClick={async () => {
                    if (run && await act(() => api.cont(run.id, stepNote), "已继续")) setStepNote("");
                  }}>继续</button>
                </div>
              </div>
            </div>
          )}

          {running && (
            <div className="flex items-center gap-1.5 border-t border-line bg-panel/60 px-4 py-0.5">
              <Loader2 size={10} className="animate-spin text-accent" />
              <span className="text-[10.5px] leading-4 text-muted">{runStatusText(run)}</span>
              <button
                onClick={doStop}
                disabled={busy}
                className="ml-auto flex items-center gap-1 text-[10.5px] leading-4 text-danger hover:underline"
              >
                <Square size={8} fill="currentColor" /> 停止
              </button>
            </div>
          )}

          <Composer
            key={chatId || "empty"}
            members={chat?.members || []}
            running={running}
            disabledReason={disabledReason}
            onSend={send}
            onInterject={interject}
            onStop={doStop}
            seed={seed}
            onUpload={chatId ? async (file) => {
              const uploaded = await api.uploadChatFile(chatId, file);
              refreshChat(chatId);
              return uploaded;
            } : undefined}
          />
        </section>

        {/* 右：任务与文件（可拖拽调整宽度） */}
        {panel && (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="拖拽调整右侧面板宽度，双击恢复默认"
              aria-valuemin={260}
              aria-valuemax={760}
              aria-valuenow={panelWidth}
              tabIndex={0}
              onPointerDown={startPanelResize}
              onPointerMove={movePanelResize}
              onPointerUp={endPanelResize}
              onPointerCancel={endPanelResize}
              onDoubleClick={resetPanelWidth}
              onKeyDown={onPanelKeyDown}
              className="hidden w-1.5 shrink-0 cursor-col-resize touch-none transition-colors hover:bg-accent/30 focus:bg-accent/30 focus:outline-none lg:block"
            />
            <aside className="hidden shrink-0 border-l border-line lg:block" style={{ width: panelWidth }}>
              <SidePanel
                chatId={chatId || ""}
                tasks={activeTasks}
                files={chat?.files || []}
                pendingApprovals={chat?.pending_approvals || []}
                onOpenApproval={openApproval}
                activeTask={selectedTask}
                onSelectTask={setActiveTask}
                onCollapse={() => setPanelPersist(false)}
              />
            </aside>
          </>
        )}
      </div>

      {/* 移动端底部抽屉 */}
      {previewFile && <FilePreview file={previewFile} onClose={() => setPreviewFile(null)} />}
      <Modal
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        title="重命名群聊"
        width="max-w-sm"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setRenameOpen(false)}>
              取消
            </button>
            <button className="btn-primary" onClick={doRename}>
              保存
            </button>
          </>
        }
      >
        <input
          autoFocus
          className="field"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doRename()}
          placeholder="群聊名称"
        />
      </Modal>

      <MembersModal
        open={membersOpen}
        chat={chat}
        onClose={() => setMembersOpen(false)}
        onChanged={() => {
          if (!chatId) return;
          refreshChat(chatId);
          api.chats().then(setChats).catch(() => {});
        }}
      />

      <SearchModal
        open={searchOpen}
        chatId={chatId}
        onClose={() => setSearchOpen(false)}
        onJump={(messageId) => {
          setSearchOpen(false);
          jumpToMessage(messageId);
        }}
      />

      {chat && (
        <ChatSettings
          chat={chat}
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => refreshChat(chat.id)}
        />
      )}

      {mobilePanel && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobilePanel(false)} />
          <div className="absolute bottom-0 left-0 right-0 h-[72vh] overflow-hidden rounded-t-2xl border-t border-line">
            <SidePanel
              chatId={chatId || ""}
              tasks={activeTasks}
              files={chat?.files || []}
              pendingApprovals={chat?.pending_approvals || []}
              onOpenApproval={openApproval}
              activeTask={selectedTask}
              onSelectTask={setActiveTask}
              onClose={() => setMobilePanel(false)}
            />
          </div>
        </div>
      )}
    </AppShell>
  );
}

function NoChatState({ onOpenList }: { onOpenList: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-panel text-[18px]">
        💬
      </div>
      <div className="text-[12.5px] font-medium">还没有群聊</div>
      <p className="mt-1.5 max-w-[280px] text-[12px] leading-relaxed text-muted">
        新建一个群聊，选好成员和总指挥，就可以开始交代任务了。
      </p>
      <button className="btn-primary mt-4 lg:hidden" onClick={onOpenList}>
        打开群聊列表
      </button>
      <p className="mt-4 hidden text-[11px] text-faint lg:block">在左侧列表点「+」新建群聊</p>
    </div>
  );
}

function statusLabel(s?: string) {
  return s === "working" ? "工作中" : s === "waiting" ? "等待处理" : "空闲";
}

function runStatusText(run: Run | null) {
  if (!run) return "";
  if (run.status === "queued") return "排队中，等待 Worker…";
  if (run.status === "waiting") return "等待你的处理";
  return "总指挥正在调度…";
}

function EmptyState({ onPick }: { onPick: (t: string) => void }) {
  const samples = [
    "调研 3 家国内向量数据库厂商，写一份 1500 字对比报告。",
    "把这份需求文档拆成开发任务，评估每个任务的复杂度。",
    "查一下最近一周 AI Agent 领域的三个重要发布，整理成简报。",
  ];
  return (
    <div className="flex h-full flex-col items-center justify-center px-6">
      <div className="w-full max-w-[560px]">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-panel text-[18px]">
            🧭
          </div>
          <h2 className="text-[17px] font-semibold tracking-tight">交代一件事，团队自己跑</h2>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
            总指挥会判断复杂度、拆解任务、派给合适的成员。涉及高风险动作时会停下来等你审批。
          </p>
        </div>
        <div className="space-y-2">
          {samples.map((s) => (
            <button
              key={s}
              onClick={() => onPick(s)}
              className="w-full rounded-xl border border-line bg-panel px-3.5 py-2.5 text-left text-[12.5px] text-muted transition-colors hover:border-accent/40 hover:text-ink"
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
