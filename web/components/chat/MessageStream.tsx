"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { FileText } from "lucide-react";
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
  ToolBlock,
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
  allMessages,
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
  /** 交给内联详情统计工具调用次数；未传时用当前消息列表 */
  allMessages?: Message[];
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

  // tool_result 挂到对应的 tool_call 上；delegate 的过程由任务卡承载，不再重复展示
  const { items, results, toolGroups } = useMemo(() => {
    const results: Record<string, Message> = {};
    const toolGroups: Record<string, Message[]> = {};
    const items: Message[] = [];
    const seen = new Set<string>();
    messages.forEach((m) => {
      if (m.type === "tool_result") {
        const id = m.meta?.tool_call_id;
        if (id) results[`${m.run_id}:${id}`] = m;
        return;
      }
      // 兜底去重：SSE 推送与本地乐观插入可能产生同一条消息
      if (seen.has(m.id)) return;
      seen.add(m.id);
      if (m.type === "tool_call") {
        const name = m.meta?.tool_calls?.[0]?.function?.name;
        // 这三类控制工具都有专门的卡片承载，不再重复展示原始调用
        if (name === "delegate" || name === "propose_plan" || name === "ask_user") return;
        const previous = items[items.length - 1];
        if (previous?.sender_type === "bot" && previous.sender_bot_id === m.sender_bot_id
          && previous.run_id === m.run_id && previous.thread_id === m.thread_id
          && ["text", "tool_call"].includes(previous.type)
          && !previous.meta?.delegation && !previous.meta?.subtask) {
          (toolGroups[previous.id] ||= []).push(m);
          return;
        }
        toolGroups[m.id] = [m];
      }
      items.push(m);
    });
    return { items, results, toolGroups };
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
            results={results}
            tools={toolGroups[m.id] || []}
            files={files}
            taskMap={taskMap}
            handlers={handlers}
            detailMessages={allMessages || messages}
            detailOpen={!!openDetails[m.id]}
            onToggleDetail={() => toggleDetail(m.id)}
            onOpenTaskDetail={onOpenTaskDetail}
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
  results,
  tools,
  taskMap,
  handlers,
  files,
  detailMessages,
  detailOpen,
  onToggleDetail,
  onOpenTaskDetail,
}: {
  msg: Message;
  bot?: Bot;
  me: { name: string } | null;
  members: Bot[];
  results: Record<string, Message>;
  tools: Message[];
  files: ChatFile[];
  taskMap: Record<string, Task>;
  handlers: Handlers;
  detailMessages: Message[];
  detailOpen: boolean;
  onToggleDetail: () => void;
  onOpenTaskDetail?: (taskId: string, threadId: string) => void;
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
  if (msg.type === "tool_call") {
    return (
      <div className="flex gap-2.5">
        <Avatar value={bot?.avatar || msg.sender_bot_avatar || "🤖"} size={32} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[12.5px] font-medium">{bot?.name || msg.sender_bot_name || "Bot"}</span>
            <span className="text-[10.5px] text-faint">{bot?.role || msg.sender_bot_role}</span>
            <span className="text-[10.5px] text-faint">{clock(msg.created_at)}</span>
          </div>
          <AgentTools tools={tools} results={results} />
        </div>
      </div>
    );
  }
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
      <div className="flex justify-end gap-2.5">
        <div className="max-w-[85%]">
          <div className="rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[13.5px] leading-relaxed text-accentink">
            {interjection && (
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-accentink/65">
                插话 · 执行中补充
              </div>
            )}
            <div className="whitespace-pre-wrap">{msg.content}</div>
          </div>
          <div className="mt-1 text-right text-[10.5px] text-faint">{clock(msg.created_at)}</div>
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
  const botAvatar = bot?.avatar || msg.sender_bot_avatar || "🤖";
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
    <div className="flex gap-2.5">
      <Avatar value={botAvatar} size={32} alt={botName} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[12.5px] font-medium">{botName}</span>
          {botRole && <span className="text-[10.5px] text-faint">{botRole}</span>}
          <span className="text-[10.5px] text-faint">{clock(msg.created_at)}</span>
          {msg.status === "interrupted" && <span className="text-[10.5px] text-warn">输出已中断</span>}
        </div>
        {streaming && !hasContent ? (
          // 模型还没吐字：不显示空气泡和光标，只显示三点跳动
          <div className="flex items-center gap-1 py-1.5" aria-label="正在输入">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="h-1.5 w-1.5 animate-bounce rounded-full bg-faint"
                style={{ animationDelay: `${i * 0.15}s` }}
              />
            ))}
          </div>
        ) : hasContent ? (
          <div className="max-w-[85%] rounded-2xl rounded-tl-md border border-line bg-panel px-3.5 py-2.5">
            <Markdown text={msg.content} mention={mentionName} />
            {streaming && (
              <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulseSoft bg-accent align-middle" />
            )}
          </div>
        ) : null}
        {tools.length > 0 && <AgentTools tools={tools} results={results} />}
        {/* 子 agent 的任务详情：概要随 SSE 实时更新，可原地展开看完整信息 */}
        {(isDelegation || isSubtask) && (
          <TaskSummaryLine live={live} fallback={summaryFallback} onOpen={() => handlers.onOpenTask(msg.meta?.task_id, msg.meta?.thread_id)} />
        )}
        <TaskInlineDetail
          msg={msg}
          live={live}
          messages={detailMessages}
          open={detailOpen}
          onToggle={onToggleDetail}
          tokensFallback={summaryFallback.tokens_used || 0}
        />
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

function AgentTools({ tools, results }: { tools: Message[]; results: Record<string, Message> }) {
  return <div className="mt-1.5 space-y-2">
    {tools.map((m) => <div key={m.id}>
      {m.content && <div className="mb-1"><Markdown text={m.content} size="sm" /></div>}
      {/* 群聊里默认展开：子 Bot 的执行过程要能直接看到，不用再点进任务面板 */}
      <ToolBlock msg={m} result={results[`${m.run_id}:${m.meta?.tool_calls?.[0]?.id}`]}
        defaultOpen
        ended={["completed", "failed", "stopped"].includes(m.meta?.run_status)} />
    </div>)}
  </div>;
}
