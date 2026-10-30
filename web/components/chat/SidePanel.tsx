"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, Download, FileText, ListChecks, Loader2, PanelRightClose, RotateCw, X } from "lucide-react";
import { api } from "@/lib/api";
import Avatar from "@/components/ui/Avatar";
import type { ChatFile, Message, Task } from "@/lib/types";
import { bytes, clock, num, timeAgo } from "@/lib/fmt";

import { ToolBlock } from "./cards";
import Markdown from "./Markdown";

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
}) {
  const [tab, setTab] = useState<"tasks" | "files">("tasks");
  const [preview, setPreview] = useState<ChatFile | null>(null);
  // 任务面板只列进行中的任务；已完成/失败的细节通过群聊消息下的「查看过程」打开
  const ACTIVE_TASK = new Set(["queued", "running", "waiting_approval", "waiting_reply"]);
  const activeTasks = tasks.filter((t) => ACTIVE_TASK.has(t.status));

  useEffect(() => {
    if (activeTask) setTab("tasks");
  }, [activeTask?.id]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line px-2">
        <button
          onClick={() => setTab("tasks")}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] transition-colors ${
            tab === "tasks" ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
          }`}
        >
          <ListChecks size={13} /> 任务
          {activeTasks.length > 0 && <span className="text-[10.5px] text-faint">{activeTasks.length}</span>}
        </button>
        <button
          onClick={() => setTab("files")}
          className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] transition-colors ${
            tab === "files" ? "bg-panel2 text-ink" : "text-muted hover:text-ink"
          }`}
        >
          <FileText size={13} /> 文件
          {files.length > 0 && <span className="text-[10.5px] text-faint">{files.length}</span>}
        </button>
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
          <TaskThread key={activeTask.id} chatId={chatId} task={activeTask} onBack={() => onSelectTask(null)} />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {activeTasks.length === 0 && (
              <div className="px-3 py-8 text-center text-[12px] leading-relaxed text-faint">
                当前没有进行中的任务
                <br />
                已完成的过程可点群聊消息下的「查看过程」
              </div>
            )}
            {activeTasks.map((t) => (
              <button
                key={t.id}
                onClick={() => onSelectTask(t)}
                className="mb-1 w-full rounded-lg border border-transparent px-2.5 py-2 text-left hover:border-line hover:bg-panel2"
              >
                <div className="flex items-center gap-1.5">
                  <Avatar value={t.bot_avatar} size={16} />
                  <span className="text-[12px] font-medium">{t.bot_name}</span>
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
                  {t.instruction}
                </div>
              </button>
            ))}
          </div>
        )
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {files.length === 0 && (
            <div className="px-3 py-8 text-center text-[12px] leading-relaxed text-faint">
              暂无产物
              <br />
              Bot 写入文件后会出现在这里
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
            </div>
          ))}
        </div>
      )}

      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function TaskThread({ chatId, task, onBack }: {
  chatId: string;
  task: Task;
  onBack: () => void;
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
    setLoading(true);
    setError("");
    Promise.all([api.messages(chatId, { thread_id: task.thread_id, limit: 200 }), api.task(task.id)])
      .then(([history, current]) => {
        if (!alive) return;
        setMsgs(history.messages);
        setHasMore(history.has_more);
        setDetail(current);
        es = new EventSource(`/api/chats/${chatId}/events?after=${history.event_cursor}`);
        es.onopen = () => alive && setError("");
        es.onerror = () => alive && setError("实时连接中断，正在重连…");
        es.addEventListener("message.created", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data) as Message;
          if (p.thread_id !== task.thread_id) return;
          setMsgs((prev) => [...prev.filter((m) => m.id !== p.id), p].sort((a, b) => a.seq - b.seq));
        });
        ["message.delta", "message.completed"].forEach((type) => es!.addEventListener(type, (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          setMsgs((prev) => prev.map((m) => m.id === p.message_id
            ? { ...m, content: p.content, status: p.status || m.status,
                meta: p.meta ? { ...m.meta, ...p.meta } : m.meta }
            : m));
        }));
        es.addEventListener("message.deleted", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          setMsgs((prev) => prev.filter((m) => m.id !== p.id));
        });
        es.addEventListener("task.status", (ev) => {
          if (!alive) return;
          const p = JSON.parse((ev as MessageEvent).data);
          if (p.task_id === task.id) setDetail((t) => ({ ...t, ...p, id: t.id }));
        });
      })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; es?.close(); };
  }, [chatId, task.id, task.thread_id, retry]);

  const loadMore = async () => {
    if (!msgs.length) return;
    setLoadingMore(true);
    try {
      const page = await api.messages(chatId, { thread_id: task.thread_id, before: msgs[0].seq, limit: 200 });
      setMsgs((prev) => [...page.messages.filter((m) => !prev.some((p) => p.id === m.id)), ...prev]);
      setHasMore(page.has_more);
    } catch (e: any) { setError(e.message); }
    finally { setLoadingMore(false); }
  };
  const results: Record<string, Message> = {};
  msgs.forEach((m) => { if (m.type === "tool_result") results[m.meta?.tool_call_id] = m; });
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
        </div>
        <div className="mb-2 text-[11px] font-medium text-muted">执行过程 · 实时更新</div>
        {error && <div className="mb-2 text-[11px] text-danger">{error} <button className="underline" onClick={() => setRetry((v) => v + 1)}>重试</button></div>}
        {hasMore && <button className="btn-ghost mb-2 w-full text-[11px]" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更早的过程"}</button>}
        {loading && <Loader2 size={14} className="animate-spin text-faint" />}
        {!loading && !msgs.length && !error && <div className="text-[11px] text-faint">{ended ? "没有过程记录" : "等待成员开始执行…"}</div>}
        {msgs.filter((m) => m.type !== "tool_result").map((m) => (
          <div key={m.id} className="mb-3 min-w-0">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] text-faint">
              <span>{detail.bot_name}</span><span>{clock(m.created_at)}</span>
              {m.status === "streaming" && <span className="text-accent">正在输出…</span>}
              {m.status === "interrupted" && <span className="text-warn">输出已中断</span>}
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

export function FilePreview({ file, onClose }: { file: ChatFile; onClose: () => void }) {
  const [content, setContent] = useState<string>("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .fileContent(file.id)
      .then(setContent)
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [file.id]);

  const isMarkdown = /\.(md|markdown)$/i.test(file.path);
  const isHtml = /\.html?$/i.test(file.path);
  const [htmlView, setHtmlView] = useState<"preview" | "source">("preview");
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className={`flex max-h-[80vh] w-full flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-card ${isHtml ? "max-w-5xl" : "max-w-2xl"}`}>
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <FileText size={14} className="text-accent" />
          <span className="font-mono text-[12.5px]">{file.path}</span>
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
          <button onClick={onClose} className="p-1 text-faint hover:text-ink">
            <X size={15} />
          </button>
        </div>
        {isHtml && htmlView === "preview" ? (
          // srcDoc 直接渲染，沙箱只放行脚本：不能碰宿主页面的 Cookie/存储，也不能读 /api
          <div className="min-h-0 flex-1 bg-white">
            {loading ? (
              <div className="p-4"><Loader2 size={16} className="animate-spin text-faint" /></div>
            ) : err ? (
              <div className="p-4 text-[12px] text-danger">{err}</div>
            ) : (
              <iframe
                key={reloadKey}
                title={file.path}
                srcDoc={content}
                sandbox="allow-scripts allow-modals allow-popups allow-forms"
                referrerPolicy="no-referrer"
                className="h-[70vh] w-full border-0"
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
      </div>
    </div>
  );
}
