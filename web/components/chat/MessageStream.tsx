"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { FileText, GitBranch } from "lucide-react";
import type { Bot, ChatFile, Message, PlanStep, Task } from "@/lib/types";
import Avatar from "@/components/ui/Avatar";
import { bytes, clock } from "@/lib/fmt";
import Markdown from "./Markdown";
import {
  ApprovalCard,
  HandoffLine,
  LimitNotice,
  PlanCard,
  QuestionCard,
  SystemLine,
  TaskCard,
  TaskDoneCard,
  TaskInlineDetail,
  TaskSummaryLine,
} from "./cards";

type Handlers = {
  onPlanApprove: (msg: Message, steps: PlanStep[]) => void;
  onPlanCancel: (msg: Message) => void;
  onApproval: (msg: Message, approve: boolean, reason?: string, alwaysAllow?: boolean) => void;
  onAnswer: (msg: Message, answer: string) => void;
  onOpenTask: (taskId: string, threadId: string) => void;
  onOpenFile: (file: ChatFile) => void;
  busy: boolean;
};

export default function MessageStream({
  messages,
  members,
  tasks,
  me,
  handlers,
  emptyState,
  chatId,
  files = [],
  hasEarlier = false,
  loadingEarlier = false,
  onLoadEarlier,
  focusMessageId,
  onOpenTaskDetail,
  onBranch,
}: {
  messages: Message[];
  members: Bot[];
  tasks: Task[];
  me: { name: string } | null;
  handlers: Handlers;
  emptyState?: React.ReactNode;
  chatId?: string;
  files?: ChatFile[];
  hasEarlier?: boolean;
  loadingEarlier?: boolean;
  onLoadEarlier?: () => Promise<void>;
  focusMessageId?: string | null;
  onOpenTaskDetail?: (taskId: string, threadId: string) => void;
  /** CHT-07：从某条消息拉出分支；分支视图里不传 */
  onBranch?: (msg: Message) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const lastChat = useRef<string | undefined>(undefined);
  const lastFocused = useRef<string | null>(null);
  const prepending = useRef<{ height: number; top: number } | null>(null);
  // 群聊消息流里的子任务详情展开态；按消息记，切群聊时组件重挂载自然清空
  const [openDetails, setOpenDetails] = useState<Record<string, boolean>>({});
  const toggleDetail = (id: string) =>
    setOpenDetails((prev) => ({ ...prev, [id]: !prev[id] }));

  const taskMap = useMemo(() => {
    const m: Record<string, Task> = {};
    tasks.forEach((t) => (m[t.id] = t));
    return m;
  }, [tasks]);

  const botMap = useMemo(() => {
    const m: Record<string, Bot> = {};
    members.forEach((b) => (m[b.id] = b));
    return m;
  }, [members]);

  // 群聊只展示聊天记录：工具调用与返回留在任务线程里，通过任务面板查看
  const items = useMemo(() => {
    const out: Message[] = [];
    const seen = new Set<string>();
    messages.forEach((m) => {
      if (m.type === "tool_call" || m.type === "tool_result") return;
      // 兜底去重：SSE 推送与本地乐观插入可能产生同一条消息
      if (seen.has(m.id)) return;
      seen.add(m.id);
      out.push(m);
    });
    return out;
  }, [messages]);

  useLayoutEffect(() => {
    if (lastChat.current !== chatId) {
      lastChat.current = chatId;
      stick.current = true;
      lastFocused.current = null;
    }
    const el = scroller.current;
    if (el && prepending.current && !loadingEarlier) {
      el.scrollTop = prepending.current.top + el.scrollHeight - prepending.current.height;
      prepending.current = null;
      return;
    }
    if (!focusMessageId && lastFocused.current) {
      lastFocused.current = null;
      stick.current = true;
    }
    if (el && focusMessageId && lastFocused.current !== focusMessageId) {
      const target = el.querySelector<HTMLElement>(`[data-message-id="${focusMessageId}"]`);
      if (target) {
        el.scrollTop += target.getBoundingClientRect().top - el.getBoundingClientRect().top - el.clientHeight / 3;
        stick.current = false;
        lastFocused.current = focusMessageId;
        return;
      }
    }
    // 历史消息异步加载；空列表阶段保持首次定位到底部的状态。
    if (!el || !items.length) {
      stick.current = true;
      return;
    }
    if (!stick.current) return;
    // 历史加载、刷新和流式内容都只调整本容器，绘制前直接定位。
    // scrollIntoView 会平滑滚动并影响祖先容器，切页时尤其明显。
    el.scrollTop = el.scrollHeight;
  }, [items.length, messages[messages.length - 1]?.content, chatId, focusMessageId, loadingEarlier]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  if (!items.length && emptyState) {
    return <div className="flex-1 overflow-y-auto">{emptyState}</div>;
  }

  return (
    <div ref={scroller} onScroll={onScroll} className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
      <div className="mx-auto flex max-w-[1100px] flex-col gap-3">
        {hasEarlier && onLoadEarlier && <button className="btn-ghost mx-auto text-[11.5px]" disabled={loadingEarlier} onClick={async () => {
          const el = scroller.current;
          if (el) prepending.current = { height: el.scrollHeight, top: el.scrollTop };
          await onLoadEarlier();
        }}>{loadingEarlier ? "加载中…" : "加载更早消息"}</button>}
        {items.map((m) => (
          <div key={m.id} data-message-id={m.id} className={focusMessageId === m.id ? "rounded-lg bg-accent/[0.05] p-2 ring-1 ring-accent/30" : undefined}>
          <MessageItem
            msg={m}
            bot={m.sender_bot_id ? botMap[m.sender_bot_id] : undefined}
            me={me}
            members={members}
            files={files}
            taskMap={taskMap}
            handlers={handlers}
            detailOpen={!!openDetails[m.id]}
            onToggleDetail={() => toggleDetail(m.id)}
            onOpenTaskDetail={onOpenTaskDetail}
            onBranch={onBranch}
          />
          </div>
        ))}
        <div className="h-1" />
      </div>
    </div>
  );
}

function MessageItem({
  msg,
  bot,
  me,
  members,
  taskMap,
  handlers,
  files,
  detailOpen,
  onToggleDetail,
  onOpenTaskDetail,
  onBranch,
}: {
  msg: Message;
  bot?: Bot;
  me: { name: string } | null;
  members: Bot[];
  files: ChatFile[];
  taskMap: Record<string, Task>;
  handlers: Handlers;
  detailOpen: boolean;
  onToggleDetail: () => void;
  onOpenTaskDetail?: (taskId: string, threadId: string) => void;
  onBranch?: (msg: Message) => void;
}) {
  const streaming = msg.status === "streaming";

  if (msg.type === "handoff") return <HandoffLine text={msg.content} />;
  if (msg.type === "system")
    return (
      <div className="px-1 py-0.5">
        <SystemLine
          text={msg.meta?.kind === "plan_approved" ? "已确认计划，开始执行" : msg.content}
        />
      </div>
    );
  if (msg.type === "limit") return <LimitNotice text={msg.content} />;
  if (msg.type === "plan")
    return (
      <div className="pl-11">
        <PlanCard
          msg={msg}
          members={members}
          busy={handlers.busy}
          onApprove={(steps) => handlers.onPlanApprove(msg, steps)}
          onCancel={() => handlers.onPlanCancel(msg)}
        />
      </div>
    );
  if (msg.type === "task")
    return (
      <div className="pl-11">
        <TaskCard
          msg={msg}
          live={taskMap[msg.meta?.task_id]}
          onOpen={() => handlers.onOpenTask(msg.meta?.task_id, msg.meta?.thread_id)}
        />
      </div>
    );
  if (msg.type === "task_done")
    return (
      <div className="pl-11">
        <TaskDoneCard
          msg={msg}
          live={taskMap[msg.meta?.task_id]}
          onOpen={() => handlers.onOpenTask(msg.meta?.task_id, msg.meta?.thread_id)}
        />
      </div>
    );
  if (msg.type === "approval")
    return (
      <div className="pl-11">
        <ApprovalCard
          msg={msg}
          busy={handlers.busy}
          onApprove={(alwaysAllow) => handlers.onApproval(msg, true, undefined, alwaysAllow)}
          onReject={(r) => handlers.onApproval(msg, false, r)}
        />
      </div>
    );
  if (msg.type === "question")
    return (
      <div className="pl-11">
        <QuestionCard msg={msg} busy={handlers.busy} onAnswer={(a) => handlers.onAnswer(msg, a)} />
      </div>
    );

  // 普通文本
  if (msg.sender_type === "user") {
    const interjection = msg.meta?.kind === "interjection";
    return (
      <div className="group flex justify-end gap-2.5">
        <div className="max-w-[85%]">
          <div className="rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[13.5px] leading-relaxed text-accentink">
            {interjection && (
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-accentink/65">
                插话 · 执行中补充
              </div>
            )}
            <div className="whitespace-pre-wrap">{msg.content}</div>
          </div>
          <div className="mt-1 flex items-center justify-end gap-1.5 text-[10.5px] text-faint">
            {onBranch && (
              <button
                onClick={() => onBranch(msg)}
                title="从此消息拉出分支"
                className="flex items-center gap-1 rounded px-1 py-0.5 opacity-0 transition-opacity hover:text-accent group-hover:opacity-100"
              >
                <GitBranch size={12} />
                分支
              </button>
            )}
            {clock(msg.created_at)}
          </div>
        </div>
      </div>
    );
  }

  const live = msg.meta?.task_id ? taskMap[msg.meta.task_id] : undefined;
  const mentionName: string | undefined = msg.meta?.mention_name;
  const isSubtask = !!msg.meta?.subtask;
  const isDelegation = !!msg.meta?.delegation;
  const taskStatus = live?.status || msg.meta?.task_status;
  // 发送者可能已移出群聊或已被删除：优先用当前成员信息，其次用消息里补全的历史信息
  const botName = bot?.name || msg.sender_bot_name || "Bot";
  const botAvatar = bot?.avatar || msg.sender_bot_avatar || "bot";
  const botRole = msg.sender_bot_deleted ? undefined : bot?.role || msg.sender_bot_role;
  const hasContent = !!msg.content?.trim();
  // 运行中的 token 数：task.status 事件会实时刷新 chat.tasks 里的 tokens_used
  const tokensSoFar = live?.tokens_used || 0;
  const summaryFallback = {
    status: taskStatus,
    attempt: msg.meta?.attempt,
    started_at: msg.meta?.started_at || live?.started_at,
    tokens_used: isSubtask ? msg.meta?.tokens : tokensSoFar,
  };

  return (
    <div className="group flex gap-2.5">
      <Avatar value={botAvatar} size={32} alt={botName} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[12.5px] font-medium">{botName}</span>
          {botRole && <span className="text-[10.5px] text-faint">{botRole}</span>}
          {msg.meta?.parallel && (
            <span
              className="chip px-1 py-0 text-[10px] text-accent"
              title={`计划第 ${msg.meta.batch} 批，与其他步骤同时执行`}
            >
              并行 · 第 {msg.meta.batch} 批
            </span>
          )}
          <span className="text-[10.5px] text-faint">{clock(msg.created_at)}</span>
          {msg.status === "interrupted" && <span className="text-[10.5px] text-warn">输出已中断</span>}
        </div>
        {streaming && !hasContent ? (
          // 模型还没吐字：不显示空气泡，只显示三点跳动
          <TypingDots className="py-1.5" />
        ) : hasContent ? (
          <div className="flex items-start gap-1.5">
            <div className="max-w-[85%] rounded-2xl rounded-tl-md border border-line bg-panel px-3.5 py-2.5">
              <Markdown text={msg.content} mention={mentionName} />
              {/* 输出中：气泡尾部用三点跳动，不再用竖线光标 */}
              {streaming && <TypingDots className="mt-1.5" />}
            </div>
            {/* 分支入口贴在气泡右侧，避免飘到整行最右边、看不出属于哪条消息 */}
            {onBranch && (
              <button
                onClick={() => onBranch(msg)}
                title="从此消息拉出分支"
                className="mt-2 flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[10.5px] text-faint opacity-0 transition-opacity hover:bg-accent/10 hover:text-accent group-hover:opacity-100"
              >
                <GitBranch size={12} />
                分支
              </button>
            )}
          </div>
        ) : null}
        {/* 任务概要与气泡使用同一个最大宽度，整条消息看起来是一列 */}
        <div className="max-w-[85%]">
          {/* 子 agent 的任务详情：概要随 SSE 实时更新，可原地展开看完整信息 */}
          {(isDelegation || isSubtask) && (
            <TaskSummaryLine live={live} fallback={summaryFallback} onOpen={() => handlers.onOpenTask(msg.meta?.task_id, msg.meta?.thread_id)} />
          )}
          <TaskInlineDetail
            msg={msg}
            live={live}
            open={detailOpen}
            onToggle={onToggleDetail}
            tokensFallback={summaryFallback.tokens_used || 0}
          />
        </div>
        {files.filter((file) => msg.content.includes(file.path)).map((file) => (
          <button key={file.id} onClick={() => handlers.onOpenFile(file)}
            className="mt-2 flex max-w-full items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-2 text-left hover:border-accent/40">
            <FileText size={14} className="shrink-0 text-accent" />
            <span className="truncate text-[12px] text-ink">{file.path}</span>
            <span className="shrink-0 text-[10.5px] text-faint">{bytes(file.size)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** 三点跳动：模型还没吐字、以及流式输出中的「正在说」都用它。 */
function TypingDots({ className = "" }: { className?: string }) {
  return (
    <span className={`flex items-center gap-1 ${className}`} role="status" aria-label="正在输出">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-faint"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  );
}
