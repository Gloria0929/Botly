"use client";

import {
  Compass,
  Plus,
  SlidersHorizontal,
  Users,
  Workflow,
} from "lucide-react";
import type { Message, Run, Task } from "@/lib/types";

export function NoChatState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="chat-welcome flex h-full flex-col items-center justify-center overflow-y-auto px-6 py-10 text-center">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-panel2 text-ink">
        <Workflow size={30} strokeWidth={1.3} />
      </div>
      <h1 className="text-[28px] font-medium tracking-tight sm:text-[32px]">
        和你的 AI 团队，一起工作。
      </h1>
      <p className="mt-4 max-w-[400px] text-[14px] leading-7 text-muted">
        从一个目标开始。让不同角色的智能体
        <br className="hidden sm:block" />
        一起研究、协作，并把结果交付给你。
      </p>
      <button className="btn-primary mt-7 rounded-full px-5" onClick={onCreate}>
        <Plus size={15} />
        新建会话
      </button>
      <div className="mt-12 flex flex-wrap justify-center gap-5 text-[11px] text-faint">
        {[
          { icon: Users, label: "多角色协作" },
          { icon: Workflow, label: "过程可见" },
          { icon: SlidersHorizontal, label: "关键操作可控" },
        ].map(({ icon: Icon, label }) => (
          <span key={label} className="flex items-center gap-1.5">
            <Icon size={13} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

export function statusLabel(s?: string) {
  return s === "working" ? "执行中" : s === "waiting" ? "等待处理" : "空闲";
}

export function runStatusText(
  run: Run | null,
  tasks: Task[],
  messages: Message[],
) {
  if (run?.kind === "free") return run.status === "waiting_children"
    ? "群聊进行中，可继续发送消息"
    : "";
  if (
    !run ||
    run.target_bot_id ||
    !["running", "waiting_children"].includes(run.status)
  )
    return "";
  const activeTasks = tasks.some(
    (task) =>
      task.run_id === run.id &&
      ["queued", "running", "waiting_approval", "waiting_reply"].includes(
        task.status,
      ),
  );
  const answered = new Set(
    messages
      .filter((m) => m.run_id === run.id && m.type === "tool_result")
      .map((m) => m.meta?.tool_call_id),
  );
  const dispatching = messages.some(
    (m) =>
      m.run_id === run.id &&
      m.type === "tool_call" &&
      m.status === "done" &&
      !m.meta?.subtask_tool &&
      m.meta?.tool_calls?.some(
        (call: { id: string; function?: { name?: string } }) =>
          call.function?.name === "delegate" && !answered.has(call.id),
      ),
  );
  if (!activeTasks && !dispatching) return "";
  return run.status === "waiting_children"
    ? "等待并行子任务返回…"
    : "总指挥正在调度…";
}

export function EmptyState({ onPick }: { onPick: (t: string) => void }) {
  const samples = [
    "调研 3 家国内向量数据库厂商，写一份 1500 字对比报告。",
    "把这份需求文档拆成开发任务，评估每个任务的复杂度。",
    "查一下最近一周 AI Agent 领域的三个重要发布，整理成简报。",
  ];
  return (
    <div className="chat-welcome flex h-full flex-col items-center justify-center overflow-y-auto px-6 py-8">
      <div className="w-full max-w-[560px]">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-panel">
            <Compass size={20} />
          </div>
          <h2 className="text-[26px] font-semibold tracking-tight">
            今天，和团队一起完成什么？
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
            总指挥会判断复杂度、拆解任务、派给合适的成员。涉及高风险动作时会停下来等你审批。
          </p>
        </div>
        <div className="space-y-2">
          {samples.map((s) => (
            <button
              key={s}
              onClick={() => onPick(s)}
              className="group flex w-full items-center gap-4 rounded-xl bg-panel2/70 px-5 py-4 text-left text-[13px] leading-relaxed text-muted transition-colors hover:bg-panel3 hover:text-ink"
            >
              <span className="flex-1">{s}</span>
              <Plus
                size={15}
                className="shrink-0 text-faint group-hover:text-accent"
              />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
