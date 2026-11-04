import type { ChatFile, Message, Run, Task } from "./types";

const ACTIVE = new Set(["queued", "running", "waiting", "waiting_children"]);

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
