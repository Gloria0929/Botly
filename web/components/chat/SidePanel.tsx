"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, Download, FileText, ListChecks, Loader2, PanelRightClose, RotateCw, Terminal, Trash2, X } from "lucide-react";
import { api, errText, toastError } from "@/lib/api";
import { acceptMessageEvent, createMessageSync, mergeMessageSnapshot, mergeMessageUpdate } from "@/lib/message-sync";
import Avatar from "@/components/ui/Avatar";
import { useConfirm } from "@/components/ui/Confirm";
import type { Bot, ChatFile, Message, Run, Task } from "@/lib/types";
import { canRetryTask, taskCounts, teamStatus } from "@/lib/task-state";
import { bytes, clock, num, timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

import { ToolBlock } from "./cards";
import Markdown from "./Markdown";
import SandboxTerminal from "./SandboxTerminal";

const TASK_STATUS: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  waiting_approval: "等待审批",
  waiting_reply: "等待回复",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export default function SidePanel({
  chatId,
  tasks,
  files,
  onClose,
  onCollapse,
  activeTask,
  onSelectTask,
  pendingApprovals = [],
  onOpenApproval,
  messages = [],
  supervisor = null,
  previewFile = null,
  onPreviewClose,
  onFileDeleted,
  run = null,
  onRetryTask,
  busy = false,
}: {
  chatId: string;
  tasks: Task[];
  files: ChatFile[];
  onClose?: () => void;
  onCollapse?: () => void;
  activeTask: Task | null;
  onSelectTask: (t: Task | null) => void;
  pendingApprovals?: { id: string; tool: string; bot_name: string }[];
  onOpenApproval?: (id: string) => void;
  /** 主线程消息：总指挥自己的工具调用留在这里，群聊不展示 */
  messages?: Message[];
  supervisor?: Bot | null;
  previewFile?: ChatFile | null;
  onPreviewClose?: () => void;
  onFileDeleted: (chatId: string, file: ChatFile) => void;
  run?: Run | null;
  onRetryTask?: (task: Task) => void;
  busy?: boolean;
}) {
  const [tab, setTab] = useState<"tasks" | "files" | "terminal">("tasks");
  const [preview, setPreview] = useState<ChatFile | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const confirm = useConfirm();
  const [terminalOpenedFor, setTerminalOpenedFor] = useState<string | null>(null);
  // 总指挥执行过程（工具调用）单独一屏；子任务线程由 activeTask 控制
  const [supervisorOpen, setSupervisorOpen] = useState(false);
  // 保留本次/最近一次执行的所有任务，结束后仍能从侧边栏打开完整过程。
  const ACTIVE_TASK = new Set(["queued", "running", "waiting_approval", "waiting_reply"]);
  const activeTasks = tasks.filter((t) => ACTIVE_TASK.has(t.status));
  const endedTasks = tasks.filter((t) => !ACTIVE_TASK.has(t.status));
  const counts = taskCounts(tasks);
  useEffect(() => { setPreview(null); }, [chatId]);
  useEffect(() => { if (previewFile) { setPreview(previewFile); setTab("files"); } }, [previewFile, chatId]);

  const deleteArtifact = async (file: ChatFile) => {
    if (deletingId) return;
    setDeletingId(file.id);
    try {
      if (!await confirm({
        title: "删除产物？",
        description: `将删除 ${file.path} 的文件和产物记录，此操作无法撤销。`,
        confirmText: "删除",
        danger: true,
      })) return;
      await api.deleteFile(file.id);
      setPreview((current) => current?.id === file.id ? null : current);
      onFileDeleted(chatId, file);
      toast("产物已删除");
    } catch (e) {
      toastError(e, "产物删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  // 总指挥在主线程里的工具调用：群聊只显示聊天记录，过程统一收进面板
  const supervisorTools = useMemo(
    () =>
      messages.filter((m) => {
        if (m.meta?.task_id) return false;
        if (m.type === "tool_call") {
          const name = m.meta?.tool_calls?.[0]?.function?.name;
          // 派任务、计划、提问在群聊里都有专门的卡片，不在这里重复
          return name !== "delegate" && name !== "propose_plan" && name !== "ask_user";
        }
        return m.type === "tool_result";
      }),
    [messages]
  );
  const supervisorCalls = supervisorTools.filter((m) => m.type === "tool_call");

  useEffect(() => {
    if (activeTask) setSupervisorOpen(false);
  }, [activeTask?.id]);

  useEffect(() => {
    if (activeTask) setTab("tasks");
  }, [activeTask?.id]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel">
      <div className="flex h-16 shrink-0 items-center gap-1 border-b border-line px-2">
        <button
          onClick={() => setTab("tasks")}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] transition-colors ${
            tab === "tasks" ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
          }`}
        >
          <ListChecks size={13} /> 任务
          {tasks.length > 0 && <span className="text-[10.5px] text-faint">{tasks.length}</span>}
        </button>
        <button
          onClick={() => setTab("files")}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] transition-colors ${
            tab === "files" ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
          }`}
        >
          <FileText size={13} /> 产物
          {files.length > 0 && <span className="text-[10.5px] text-faint">{files.length}</span>}
        </button>
        <button
          onClick={() => { setTerminalOpenedFor(chatId); setTab("terminal"); }}
          aria-pressed={tab === "terminal"}
          className={`flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] transition-colors ${tab === "terminal" ? "bg-panel2 text-ink" : "text-muted hover:text-ink"}`}
        ><Terminal size={13} /> 终端</button>
        <div className="ml-auto flex items-center">
          {onCollapse && (
            <button
              onClick={onCollapse}
              title="收起面板"
              className="hidden p-1.5 text-faint hover:text-ink lg:block"
            >
              <PanelRightClose size={15} />
            </button>
          )}
          {onClose && (
            <button onClick={onClose} className="p-1.5 text-faint hover:text-ink lg:hidden">
              <X size={15} />
            </button>
          )}
        </div>
      </div>

      {tab === "tasks" && <div className="shrink-0 border-b border-line px-3 py-2.5" role="status">
        <div className="text-[11.5px] font-medium text-ink">{teamStatus(run, tasks)}</div>
        {counts.total > 0 && <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-muted">
          <span>已完成 {counts.completed} / {counts.total} 条记录</span>
          {counts.running > 0 && <span className="text-accent">进行中 {counts.running}</span>}
          {counts.queued > 0 && <span>排队 {counts.queued}</span>}
          {counts.waiting > 0 && <span className="text-warn">待处理 {counts.waiting}</span>}
          {counts.failed > 0 && <span className="text-danger">失败 {counts.failed}</span>}
        </div>}
      </div>}
      {tab === "tasks" && pendingApprovals.length > 0 && <div className="shrink-0 border-b border-line p-2">
        <div className="px-2 py-1 text-[11px] font-medium text-warn">待处理审批 · {pendingApprovals.length}</div>
        {pendingApprovals.map((approval) => <button key={approval.id} onClick={() => onOpenApproval?.(approval.id)}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-[11.5px] hover:bg-panel2">
          <span className="truncate text-muted">{approval.bot_name}</span><code className="truncate text-ink">{approval.tool}</code>
          <span className="ml-auto shrink-0 text-accent">去处理</span>
        </button>)}
      </div>}
      {tab === "tasks" ? (
        activeTask ? (
          <TaskThread key={activeTask.id} chatId={chatId} task={activeTask} onBack={() => onSelectTask(null)}
            onRetry={onRetryTask && canRetryTask(activeTask, run) ? () => onRetryTask(activeTask) : undefined} busy={busy} />
        ) : supervisorOpen ? (
          <SupervisorThread
            msgs={supervisorTools}
            supervisor={supervisor}
            onBack={() => setSupervisorOpen(false)}
          />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {supervisorCalls.length > 0 && (
              <button
                onClick={() => setSupervisorOpen(true)}
                className="mb-1 w-full rounded-lg border border-transparent px-2.5 py-2 text-left hover:border-line hover:bg-panel2"
              >
                <div className="flex items-center gap-1.5">
                  <Avatar value={supervisor?.avatar || "🧭"} size={16} />
                  <span className="text-[12px] font-medium">{supervisor?.name || "总指挥"}</span>
                  <span className="ml-auto text-[10.5px] text-faint">{supervisorCalls.length} 次工具调用</span>
                </div>
                <div className="mt-1 flex items-center gap-1 text-[11.5px] leading-relaxed text-muted">
                  <Terminal size={11} className="shrink-0 text-faint" /> 工具调用与返回
                </div>
              </button>
            )}
            {tasks.length === 0 && supervisorCalls.length === 0 && (
              <div className="px-3 py-8 text-center text-[12px] leading-relaxed text-faint">
                当前还没有子任务记录
              </div>
            )}
            {[{ title: "进行中", items: activeTasks }, { title: "已结束", items: endedTasks }].map((group) => group.items.length > 0 && (
              <div key={group.title}>
              <div className="px-2.5 pb-1 pt-3 text-[10.5px] font-medium text-faint">{group.title} · {group.items.length}</div>
              {group.items.map((t) => (
              <div key={t.id} className="mb-1 rounded-lg border border-transparent hover:border-line hover:bg-panel2">
              <button
                onClick={() => onSelectTask(t)}
                aria-label={`查看${t.bot_name}的完整执行过程：${t.instruction.slice(0, 40)}`}
                className="w-full rounded-lg px-2.5 py-2 text-left"
              >
                <div className="flex items-center gap-1.5">
                  <Avatar value={t.bot_avatar} size={16} />
                  <span className="text-[12px] font-medium">{t.bot_name}</span>
                  {!!t.batch && (
                    <span className="chip px-1 py-0 text-[10px] text-accent" title="和其它步骤并行执行">
                      并行 {t.batch}
                    </span>
                  )}
                  <span
                    className={`ml-auto text-[10.5px] ${
                      t.status === "completed"
                        ? "text-ok"
                        : t.status === "failed"
                          ? "text-danger"
                          : t.status.includes("waiting")
                            ? "text-warn"
                            : "text-accent"
                    }`}
                  >
                    {TASK_STATUS[t.status] || t.status}
                  </span>
                </div>
                <div className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-muted">
                  {t.step_key || t.instruction}
                </div>
                {t.status === "failed" && t.result && <div className="mt-1 line-clamp-2 break-words text-[10.5px] text-danger">{t.result}</div>}
                <div className="mt-1.5 flex items-center gap-1 text-[10.5px] text-accent"><Terminal size={11} />查看完整过程</div>
              </button>
              {onRetryTask && canRetryTask(t, run) && <button className="mb-2 ml-2.5 inline-flex items-center gap-1.5 rounded px-2 py-1 text-[11px] text-accent hover:bg-accent/10 disabled:opacity-50"
                disabled={busy} onClick={() => onRetryTask(t)} title="只重试这一项，保留其他任务和产物">
                <RotateCw size={12} />只重试此任务
              </button>}
              </div>
              ))}
              </div>
            ))}
          </div>
        )
      ) : tab === "terminal" ? null : preview ? (
        <FilePreview key={preview.id} file={preview} onClose={() => { setPreview(null); onPreviewClose?.(); }}
          onDelete={() => deleteArtifact(preview)} deleting={deletingId === preview.id} deleteDisabled={!!deletingId} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {files.length === 0 && (
            <div className="px-3 py-8 text-center text-[12px] leading-relaxed text-faint">
              暂无产物
              <br />
              Agent 的交付文件会出现在这里
            </div>
          )}
          {files.map((f) => (
            <div
              key={f.id}
              className="mb-1 flex items-center gap-2 rounded-lg px-2.5 py-2 hover:bg-panel2"
            >
              <FileText size={14} className="shrink-0 text-accent" />
              <button onClick={() => setPreview(f)} className="min-w-0 flex-1 text-left">
                <div className="truncate font-mono text-[12px]">{f.path}</div>
                <div className="text-[10.5px] text-faint">
                  {bytes(f.size)} · {timeAgo(f.updated_at)}
                </div>
              </button>
              <a
                href={`/api/files/${f.id}/download`}
                className="p-1 text-faint hover:text-ink"
                title="下载"
              >
                <Download size={13} />
              </a>
              <button
                onClick={() => deleteArtifact(f)}
                disabled={!!deletingId}
                className="p-1 text-faint hover:text-danger disabled:cursor-wait disabled:opacity-50"
                title="删除产物"
                aria-label={`删除产物 ${f.path}`}
              >
                {deletingId === f.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              </button>
            </div>
          ))}
        </div>
      )}

      {(terminalOpenedFor === chatId || tab === "terminal") && <div className={`${tab === "terminal" ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
        <SandboxTerminal key={chatId} chatId={chatId} visible={tab === "terminal"} />
      </div>}

    </div>
  );
}

function SupervisorThread({ msgs, supervisor, onBack }: {
  msgs: Message[];
  supervisor: Bot | null;
  onBack: () => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const calls = msgs.filter((m) => m.type === "tool_call");
  const results: Record<string, Message> = {};
  msgs.forEach((m) => {
    if (m.type === "tool_result" && m.meta?.tool_call_id) results[m.meta.tool_call_id] = m;
  });
  // 新的调用追加在末尾，跟随滚动；用户往上翻时不做强制
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
  }, [calls.length]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-2.5 py-2">
        <button onClick={onBack} title="返回任务列表" className="p-1 text-faint hover:text-ink"><ChevronLeft size={15} /></button>
        <Avatar value={supervisor?.avatar || "🧭"} size={20} />
        <span className="text-[12px] font-medium">{supervisor?.name || "总指挥"}</span>
        <span className="ml-auto text-[10.5px] text-faint">{calls.length} 次工具调用</span>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="mb-3 rounded-lg bg-panel2 p-2.5 text-[11.5px] leading-relaxed text-muted">
          群聊只展示对话，总指挥自己的工具调用与返回都收在这里，失败原因也在这里看。
        </div>
        {!calls.length && <div className="text-[11px] text-faint">这段对话里总指挥还没有调用工具</div>}
        {calls.map((m) => (
          <div key={m.id} className="mb-3 min-w-0">
            <div className="mb-1 text-[10px] text-faint">{clock(m.created_at)}</div>
            <ToolBlock
              msg={{ ...m, content: "" }}
              result={results[m.meta?.tool_calls?.[0]?.id]}
              defaultOpen
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function TaskThread({ chatId, task, onBack, onRetry, busy = false }: {
  chatId: string;
  task: Task;
  onBack: () => void;
  onRetry?: () => void;
  busy?: boolean;
}) {
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [detail, setDetail] = useState(task);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => setDetail(task), [task]);
  useEffect(() => {
    const reconnect = () => setRetry((v) => v + 1);
    window.addEventListener("online", reconnect);
    return () => window.removeEventListener("online", reconnect);
  }, []);

  useEffect(() => {
    let alive = true;
    let es: EventSource | null = null;
    const sync = createMessageSync();
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    let refreshing = false;
    const refresh = () => {
      if (refreshTimer || refreshing) return;
      refreshTimer = setTimeout(async () => {
        refreshTimer = null;
        if (!alive) return;
        refreshing = true;
        try {
          const [history, current] = await Promise.all([
            api.messages(chatId, { thread_id: task.thread_id, limit: 200 }), api.task(task.id),
          ]);
          if (!alive) return;
          sync.cursor = Math.max(sync.cursor, history.event_cursor);
          setMsgs((prev) => mergeMessageSnapshot(prev, history.messages, history.event_cursor, sync));
          setDetail(current);
          setError("");
        } catch (e) {
          if (alive) setError(errText(e, "过程记录刷新失败"));
        } finally {
          refreshing = false;
        }
      }, 200);
    };
    setLoading(true);
    setError("");
    Promise.all([api.messages(chatId, { thread_id: task.thread_id, limit: 200 }), api.task(task.id)])
      .then(([history, current]) => {
        if (!alive) return;
        setMsgs(history.messages);
        sync.cursor = history.event_cursor;
        setHasMore(history.has_more);
        setDetail(current);
        es = new EventSource(`/api/chats/${chatId}/events?after=${history.event_cursor}`);
        es.onopen = () => { if (alive) { setError(""); refresh(); } };
        es.onerror = () => alive && setError("实时连接中断，正在重连…");
        es.addEventListener("message.created", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data) as Message;
          if (p.thread_id !== task.thread_id) return;
          if (!acceptMessageEvent(sync, p.id, Number((ev as MessageEvent).lastEventId))) return;
          setMsgs((prev) => {
            const old = prev.find((m) => m.id === p.id);
            return [...prev.filter((m) => m.id !== p.id), old ? mergeMessageUpdate(old, p) : p].sort((a, b) => a.seq - b.seq);
          });
        });
        ["message.delta", "message.completed"].forEach((type) => es!.addEventListener(type, (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          if (!acceptMessageEvent(sync, p.message_id, Number((ev as MessageEvent).lastEventId))) return;
          setMsgs((prev) => prev.map((m) => m.id === p.message_id
            && (type === "message.completed" || m.status === "streaming")
            ? mergeMessageUpdate(m, { content: p.content, status: p.status || m.status,
                type: p.type || m.type, meta: p.meta })
            : m));
        }));
        es.addEventListener("message.deleted", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          if (!acceptMessageEvent(sync, p.id, Number((ev as MessageEvent).lastEventId), true)) return;
          setMsgs((prev) => prev.filter((m) => m.id !== p.id));
        });
        es.addEventListener("task.status", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          if (p.task_id === task.id) {
            setDetail((t) => ({ ...t, ...p, id: t.id }));
            if (["completed", "failed", "canceled"].includes(p.status)) refresh();
          }
        });
      })
      .catch((e) => alive && setError(errText(e, "任务详情加载失败")))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; es?.close(); if (refreshTimer) clearTimeout(refreshTimer); };
  }, [chatId, task.id, task.thread_id, retry]);

  const loadMore = async () => {
    if (!msgs.length) return;
    setLoadingMore(true);
    try {
      const page = await api.messages(chatId, { thread_id: task.thread_id, before: msgs[0].seq, limit: 200 });
      setMsgs((prev) => [...page.messages.filter((m) => !prev.some((p) => p.id === m.id)), ...prev]);
      setHasMore(page.has_more);
    } catch (e: any) { setError(errText(e, "加载失败，请稍后重试")); }
    finally { setLoadingMore(false); }
  };
  const results: Record<string, Message> = {};
  msgs.forEach((m) => { if (m.type === "tool_result") results[m.meta?.tool_call_id] = m; });
  const loadedCallIds = new Set<string>(msgs.flatMap((m) => m.type === "tool_call"
    ? (m.meta?.tool_calls || []).map((call: { id: string }) => call.id) : []));
  const ended = ["completed", "failed", "canceled"].includes(detail.status);
  const toolCount = msgs.filter((m) => m.type === "tool_call").length;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-2.5 py-2">
        <button onClick={onBack} title="返回任务列表" className="p-1 text-faint hover:text-ink"><ChevronLeft size={15} /></button>
        <Avatar value={detail.bot_avatar} size={20} />
        <span className="text-[12px] font-medium">{detail.bot_name}</span>
        <span className={`ml-auto text-[10.5px] ${detail.status === "completed" ? "text-ok" : detail.status === "failed" ? "text-danger" : "text-accent"}`}>
          {TASK_STATUS[detail.status] || detail.status}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="mb-3 space-y-2 rounded-lg bg-panel2 p-2.5 text-[11.5px] leading-relaxed text-muted">
          <div className="text-[10.5px] text-faint">任务指令</div>
          <div className="whitespace-pre-wrap break-words">{detail.instruction}</div>
          {detail.expected_output && <div className="whitespace-pre-wrap">期望产出：{detail.expected_output}</div>}
          <div className="text-[10.5px] text-faint">{num(detail.tokens_used)} tokens · {toolCount} 次工具调用 · 第 {detail.attempt} 次执行</div>
          <div className="text-[10.5px] text-faint">开始 {clock(detail.started_at) || "—"}{detail.ended_at && ` · 结束 ${clock(detail.ended_at)}`}</div>
          {detail.status === "failed" && detail.result && <div className="whitespace-pre-wrap break-words text-danger">失败原因：{detail.result}</div>}
          {onRetry && <button className="btn-ghost inline-flex items-center gap-1.5 text-[11px] text-accent" disabled={busy} onClick={onRetry}>
            <RotateCw size={12} />只重试此任务
          </button>}
        </div>
        <div className="mb-2 text-[11px] font-medium text-muted">执行过程 · 实时更新</div>
        {error && <div className="mb-2 text-[11px] text-danger">{error} <button className="underline" onClick={() => setRetry((v) => v + 1)}>重试</button></div>}
        {hasMore && <button className="btn-ghost mb-2 w-full text-[11px]" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更早的过程"}</button>}
        {loading && <Loader2 size={14} className="animate-spin text-faint" />}
        {!loading && !msgs.length && !error && <div className="text-[11px] text-faint">{ended ? "没有过程记录" : "等待成员开始执行…"}</div>}
        {msgs.filter((m) => m.type !== "tool_result" || !loadedCallIds.has(m.meta?.tool_call_id)).map((m) => (
          <div key={m.id} className="mb-3 min-w-0">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] text-faint">
              <span>{detail.bot_name}</span><span>{clock(m.created_at)}</span>
              {m.status === "streaming" && <span className="text-accent">正在输出…</span>}
              {m.status === "interrupted" && <span className="text-warn">输出已中断</span>}
              {m.type === "tool_result" && <span>工具返回 · {m.meta?.tool || "工具"}</span>}
            </div>
            {m.content && <Markdown text={m.content} size="sm" className="break-words" />}
            {m.type === "tool_call" && <ToolBlock msg={{ ...m, content: "" }} result={results[m.meta?.tool_calls?.[0]?.id]} defaultOpen ended={ended} />}
          </div>
        ))}
        {detail.result && (detail.status !== "completed" || !msgs.some((m) => m.type === "text" && m.content === detail.result)) && <div className="mt-3 border-t border-line pt-3">
          <div className="mb-1 text-[11px] font-medium text-muted">{detail.status === "completed" ? "任务结果" : "结束原因"}</div>
          <Markdown text={detail.result} size="sm" className="break-words" />
        </div>}
      </div>
    </div>
  );
}

export function FilePreview({ file, onClose, onDelete, deleting, deleteDisabled }: {
  file: ChatFile;
  onClose: () => void;
  onDelete: () => void;
  deleting: boolean;
  deleteDisabled: boolean;
}) {
  const [content, setContent] = useState<string>("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const isImage = /\.(png|jpe?g|gif|webp|avif)$/i.test(file.path);
  const isPdf = /\.pdf$/i.test(file.path);

  useEffect(() => {
    let active = true;
    setLoading(true); setContent(""); setErr("");
    if (isImage || isPdf) { setLoading(false); return; }
    api
      .fileContent(file.id)
      .then(text => { if (active) setContent(text); })
      .catch((e) => { if (active) setErr(errText(e, "无法读取文件")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [file.id, isImage, isPdf]);

  const isMarkdown = /\.(md|markdown)$/i.test(file.path);
  const isHtml = /\.html?$/i.test(file.path);
  const [htmlView, setHtmlView] = useState<"preview" | "source">("preview");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <section aria-label="产物预览" className="flex min-h-0 flex-1 flex-col overflow-hidden bg-panel">
        <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2.5">
          <button onClick={onClose} title="返回产物列表" aria-label="返回产物列表" className="shrink-0 p-1 text-faint hover:text-ink"><ChevronLeft size={15} /></button>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px]" title={file.path}>{file.path}</span>
          <span className="text-[10.5px] text-faint">{bytes(file.size)}</span>
          {isHtml && (
            <div className="ml-auto flex items-center gap-0.5 rounded-lg bg-panel2 p-0.5">
              {(["preview", "source"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setHtmlView(v)}
                  className={`rounded-md px-2 py-0.5 text-[11px] transition-colors ${htmlView === v ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink"}`}
                >
                  {v === "preview" ? "预览" : "源码"}
                </button>
              ))}
              {htmlView === "preview" && (
                <button
                  onClick={() => setReloadKey((k) => k + 1)}
                  className="rounded-md px-1.5 py-0.5 text-muted hover:text-ink"
                  title="重新加载"
                >
                  <RotateCw size={11} />
                </button>
              )}
            </div>
          )}
          <a
            href={`/api/files/${file.id}/download`}
            className={`${isHtml ? "" : "ml-auto "}p-1 text-faint hover:text-ink`}
            title="下载"
          >
            <Download size={14} />
          </a>
          <button onClick={onDelete} disabled={deleteDisabled} title="删除产物" aria-label={`删除产物 ${file.path}`}
            className="shrink-0 p-1 text-faint hover:text-danger disabled:cursor-wait disabled:opacity-50">
            {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          </button>
        </div>
        {isImage ? <div className="min-h-0 flex-1 overflow-auto p-3"><img src={`/api/files/${file.id}/preview`} alt={file.path} className="h-auto max-w-full rounded" /></div> : isPdf ? (
          <iframe title={file.path} src={`/api/files/${file.id}/preview`} className="min-h-0 w-full flex-1 border-0" />
        ) : isHtml && htmlView === "preview" ? (
          // srcDoc 直接渲染，沙箱只放行脚本：不能碰宿主页面的 Cookie/存储，也不能读 /api
          <div className="flex min-h-0 flex-1 flex-col bg-white">
            {loading ? (
              <div className="p-4"><Loader2 size={16} className="animate-spin text-faint" /></div>
            ) : err ? (
              <div className="p-4 text-[12px] text-danger">{err}</div>
            ) : (
              <iframe
                key={reloadKey}
                title={file.path}
                srcDoc={content}
                sandbox="allow-scripts"
                referrerPolicy="no-referrer"
                className="min-h-0 w-full flex-1 border-0"
              />
            )}
          </div>
        ) : (
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {loading ? (
            <Loader2 size={16} className="animate-spin text-faint" />
          ) : err ? (
            <div className="text-[12px] text-danger">{err}</div>
          ) : isMarkdown ? (
            <Markdown text={content} />
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-ink">
              {content}
            </pre>
          )}
        </div>
        )}
    </section>
  );
}
