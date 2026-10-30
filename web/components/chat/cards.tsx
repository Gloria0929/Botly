"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Coins,
  FileText,
  HelpCircle,
  Info,
  ListChecks,
  Loader2,
  Pencil,
  Play,
  Plus,
  ShieldAlert,
  Target,
  Terminal,
  Wrench,
  X,
} from "lucide-react";
import type { Bot, Message, PlanStep, Task } from "@/lib/types";
import Avatar from "@/components/ui/Avatar";
import Select from "@/components/ui/Select";
import { bytes, clock, countdown, num } from "@/lib/fmt";

/* ------------------------------------------------------------------ */
/* 计划卡片                                                            */
/* ------------------------------------------------------------------ */
export function PlanCard({
  msg,
  members,
  onApprove,
  onCancel,
  busy,
}: {
  msg: Message;
  members: Bot[];
  onApprove: (steps: PlanStep[]) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [steps, setSteps] = useState<PlanStep[]>(msg.meta?.steps || []);
  const [editing, setEditing] = useState(false);
  const rawStatus = msg.meta?.status || "pending";
  const status = rawStatus === "pending" && ["completed", "failed", "stopped"].includes(msg.meta?.run_status) ? "canceled" : rawStatus;

  useEffect(() => {
    if (!editing) setSteps(msg.meta?.steps || []);
  }, [msg.meta?.steps, editing]);

  const pending = status === "pending";
  // 服务端确认/取消后自动退出编辑；请求失败时保持编辑态，避免丢掉修改
  useEffect(() => {
    if (!pending) setEditing(false);
  }, [pending]);

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line bg-panel2/50 px-4 py-2.5">
        <ListChecks size={15} className="text-accent" />
        <span className="text-[13px] font-medium">执行计划</span>
        <span className="chip ml-1">{steps.length} 步</span>
        <span className="ml-auto text-[11.5px] text-faint">
          {status === "pending" ? "等待确认" : status === "approved" ? "已确认" : "已取消"}
        </span>
      </div>

      <div className="divide-y divide-line">
        {steps.map((s, i) => (
          <div key={i} className="flex items-start gap-3 px-4 py-3">
            <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line bg-panel2 text-[11px] text-muted">
              {i + 1}
            </div>
            <div className="min-w-0 flex-1">
              {editing ? (
                <div className="space-y-1.5">
                  <input
                    className="field py-1.5"
                    value={s.title}
                    onChange={(e) => {
                      const next = [...steps];
                      next[i] = { ...s, title: e.target.value };
                      setSteps(next);
                    }}
                  />
                  <div className="flex gap-1.5">
                    <Select
                      size="sm"
                      className="flex-1"
                      value={s.bot}
                      onChange={(v) => {
                        const next = [...steps];
                        next[i] = { ...s, bot: v };
                        setSteps(next);
                      }}
                      options={members.map((m) => ({
                        value: m.name,
                        label: m.name,
                        avatar: m.avatar,
                      }))}
                    />
                    <button
                      className="btn-ghost shrink-0 px-2"
                      onClick={() => setSteps(steps.filter((_, idx) => idx !== i))}
                    >
                      <X size={13} />
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="text-[13px] leading-snug">{s.title}</div>
                  <div className="mt-1 flex items-center gap-2 text-[11.5px] text-faint">
                    <span className="chip">{s.bot}</span>
                    {s.expected && <span className="truncate">产出：{s.expected}</span>}
                  </div>
                </>
              )}
            </div>
          </div>
        ))}
        {editing && (
          <div className="px-4 py-2">
            <button
              className="btn-ghost"
              onClick={() =>
                setSteps([
                  ...steps,
                  { title: "", bot: members[0]?.name || "", expected: "" },
                ])
              }
            >
              <Plus size={13} /> 添加步骤
            </button>
          </div>
        )}
      </div>

      {pending && (
        <div className="flex items-center gap-2 border-t border-line bg-panel2/40 px-4 py-2.5">
          {editing ? (
            <>
              <button
                className="btn-primary"
                disabled={busy || steps.some((s) => !s.title.trim() || !s.bot)}
                onClick={() => {
                  // 不在这里退出编辑：请求失败时保留用户改过的步骤
                  onApprove(steps);
                }}
              >
                保存并执行
              </button>
              <button className="btn-ghost" onClick={() => setEditing(false)}>
                取消编辑
              </button>
            </>
          ) : (
            <>
              <button className="btn-primary" disabled={busy} onClick={() => onApprove(steps)}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} 开始执行
              </button>
              <button className="btn-ghost" onClick={() => setEditing(true)}>
                <Pencil size={13} /> 编辑
              </button>
              <button className="btn-ghost ml-auto" onClick={onCancel} disabled={busy}>
                取消
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 任务卡片                                                            */
/* ------------------------------------------------------------------ */
const TASK_STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: "排队中", cls: "text-faint" },
  running: { label: "进行中", cls: "text-accent" },
  waiting_approval: { label: "等待审批", cls: "text-warn" },
  waiting_reply: { label: "等待回复", cls: "text-warn" },
  completed: { label: "已完成", cls: "text-ok" },
  failed: { label: "失败", cls: "text-danger" },
  canceled: { label: "已取消", cls: "text-faint" },
};

export function TaskCard({
  msg,
  live,
  onOpen,
}: {
  msg: Message;
  live?: Task;
  onOpen: () => void;
}) {
  const m = msg.meta || {};
  const status = live?.status || m.status;
  const botName = live?.bot_name || m.bot_name;
  const avatar = live?.bot_avatar || m.bot_avatar;
  const st = TASK_STATUS[status] || TASK_STATUS.queued;
  return (
    <button
      onClick={onOpen}
      className="group flex w-full items-start gap-3 rounded-xl border border-line bg-panel2/40 px-3.5 py-2.5 text-left transition-colors hover:border-accent/40 hover:bg-panel2"
    >
      <Avatar value={avatar || botName?.slice(0, 1)} size={28} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[12.5px] font-medium">{botName}</span>
          <span className={`text-[11px] ${st.cls}`}>{st.label}</span>
          <ChevronRight
            size={13}
            className="ml-auto text-faint opacity-0 transition-opacity group-hover:opacity-100"
          />
        </div>
        <div className="mt-0.5 line-clamp-2 text-[12px] leading-relaxed text-muted">
          {m.instruction || msg.content}
        </div>
      </div>
    </button>
  );
}

export function TaskDoneCard({
  msg,
  live,
  onOpen,
}: {
  msg: Message;
  live?: Task;
  onOpen: () => void;
}) {
  const m = msg.meta || {};
  return (
    <div className="flex items-start gap-3 rounded-xl border border-ok/25 bg-ok/[0.06] px-3.5 py-2.5">
      <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-ok" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          <span className="font-medium">{live?.bot_name || m.bot_name}</span>
          <span className="text-muted">已完成子任务</span>
          {m.elapsed && (
            <span className="flex items-center gap-1 text-[11px] text-faint">
              <Clock3 size={11} /> {m.elapsed}
            </span>
          )}
          {m.tokens > 0 && <span className="text-[11px] text-faint">{num(m.tokens)} tokens</span>}
        </div>
        {m.summary && (
          <div className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-muted">{m.summary}</div>
        )}
      </div>
      <button onClick={onOpen} className="shrink-0 text-[11.5px] text-accent hover:underline">
        查看
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 子 agent 详情：群聊里实时可见                                         */
/* ------------------------------------------------------------------ */
const LIVE_STATUS = new Set(["queued", "running", "waiting_approval", "waiting_reply"]);

function elapsedText(task: Task, now: number): string {
  if (!task.started_at) return "";
  const start = Date.parse(task.started_at);
  if (Number.isNaN(start)) return "";
  const end = task.ended_at ? Date.parse(task.ended_at) : now;
  const sec = Math.max(0, Math.round((end - start) / 1000));
  return sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`;
}

/** 挂在下发消息/子 Bot 回复下方的一行概要，随任务状态实时更新。 */
export function TaskSummaryLine({
  live,
  fallback,
  onOpen,
  tokensFallback = 0,
}: {
  live?: Task;
  fallback?: Partial<Task> & { task_id?: string };
  onOpen?: () => void;
  tokensFallback?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  const status = live?.status || fallback?.status;
  const running = !!status && LIVE_STATUS.has(status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const attempt = live?.attempt ?? fallback?.attempt;
  const tokens = live?.tokens_used || fallback?.tokens_used || tokensFallback || 0;
  const st = status ? TASK_STATUS[status] || TASK_STATUS.queued : null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10.5px] text-faint">
      {st && (
        <span className={`flex items-center gap-1 ${st.cls}`}>
          {running && <Loader2 size={10} className="animate-spin" />}
          {st.label}
        </span>
      )}
      {!!attempt && attempt > 1 && <span>· 第 {attempt} 次执行</span>}
      {live?.started_at && <span>· 已运行 {elapsedText(live, now) || "—"}</span>}
      <span>· {num(tokens)} tokens</span>
      {onOpen && (
        <button onClick={onOpen} className="text-accent hover:underline">
          详情
        </button>
      )}
    </div>
  );
}

/** 在群聊消息流里原地展开的子任务详情，内容与任务面板一致。 */
export function TaskInlineDetail({
  msg,
  live,
  messages,
  open,
  onToggle,
  tokensFallback = 0,
}: {
  msg: Message;
  live?: Task;
  messages: Message[];
  open: boolean;
  onToggle: () => void;
  tokensFallback?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  const threadId = live?.thread_id || msg.meta?.thread_id || msg.meta?.task_id;
  const taskId = live?.id || msg.meta?.task_id;
  const mine = useMemo(
    () =>
      messages.filter(
        (m) =>
          (threadId && m.thread_id === threadId) ||
          // 群聊里的工具调用是镜像消息，thread_id 仍是主线程，靠 meta.task_id 关联
          (taskId && m.meta?.task_id === taskId)
      ),
    [messages, threadId, taskId]
  );
  const toolCount = mine.filter((m) => m.type === "tool_call").length;
  const instruction = live?.instruction || msg.meta?.delegation_instruction || "";
  const expected = live?.expected_output || msg.meta?.expected_output || "";
  const status = live?.status || msg.meta?.task_status;
  const running = !!status && LIVE_STATUS.has(status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const st = status ? TASK_STATUS[status] || TASK_STATUS.queued : null;

  return (
    <div className="mt-1.5">
      <button
        onClick={onToggle}
        className="flex items-center gap-1 text-[11px] text-accent hover:underline"
        aria-expanded={open}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {open ? "收起详情" : "查看详情"}
      </button>
      {open && (
        <div className="mt-1.5 max-w-[85%] space-y-2 rounded-lg border border-line bg-panel2/50 px-3 py-2.5 text-[11.5px] leading-relaxed text-muted">
          {instruction && (
            <div className="flex gap-1.5">
              <ListChecks size={12} className="mt-0.5 shrink-0 text-faint" />
              <div className="min-w-0">
                <div className="text-[10.5px] text-faint">任务指令</div>
                <div className="whitespace-pre-wrap break-words">{instruction}</div>
              </div>
            </div>
          )}
          {expected && (
            <div className="flex gap-1.5">
              <Target size={12} className="mt-0.5 shrink-0 text-faint" />
              <div className="min-w-0">
                <div className="text-[10.5px] text-faint">期望产出</div>
                <div className="whitespace-pre-wrap break-words">{expected}</div>
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-faint">
            {st && <span className={st.cls}>{st.label}</span>}
            <span className="flex items-center gap-1"><Coins size={10} /> {num(live?.tokens_used || tokensFallback)} tokens</span>
            <span className="flex items-center gap-1"><Wrench size={10} /> {toolCount} 次工具调用</span>
            {!!live?.attempt && <span>第 {live.attempt} 次执行</span>}
            {live?.started_at && <span className="flex items-center gap-1"><Clock3 size={10} /> {elapsedText(live, now) || "—"}</span>}
          </div>
          <div className="flex items-center gap-1 text-[10.5px] text-faint">
            <Info size={10} /> 执行过程逐条显示在群聊里，完整任务面板在右侧「任务」
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 审批卡片                                                            */
/* ------------------------------------------------------------------ */
export function ApprovalCard({
  msg,
  onApprove,
  onReject,
  busy,
}: {
  msg: Message;
  onApprove: (alwaysAllow: boolean) => void;
  onReject: (reason: string) => void;
  busy: boolean;
}) {
  const m = msg.meta || {};
  const [open, setOpen] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [alwaysAllow, setAlwaysAllow] = useState(false);
  const runEnded = ["completed", "failed", "stopped"].includes(m.run_status);
  const status = m.status === "pending" && runEnded ? "invalidated" : m.status;
  const pending = status === "pending";
  const canAlwaysAllow = m.level !== "L3";
  // 剩余有效期每 30 秒刷新一次，避免卡片长时间不动显示过期时间
  const [, tick] = useState(0);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => tick((v) => v + 1), 30_000);
    return () => clearInterval(timer);
  }, [pending]);

  return (
    <div className="overflow-hidden rounded-xl border border-warn/35 bg-warn/[0.07]">
      <div className="flex items-center gap-2 border-b border-warn/25 px-3.5 py-2.5">
        <ShieldAlert size={15} className="text-warn" />
        <span className="text-[12.5px] font-medium text-warn">{pending ? "需要审批" : status === "invalidated" ? "审批已失效" : "审批记录"}</span>
        <span className="chip border-warn/30 text-warn/90">{m.level || "L3"}</span>
        <span className="ml-auto text-[11px] text-warn/70">
          {pending ? countdown(m.expires_at) : statusText(status)}
        </span>
      </div>

      <div className="px-3.5 py-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[12px] text-muted">{m.bot_name}</span>
          <span className="text-[12px] text-muted">请求执行</span>
          <code className="rounded bg-panel2 px-1.5 py-0.5 text-[12px] font-medium text-ink">
            {m.tool}
          </code>
        </div>
        {m.reason && <p className="mt-2 text-[12px] leading-relaxed text-muted">{m.reason}</p>}

        <button
          onClick={() => setOpen(!open)}
          className="mt-2 flex items-center gap-1 text-[11.5px] text-muted hover:text-ink"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          参数
        </button>
        {open && (
          <pre className="mt-1.5 max-h-48 overflow-auto rounded-lg border border-line bg-panel p-2.5 text-[11.5px] leading-relaxed text-muted">
            {JSON.stringify(m.args || {}, null, 2)}
          </pre>
        )}

        {rejecting && pending && (
          <input
            autoFocus
            className="field mt-2.5"
            placeholder="拒绝原因（会回传给 Bot，便于它调整）"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        )}

        {pending ? (
          <>
            {canAlwaysAllow && (
              <label className="mt-3 flex cursor-pointer items-center gap-2 text-[11.5px] text-muted">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-[var(--warn)]"
                  checked={alwaysAllow}
                  onChange={(e) => setAlwaysAllow(e.target.checked)}
                />
                在本群聊始终允许 <code className="rounded bg-panel2 px-1 text-[11px]">{m.tool}</code>
                <span className="text-faint">（可到群聊设置撤销）</span>
              </label>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button
                className="btn-primary bg-warn text-black hover:bg-warn/90"
                disabled={busy}
                onClick={() => onApprove(alwaysAllow)}
              >
                <Check size={14} /> 批准
              </button>
              {rejecting ? (
                <>
                  <button
                    className="btn-danger"
                    disabled={busy}
                    onClick={() => onReject(reason || "用户拒绝")}
                  >
                    确认拒绝
                  </button>
                  <button className="btn-ghost" onClick={() => setRejecting(false)}>
                    返回
                  </button>
                </>
              ) : (
                <button className="btn-ghost" onClick={() => setRejecting(true)} disabled={busy}>
                  <X size={14} /> 拒绝
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="mt-2 text-[11.5px] text-muted">
            {status === "invalidated"
              ? `${m.closed_reason || (m.run_status === "stopped" ? "执行已停止" : "所属执行已结束")}，此审批已失效，无需再操作。`
              : status === "rejected" && m.reject_reason
              ? `已拒绝：${m.reject_reason}`
              : statusText(status)}
          </div>
        )}
      </div>
    </div>
  );
}

function statusText(s: string) {
  return (
    { approved: "已批准", rejected: "已拒绝", expired: "已过期", invalidated: "已失效" }[s] || s
  );
}

/* ------------------------------------------------------------------ */
/* 提问卡片                                                            */
/* ------------------------------------------------------------------ */
export function QuestionCard({
  msg,
  onAnswer,
  busy,
}: {
  msg: Message;
  onAnswer: (a: string) => void;
  busy: boolean;
}) {
  const m = msg.meta || {};
  const [text, setText] = useState("");
  const pending = m.status === "pending" && !["completed", "failed", "stopped"].includes(m.run_status);
  return (
    <div className="overflow-hidden rounded-xl border border-accent/35 bg-accent/[0.06]">
      <div className="flex items-center gap-2 border-b border-accent/20 px-3.5 py-2.5">
        <HelpCircle size={15} className="text-accent" />
        <span className="text-[12.5px] font-medium text-accent">提问</span>
        <span className="ml-auto text-[11px] text-muted">{m.bot_name}</span>
      </div>
      <div className="px-3.5 py-3">
        <p className="text-[13px] leading-relaxed">{msg.content}</p>
        {pending ? (
          <div className="mt-2.5 flex gap-2">
            <input
              className="field"
              placeholder="输入回复，执行会从这里继续"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
                if (e.key === "Enter" && text.trim()) onAnswer(text.trim());
              }}
            />
            <button
              className="btn-primary shrink-0"
              disabled={busy || !text.trim()}
              onClick={() => onAnswer(text.trim())}
            >
              回复
            </button>
          </div>
        ) : (
          <div className="mt-2 rounded-lg bg-panel2 px-2.5 py-1.5 text-[12px] text-muted">
            {m.status === "answered" ? `已回复：${m.answer || ""}` : `${m.closed_reason || "问题已过期或所属执行已结束"}，无需再回复。`}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 工具调用折叠块                                                       */
/* ------------------------------------------------------------------ */
export function ToolBlock({ msg, result, defaultOpen = false, ended = false }: {
  msg: Message; result?: Message; defaultOpen?: boolean; ended?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const calls = msg.meta?.tool_calls || [];
  const names = calls.map((c: any) => c.function?.name).join(", ");
  const failed = (result?.content || "").includes("失败") || (result?.content || "").includes("拒绝");

  return (
    <div className="rounded-lg border border-line bg-panel2/30">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        <Terminal size={12} className="text-faint" />
        <span className="font-mono text-[11.5px] text-muted">{names}</span>
        <span
          className={`min-w-0 flex-1 truncate text-[11px] ${failed ? "text-warn/80" : "text-faint"}`}
        >
          {result ? result.content.slice(0, 46).replace(/\n/g, " ") : ended ? "未返回，执行已结束" : "执行中…"}
        </span>
        <ChevronDown
          size={12}
          className={`ml-auto shrink-0 text-faint transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-3 py-2.5">
          {calls.map((c: any, i: number) => (
            <div key={i}>
              <div className="mb-1 text-[11px] text-faint">参数</div>
              <ToolArguments raw={c.function?.arguments} />
            </div>
          ))}
          {result && (
            <div>
              <div className="mb-1 text-[11px] text-faint">返回</div>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded border border-line bg-panel p-2 text-[11px] leading-relaxed text-muted">
                {result.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ToolArguments({ raw }: { raw: string }) {
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(raw || "{}");
    if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error();
  } catch {
    return <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-[11px]">{raw}</pre>;
  }
  return <div className="space-y-2">{Object.entries(args).map(([key, value]) => <div key={key}>
    <div className="mb-1 font-mono text-[10.5px] text-faint">{key}</div>
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-panel p-2 text-[11px] leading-relaxed text-muted">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  </div>)}</div>;
}

/* ------------------------------------------------------------------ */
/* 系统提示 / 交接 / 限额                                               */
/* ------------------------------------------------------------------ */
export function SystemLine({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-0.5 text-[11.5px] text-faint">
      <CircleDot size={10} />
      <span className="whitespace-pre-wrap">{text}</span>
    </div>
  );
}

export function HandoffLine({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-faint">
      <span className="h-px w-6 bg-line" />
      <span className="font-mono">{text}</span>
      <span className="h-px flex-1 bg-line" />
    </div>
  );
}

export function LimitNotice({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/[0.07] px-3.5 py-2.5">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-danger" />
      <div className="text-[12.5px] leading-relaxed text-danger/90">{text}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 文件卡片                                                            */
/* ------------------------------------------------------------------ */
export function FileChip({ path, size, onOpen }: { path: string; size?: number; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel2/50 px-2.5 py-1.5 text-[12px] text-muted transition-colors hover:border-accent/40 hover:text-ink"
    >
      <FileText size={13} className="text-accent" />
      <span className="font-mono">{path}</span>
      {size !== undefined && <span className="text-[10.5px] text-faint">{bytes(size)}</span>}
    </button>
  );
}
