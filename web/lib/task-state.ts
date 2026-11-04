import type { Bot, Chat, ChatFile, Message, Run, Task } from "./types";

const ACTIVE = new Set(["queued", "running", "waiting", "waiting_children"]);
const ACTIVE_TASK = new Set(["queued", "running", "waiting_approval", "waiting_reply"]);

export function memberActivity(bot: Bot, messages: Message[], tasks: Task[]) {
  const own = tasks.find((task) => task.assigned_bot_id === bot.id && ACTIVE_TASK.has(task.status));
  if (own) {
    const label = ({ queued: "等待开始任务", running: "正在执行任务", waiting_approval: "等待你审批",
      waiting_reply: "等待你的答复" } as Record<string, string>)[own.status];
    return { kind: own.status === "running" ? "working" : "waiting", label, task: own };
  }
  const delegated = tasks.find((task) => ACTIVE_TASK.has(task.status) && messages.some((message) =>
    message.sender_bot_id === bot.id && message.meta?.delegation && message.meta?.task_id === task.id));
  if (delegated) return { kind: "waiting", label: `等待${delegated.bot_name || "同伴"}完成任务`, task: delegated };
  return bot.status === "working" ? { kind: "thinking", label: "正在思考…", task: undefined } : null;
}

export function showMemberActivity(bot: Bot, messages: Message[], tasks: Task[], freeChat: boolean) {
  const activity = memberActivity(bot, messages, tasks);
  return !!activity && !(freeChat && activity.kind === "thinking");
}

/** 顶层执行已结束时立即清除输入状态，不等下一次快照。 */
export function applyRunCompletion(chat: Chat | null, payload: { run_id: string; status: Run["status"]; end_reason?: string | null }) {
  if (!chat || !["completed", "failed", "stopped"].includes(payload.status)) return chat;
  if (chat.active_run?.id !== payload.run_id && !chat.active_runs?.some((run) => run.id === payload.run_id)) return chat;
  const remaining = (chat.active_runs || []).filter((run) => run.id !== payload.run_id);
  const active = chat.active_run?.id === payload.run_id ? remaining[0] || null : chat.active_run;
  const ended = chat.active_run?.id === payload.run_id ? { ...chat.active_run, status: payload.status,
    end_reason: payload.end_reason ?? chat.active_run.end_reason } : chat.last_run;
  return { ...chat, active_run: active, active_runs: remaining, last_run: ended,
    members: chat.members.map((bot) => {
      const ids = (bot.typing_run_ids || []).filter((id) => id !== payload.run_id);
      if (!active && !remaining.length) return { ...bot, typing_run_ids: [], status: "idle" as const };
      if (bot.typing_run_ids?.includes(payload.run_id)) return { ...bot, typing_run_ids: ids,
        status: ids.length ? "working" as const : "idle" as const };
      return bot;
    }) };
}

export function canRetryTask(task: Task, run: Run | null): boolean {
  return ["failed", "canceled"].includes(task.status) && (!run || !ACTIVE.has(run.status));
}

export function taskFiles(files: ChatFile[], message: Message): ChatFile[] {
  const explicitNames = new Set(files.filter((file) => message.content.includes(file.path))
    .map((file) => file.path.split("/").pop()!));
  const owned = files.filter((file) => file.path.startsWith("tasks/") && file.run_id
    && file.run_id === message.run_id && file.created_by_bot_id === message.sender_bot_id);
  const ids = new Set(owned.map((file) => file.id));
  const names = new Map<string, number>();
  for (const file of owned) {
    const name = file.path.split("/").pop()!;
    names.set(name, (names.get(name) || 0) + 1);
  }
  return files.filter((file) => message.content.includes(file.path)
    || (ids.has(file.id) && !explicitNames.has(file.path.split("/").pop()!)
      && names.get(file.path.split("/").pop()!) === 1
      && message.content.includes(file.path.split("/").pop()!)));
}

export function taskCounts(tasks: Task[]) {
  return {
    total: tasks.length,
    running: tasks.filter((task) => task.status === "running").length,
    queued: tasks.filter((task) => task.status === "queued").length,
    waiting: tasks.filter((task) => ["waiting_approval", "waiting_reply"].includes(task.status)).length,
    completed: tasks.filter((task) => task.status === "completed").length,
    failed: tasks.filter((task) => task.status === "failed").length,
  };
}

export function teamStatus(run: Run | null, tasks: Task[]): string {
  if (!run) return "等待开始任务";
  const counts = taskCounts(tasks);
  if (run.status === "waiting") {
    return ({ plan: "等待你确认执行计划", approval: "等待你处理审批", question: "等待你回复问题",
      step: "等待你确认下一步" } as Record<string, string>)[run.resume?.kind || ""] || "等待你处理";
  }
  if (run.status === "waiting_children") {
    return counts.waiting ? `等待子任务返回 · ${counts.waiting} 项需你处理` : "等待并行子任务返回";
  }
  if (run.status === "queued") return "已进入执行队列";
  if (run.status === "running") return counts.running ? `${counts.running} 个子任务正在执行` : "智能体正在处理";
  return ({ completed: "本次执行已结束", failed: "本次执行失败，可查看原因并重试单项", stopped: "本次执行已停止" })[run.status] || run.status;
}
