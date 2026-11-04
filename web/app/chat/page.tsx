"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Bell, BellOff, Columns2, Download, GitBranch, Loader2, Menu as MenuIcon, Pencil, Plus, Search, SlidersHorizontal, Square, Trash2, Users, Workflow, Zap } from "lucide-react";
import AppShell from "@/components/AppShell";
import ChatList, { NewChatDialog } from "@/components/chat/ChatList";
import ChatSettings from "@/components/chat/ChatSettings";
import Composer from "@/components/chat/Composer";
import MembersModal from "@/components/chat/MembersModal";
import MessageStream from "@/components/chat/MessageStream";
import SearchModal from "@/components/chat/SearchModal";
import SidePanel from "@/components/chat/SidePanel";
import Avatar from "@/components/ui/Avatar";
import ActionMenu from "@/components/ui/Menu";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api, toastError } from "@/lib/api";
import { createMessageSync, mergeMessageSnapshot } from "@/lib/message-sync";
import { usePanelResize } from "@/hooks/use-panel-resize";
import type { Chat, ChatFile, ChatSummary, Message, PlanStep, Run, Task, Branch } from "@/lib/types";
import { num } from "@/lib/fmt";
import { toast } from "@/lib/ui";
import { useChatEventStream } from "@/hooks/use-chat-event-stream";
import { useChatNotifications } from "@/hooks/use-chat-notifications";
import { NoChatState, EmptyState, statusLabel, runStatusText } from "@/components/chat/ChatStates";


const ACTIVE = new Set(["queued", "running", "waiting", "waiting_children"]);

export default function ChatPage() {
  return <Suspense fallback={<div className="flex h-screen items-center justify-center bg-canvas"><Loader2 className="animate-spin text-muted" size={20} /></div>}><ChatContent /></Suspense>;
}

function ChatContent() {
  const searchParams = useSearchParams();
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [chatsLoading, setChatsLoading] = useState(true);
  const [chatId, setChatId] = useState<string | null>(null);
  const [chat, setChat] = useState<Chat | null>(null);
  // CHT-07：当前查看的分支线程；为空表示主线程
  const [branch, setBranch] = useState<Branch | null>(null);
  const branchRef = useRef<Branch | null>(null);
  const [branchesOpen, setBranchesOpen] = useState(false);
  const [branches, setBranches] = useState<Branch[]>([]);
  useEffect(() => {
    branchRef.current = branch;
  }, [branch]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  useEffect(() => setReplyTo(null), [chatId, branch?.id]);
  const messageSync = useRef(createMessageSync());
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
  const { panelWidth, startPanelResize, movePanelResize, endPanelResize, resetPanelWidth, onPanelKeyDown } = usePanelResize();
  const confirm = useConfirm();

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
        toastError(error);
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
        toastError(error);
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
      .catch((e) => toastError(e, "群聊加载失败"))
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
    const branchId = branchRef.current?.id;
    api
      .messages(id, { limit: 80, around: anchor || undefined, thread_id: branchId })
      .then((d) => {
        if (chatIdRef.current !== id || anchor !== focusRef.current || branchId !== branchRef.current?.id) return;
        const sync = messageSync.current;
        sync.cursor = Math.max(sync.cursor, d.event_cursor);
        setMessages((prev) => mergeMessageSnapshot(prev, d.messages, d.event_cursor, sync, !anchor));
      })
      .catch(() => {});
  }, []);

  const chatRequestVersion = useRef(0);
  const invalidateChatSnapshot = useCallback(() => { chatRequestVersion.current += 1; }, []);
  const refreshChat = useCallback((id: string) => {
    const version = ++chatRequestVersion.current;
    api
      .chat(id)
      .then((c) => {
        if (chatIdRef.current !== id || version !== chatRequestVersion.current) return;
        setChat(c);
      })
      .catch(() => {});
  }, []);

  const onFileDeleted = useCallback((id: string, file: ChatFile) => {
    setChat((current) => current?.id === id
      ? { ...current, files: current.files.filter((f) => f.path !== file.path) }
      : current);
    setPreviewFile((current) => current?.id === file.id ? null : current);
  }, []);

  /* ---------------- 切换群聊 ---------------- */
  useEffect(() => {
    if (!chatId) return;
    let canceled = false;
    chatRequestVersion.current += 1;
    localStorage.setItem("botly-chat", chatId);
    setChat(null);
    setMessages([]);
    messageSync.current = createMessageSync();
    branchRef.current = null;
    setBranch(null);
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
        messageSync.current.cursor = history.event_cursor;
        setHasEarlier(history.has_more);
        if (currentChat.main_thread_id) setEventStart({ chatId, threadId: currentChat.main_thread_id, after: history.event_cursor });
        if (pendingTarget.current?.chatId === chatId) {
          const target = pendingTarget.current;
          pendingTarget.current = null;
          openTarget(target);
        }
      })
      .catch((e) => {
        if (!canceled) toastError(e);
      })
      .finally(() => {
        if (!canceled && chatIdRef.current === chatId) setMessagesLoading(false);
      });
    return () => { canceled = true; };
  }, [chatId, openTarget]);

  /* ---------------- SSE ---------------- */
  useChatEventStream({ chatId, eventStart, chatIdRef, refreshChat, reloadMessages, setConnection, branchRef, messageSync, setMessages, setActiveTask, setChat,
    hasActiveRuns: !!chat?.active_run || !!chat?.active_runs?.length, invalidateChatSnapshot });

  /* ---------------- 用户级通知 ---------------- */
  useChatNotifications({ chatId, eventStart, me, setChats, chatRef, meRef, chatIdRef, openTarget, pendingTarget, setChatId });

  /* ---------------- 操作 ---------------- */
  const run: Run | null = chat?.active_run || chat?.last_run || null;
  const running = !!run && ACTIVE.has(run.status);
  const dispatchHint = branch ? "" : runStatusText(run, chat?.tasks || [], messages);
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
      toastError(e);
    } finally {
      setBusy(false);
    }
  };

  const send = async (content: string) => {
    if (!chatId) return;
    const threadId = branchRef.current?.id;
    const temp: Message = {
      id: `tmp-${Date.now()}`,
      thread_id: threadId || chat?.main_thread_id || "",
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
      const r = await api.send(chatId, content, undefined, threadId, replyTo?.id);
      if (chatIdRef.current !== chatId) return;
      // SSE 可能已经先推送了这条消息，替换临时消息时要去重，否则会出现重复 key
      setMessages((prev) => {
        const rest = prev.filter((m) => m.id !== temp.id && m.id !== r.message.id);
        return [...rest, r.message].sort((a, b) => a.seq - b.seq);
      });
      refreshChat(chatId);
      setReplyTo(null);
      api.chats().then(setChats).catch(() => {});
    } catch (e: any) {
      setMessages((p) => p.filter((m) => m.id !== temp.id));
      toastError(e);
    }
  };

  /** CHT-07：从某条消息拉出分支线程 */
  const branchFrom = async (msg: Message) => {
    if (!chatId || branchRef.current) return;
    try {
      const b = await api.createBranch(chatId, msg.id);
      branchRef.current = b;
      setBranch(b);
      const d = await api.messages(chatId, { limit: 80, thread_id: b.id });
      setMessages(d.messages);
      setHasEarlier(false);
      setFocusMessage(null);
      toast(`已拉出分支「${b.title}」`, "ok");
    } catch (e: any) {
      toastError(e);
    }
  };

  /** 从分支返回主线程 */
  const backToMain = async () => {
    if (!chatId) return;
    branchRef.current = null;
    setBranch(null);
    const d = await api.messages(chatId, { limit: 80 });
    setMessages(d.messages);
    setHasEarlier(false);
    setFocusMessage(null);
  };

  const openBranches = async () => {
    if (!chatId) return;
    try {
      const list = await api.branches(chatId);
      setBranches(list);
      setBranchesOpen(true);
    } catch (e: any) {
      toastError(e);
    }
  };

  const enterBranch = async (b: Branch) => {
    if (!chatId) return;
    branchRef.current = b;
    setBranch(b);
    setBranchesOpen(false);
    const d = await api.messages(chatId, { limit: 80, thread_id: b.id });
    setMessages(d.messages);
    setHasEarlier(false);
    setFocusMessage(null);
  };

  /** 删除分支（消息与执行一起删，主线程不受影响） */
  const removeBranch = async (b: Branch) => {
    if (!chatId) return;
    const ok = await confirm({
      title: `删除分支「${b.title}」？`,
      description: "分支里的消息和执行记录都会被删除，主线程不受影响。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteBranch(chatId, b.id);
      setBranches((list) => list.filter((x) => x.id !== b.id));
      if (branchRef.current?.id === b.id) {
        branchRef.current = null;
        setBranch(null);
        const d = await api.messages(chatId, { limit: 80 });
        setMessages(d.messages);
        setHasEarlier(false);
        setFocusMessage(null);
      }
      toast("分支已删除", "ok");
    } catch (e: any) {
      toastError(e);
    }
  };

  /** RUN-13：执行中补充，不新建执行 */
  const interject = async (content: string) => {
    if (!chatId || !run) return;
    const temp: Message = {
      id: `tmp-i-${Date.now()}`,
      thread_id: branchRef.current?.id || chat?.main_thread_id || "",
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
      toastError(e);
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
      toastError(e);
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

  const retryTask = async (task: Task) => {
    if (!chatId || busy || running) return;
    const id = chatId;
    setBusy(true);
    try {
      const result = await api.retryTask(id, task.id);
      const updated = await api.chat(id);
      if (chatIdRef.current !== id) return;
      branchRef.current = null;
      setBranch(null);
      focusRef.current = null;
      setFocusMessage(null);
      setChat(updated);
      setActiveTask(updated.tasks.find((item) => item.id === result.task.id) || result.task);
      setMessages((previous) => previous.some((message) => message.id === result.message.id) ? previous
        : [...previous, result.message].sort((a, b) => a.seq - b.seq));
      reloadMessages(id);
      toast("已启动单项重试，其他任务与产物保留", "ok");
    } catch (error) {
      toastError(error, "任务重试失败");
      refreshChat(id);
    } finally {
      setBusy(false);
    }
  };

  const onPlanCancel = (msg: Message) => msg.run_id && act(() => api.cancelPlan(msg.run_id!), "已取消计划");

  const onApproval = (msg: Message, approve: boolean, reason?: string, alwaysAllow?: boolean) =>
    act(() =>
      approve
        ? api.approve(msg.meta.approval_id, alwaysAllow)
        : api.reject(msg.meta.approval_id, reason),
      approve && alwaysAllow ? "已批准，并在本会话始终允许此工具" : approve ? "已允许本次调用" : "已拒绝"
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
      toastError(e);
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
      toastError(e);
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
      toastError(e);
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
      toastError(e);
    }
  };

  const requestNotify = async () => {
    if (typeof Notification === "undefined") return toast("当前浏览器不支持系统通知", "error");
    const p = await Notification.requestPermission();
    setNotifyPerm(p);
    if (p === "granted") toast("已开启浏览器通知", "ok");
    else toast("已拒绝浏览器通知，审批仍通过邮件提醒", "info");
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
      const history = await api.messages(id, { before: messages[0].seq, limit: 50, thread_id: branchRef.current?.id });
      if (chatIdRef.current !== id) return;
      setMessages((prev) => [...history.messages.filter((m) => !prev.some((old) => old.id === m.id)), ...prev]);
      setHasEarlier(history.has_more);
    } catch (error: any) { toastError(error); }
    finally { if (chatIdRef.current === id) setLoadingEarlier(false); }
  };
  const returnToLatest = async () => {
    if (!chatId) return;
    const id = chatId;
    navigationVersion.current += 1;
    focusRef.current = null;
    setFocusMessage(null);
    try {
      const history = await api.messages(id, { limit: 80, thread_id: branchRef.current?.id });
      if (chatIdRef.current !== id) return;
      setMessages(history.messages);
      setHasEarlier(history.has_more);
    } catch (error: any) { toastError(error); }
  };
  const selectedTask = activeTask ? activeTasks.find((t) => t.id === activeTask.id) || activeTask : null;

  return (
    <AppShell rightPanelOpen={!!chatId && panel}>
      <div className={`chat-layout relative h-full min-h-0 ${panel && chatId ? "has-detail" : ""}`}>
        {/* 左：群聊列表 */}
        <aside className="hidden min-h-0 lg:block">
          <ChatList
            onNewChat={() => setNewChatOpen(true)}
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
                onNewChat={() => { setListOpen(false); setNewChatOpen(true); }}
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
        <section className="chat-message-column flex min-h-0 min-w-0 flex-1 flex-col bg-panel">
          <header className="flex h-[64px] shrink-0 items-center gap-2 border-b border-line bg-panel px-3 sm:px-4">
            <button
              onClick={() => setListOpen(true)}
              className="icon-btn lg:hidden"
              aria-label="打开会话列表"
            >
              <MenuIcon size={17} />
            </button>
            <div className="min-w-0">
              {branch ? (
                <div className="flex min-w-0 items-center gap-2">
                  <button
                    onClick={backToMain}
                    className="flex shrink-0 items-center gap-1 rounded-md px-1 py-0.5 text-[11px] text-accent hover:bg-accent/10"
                    title="返回主线程"
                  >
                    <ArrowLeft size={13} /> 主线程
                  </button>
                  <span className="flex min-w-0 items-center gap-1 text-[13px] font-medium">
                    <GitBranch size={13} className="shrink-0 text-accent" />
                    <span className="truncate">{branch.title}</span>
                  </span>
                  <button
                    onClick={() => removeBranch(branch)}
                    title="删除该分支"
                    className="shrink-0 rounded-md p-1 text-faint transition-colors hover:bg-danger/10 hover:text-danger"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ) : (
                <div className="min-w-0 leading-tight">
                  <div className="mb-1 text-[10px] text-faint">{chat ? `${chat.members.length} 位团队成员` : "团队协作"}</div>
                  <div className="truncate text-[14px] font-semibold">{chat?.name || "团队工作空间"}</div>
                </div>
              )}
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
                    { label: "分支讨论", icon: GitBranch, onClick: openBranches },
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
                title={notifyPerm === "granted" ? "任务浏览器通知已开启（审批使用邮件）" : "开启任务浏览器通知（审批使用邮件）"}
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
            typingRunId={running && !messagesLoading && !focusMessage ? run?.id : undefined}
            typingRunIds={chat?.mode === "free" && !messagesLoading && !focusMessage ? chat.active_runs?.map((r) => r.id) : undefined}
            messages={messages}
            members={chat?.members || []}
            tasks={activeTasks}
            files={chat?.files || []}
            hasEarlier={hasEarlier}
            loadingEarlier={loadingEarlier}
            onLoadEarlier={loadEarlier}
            focusMessageId={focusMessage}
            me={me}
            onBranch={branch ? undefined : branchFrom}
            onReply={chat?.mode === "free" && !branch ? setReplyTo : undefined}
            handlers={{
              onPlanApprove,
              onPlanCancel,
              onApproval,
              onAnswer,
              onOpenTask,
              onOpenFile: file => {
                setPreviewFile(file); setActiveTask(null); setPanelPersist(true);
                if (!window.matchMedia("(min-width: 1024px)").matches) setMobilePanel(true);
              },
              busy,
            }}
            emptyState={
              !chatId ? (
                <NoChatState onCreate={() => setNewChatOpen(true)} />
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
              <div className="w-full">
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

          {dispatchHint && (
            <div className="flex items-center gap-1.5 border-t border-line bg-panel/60 px-4 py-0.5">
              <Loader2 size={10} className="animate-spin text-accent" />
              <span className="text-[10.5px] leading-4 text-muted">{dispatchHint}</span>
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
            delegationDepth={chat?.limits?.delegation_depth ?? 2}
            chatMode={branch ? "task" : chat?.mode || "task"}
            replyTo={replyTo ? { name: chat?.members.find((b) => b.id === replyTo.sender_bot_id)?.name || replyTo.sender_bot_name || "成员", content: replyTo.content } : null}
            onCancelReply={() => setReplyTo(null)}
            onUpload={chatId ? async (file) => {
              const uploaded = await api.uploadChatFile(chatId, file);
              refreshChat(chatId);
              return uploaded;
            } : undefined}
          />
        </section>

        {/* 右：任务与文件（可拖拽调整宽度） */}
        {panel && chatId && (
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
              className="chat-detail-resize hidden w-1.5 shrink-0 cursor-col-resize touch-none transition-colors hover:bg-accent/30 focus:bg-accent/30 focus:outline-none lg:block"
            />
            <aside className="chat-detail hidden min-h-0 shrink-0 border-l border-line lg:block" style={{ width: panelWidth }}>
              <SidePanel
                chatId={chatId || ""}
                tasks={activeTasks}
                files={chat?.files || []}
                pendingApprovals={chat?.pending_approvals || []}
                onOpenApproval={openApproval}
                activeTask={selectedTask}
                onSelectTask={setActiveTask}
                onCollapse={() => setPanelPersist(false)}
                messages={messages}
                supervisor={chat?.supervisor || null}
                previewFile={previewFile}
                onPreviewClose={() => setPreviewFile(null)}
                onFileDeleted={onFileDeleted}
                run={run}
                onRetryTask={retryTask}
                busy={busy}
              />
            </aside>
          </>
        )}
      </div>

      {/* 移动端底部抽屉 */}
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

      <Modal
        open={branchesOpen}
        onClose={() => setBranchesOpen(false)}
        title="分支讨论"
        subtitle="从主线程任意消息拉出的分支，不打断主线程"
        width="max-w-md"
      >
        {branches.length ? (
          <div className="space-y-1.5">
            {branches.map((b) => (
              <div
                key={b.id}
                className="group flex w-full items-center gap-1 rounded-lg border border-line px-2 py-1.5 transition-colors hover:border-accent/40"
              >
                <button
                  onClick={() => enterBranch(b)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-1 py-0.5 text-left"
                >
                  <GitBranch size={13} className="shrink-0 text-accent" />
                  <span className="min-w-0 flex-1 truncate text-[12.5px]">{b.title}</span>
                  <span className="shrink-0 text-[10.5px] text-faint">{b.message_count || 0} 条</span>
                </button>
                <button
                  onClick={() => removeBranch(b)}
                  title="删除分支"
                  className="shrink-0 rounded-md p-1 text-faint opacity-0 transition-opacity hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-8 text-center text-[12px] leading-relaxed text-faint">
            还没有分支。
            <br />
            把鼠标移到任意消息上，点气泡右侧的{" "}
            <GitBranch size={12} className="inline text-accent" /> 分支按钮即可拉出分支。
          </div>
        )}
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

      {newChatOpen && <NewChatDialog onClose={() => setNewChatOpen(false)} onCreated={id => { setNewChatOpen(false); setChatId(id); api.chats().then(setChats).catch(() => {}); }} />}

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
              messages={messages}
              supervisor={chat?.supervisor || null}
              previewFile={previewFile}
              onPreviewClose={() => setPreviewFile(null)}
              onFileDeleted={onFileDeleted}
              run={run}
              onRetryTask={retryTask}
              busy={busy}
            />
          </div>
        </div>
      )}
    </AppShell>
  );
}
