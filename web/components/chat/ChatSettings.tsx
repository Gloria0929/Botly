"use client";

import { useEffect, useState } from "react";
import { Gauge, Loader2, ShieldCheck, Workflow, X } from "lucide-react";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { api, toastError, errText } from "@/lib/api";
import type { Chat } from "@/lib/types";
import { toast } from "@/lib/ui";

/**
 * 群聊设置：运行模式、token 预算覆盖、L2 写入审批、始终允许的工具（APV-06）。
 * 改动即时保存，只影响当前群聊。
 */
export default function ChatSettings({
  chat,
  open,
  onClose,
  onSaved,
}: {
  chat: Chat;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [mode, setMode] = useState(chat.run_mode);
  const [requireWrite, setRequireWrite] = useState(chat.require_approval_for_write);
  const [allowed, setAllowed] = useState<string[]>(chat.always_allow_tools || []);
  // 限额覆盖：留空表示继承设置页里的默认值（LIM-01）
  const [budgetInput, setBudgetInput] = useState(
    chat.limits_override?.token_budget != null ? String(chat.limits_override.token_budget) : ""
  );
  // RUN-09：委派深度覆盖（1 = 只有总指挥能派任务，2 = 允许子 Bot 再派一层）
  const [depthInput, setDepthInput] = useState(
    chat.limits_override?.delegation_depth != null
      ? String(chat.limits_override.delegation_depth)
      : ""
  );
  const [tools, setTools] = useState<Record<string, { description: string; level: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMode(chat.run_mode);
    setRequireWrite(chat.require_approval_for_write);
    setAllowed(chat.always_allow_tools || []);
  }, [chat.id, chat.run_mode, chat.require_approval_for_write, chat.always_allow_tools]);

  // 限额输入单独同步：只跟随服务端的覆盖值，避免群聊刷新（数组引用变化）
  // 把用户正在输入的内容清掉。
  useEffect(() => {
    setBudgetInput(chat.limits_override?.token_budget != null ? String(chat.limits_override.token_budget) : "");
    setDepthInput(
      chat.limits_override?.delegation_depth != null
        ? String(chat.limits_override.delegation_depth)
        : ""
    );
  }, [chat.id, chat.limits_override?.token_budget, chat.limits_override?.delegation_depth]);

  useEffect(() => {
    api
      .tools()
      .then((rows) =>
        setTools(
          Object.fromEntries(rows.map((t) => [t.name, { description: t.description, level: t.level }]))
        )
      )
      .catch(() => {});
  }, []);

  const patch = async (body: any, key: string, okMsg: string) => {
    setBusy(key);
    setError(null);
    try {
      await api.patchChat(chat.id, body);
      toast(okMsg, "ok");
      onSaved();
    } catch (e: any) {
      setError(errText(e, "保存失败，请稍后重试"));
      toastError(e);
      // 失败时回滚为服务端状态
      setMode(chat.run_mode);
      setRequireWrite(chat.require_approval_for_write);
      setAllowed(chat.always_allow_tools || []);
      setBudgetInput(chat.limits_override?.token_budget != null ? String(chat.limits_override.token_budget) : "");
      setDepthInput(
        chat.limits_override?.delegation_depth != null
          ? String(chat.limits_override.delegation_depth)
          : ""
      );
    } finally {
      setBusy(null);
    }
  };

  const saveLimits = () => {
    // 只保留支持的两项；旧版本可能存过其它键，这里顺手清掉
    const next: Record<string, number> = {};
    const budget = budgetInput.trim();
    if (budget) {
      const value = Number(budget);
      if (!Number.isInteger(value) || (value !== 0 && value < 1000)) {
        return toast("token 预算应为 0（无限制）或至少 1000", "error");
      }
      next.token_budget = value;
    }
    const depth = depthInput.trim();
    if (depth) {
      const value = Number(depth);
      if (!Number.isInteger(value) || value < 1 || value > 5) {
        return toast("委派深度应为 1–5 层", "error");
      }
      next.delegation_depth = value;
    }
    patch({ limits_override: next }, "limits", "已更新本群聊的执行限额");
  };

  const resetLimits = () => {
    setBudgetInput("");
    setDepthInput("");
    patch({ limits_override: {} }, "limits", "已恢复为默认限额");
  };

  const setModePersist = (next: "auto" | "step") => {
    if (next === mode) return;
    setMode(next);
    patch({ run_mode: next }, "mode", next === "step" ? "已切换到逐步确认" : "已切换到自动模式");
  };

  const setWritePersist = (next: boolean) => {
    setRequireWrite(next);
    patch(
      { require_approval_for_write: next },
      "write",
      next ? "文件写入将先请求审批" : "文件写入不再需要审批"
    );
  };

  const removeAllowed = (tool: string) => {
    const next = allowed.filter((t) => t !== tool);
    setAllowed(next);
    patch({ always_allow_tools: next }, "allow", `已撤销对 ${tool} 的始终允许`);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="群聊设置"
      subtitle="只影响这个群聊，改动立即生效"
      width="max-w-lg"
    >
      <div className="space-y-5">
        <div>
          <label className="label">群聊模式</label>
          <div className="grid grid-cols-2 gap-2">
            {([['task', '任务模式', '总指挥拆解、成员执行'], ['free', '自由群聊', '成员自己判断、互相接话']] as const).map(([value, label, desc]) => (
              <button key={value} disabled={!!busy} onClick={() => patch({ mode: value }, "chatMode", "已更新群聊模式")}
                className={`rounded-lg border px-3 py-2 text-left ${value === (chat.mode || 'task') ? 'border-accent/50 bg-accent/[0.08]' : 'border-line'}`}>
                <span className="block text-[12.5px] font-medium">{label}</span>
                <span className="mt-1 block text-[10.5px] text-faint">{desc}</span>
              </button>
            ))}
          </div>
        </div>
        {chat.mode === "free" && <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div><span className="label">每条消息最多发言人数</span>
              <Select ariaLabel="每条消息最多发言人数" value={String(chat.max_speakers ?? 3)} disabled={!!busy}
                onChange={(value) => patch({ max_speakers: Number(value) }, "speakers", "已更新发言人数") }
                options={[1, 2, 3, 4, 5, 6].map((n) => ({ value: String(n), label: `${n} 人` }))} />
            </div>
            <div><span className="label">接话轮数</span>
              <Select ariaLabel="接话轮数" value={String(chat.max_rounds ?? 2)} disabled={!!busy}
                onChange={(value) => patch({ max_rounds: Number(value) }, "rounds", "已更新接话轮数") }
                options={[{ value: "1", label: "1 轮" }, { value: "2", label: "2 轮" }]} />
            </div>
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-[12px]">
            <input type="checkbox" checked={chat.muted ?? false} disabled={!!busy}
              onChange={(e) => patch({ muted: e.target.checked }, "muted", e.target.checked ? "已静音，@ 仍可唤醒成员" : "已恢复主动发言")}
              className="accent-[var(--accent)]" />静音：成员不主动发言，@ 和定向回复仍生效
          </label>
          <p className="text-[10.5px] leading-relaxed text-faint">人数只是上限，不按名额预先挑一组成员，也不要求每条消息凑满。@everyone 通知全体成员，每人最多一次；轮次限制成员间的接话。</p>
        </div>}
        {error && (
          <div className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
            {error}
          </div>
        )}
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
            <Workflow size={13} className="text-accent" /> 运行模式
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                { v: "auto", label: "自动", desc: "子任务完成后继续，直到给出最终回答" },
                { v: "step", label: "逐步确认", desc: "每个子任务完成后暂停等你点继续" },
              ] as const
            ).map((o) => (
              <button
                key={o.v}
                onClick={() => setModePersist(o.v)}
                disabled={busy === "mode"}
                className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                  mode === o.v
                    ? "border-accent/50 bg-accent/[0.08]"
                    : "border-line hover:border-accent/30"
                }`}
              >
                <div className="text-[12.5px] font-medium">{o.label}</div>
                <div className="mt-0.5 text-[10.5px] leading-relaxed text-faint">{o.desc}</div>
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-[10.5px] text-faint">切换对下一次暂停点生效，不影响正在跑的一步。</p>
        </div>

        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
            <Gauge size={13} className="text-accent" /> 执行限额
            <span className="ml-auto text-[10.5px] font-normal text-faint">留空则继承设置页默认值</span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[11px] text-muted">token 预算</span>
              <input
                className="field"
                inputMode="numeric"
                placeholder={
                  chat.limits.token_budget === 0 ? "默认：无限制" : `默认：${chat.limits.token_budget}`
                }
                value={budgetInput}
                onChange={(e) => setBudgetInput(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-muted">委派深度</span>
              <input
                className="field"
                inputMode="numeric"
                placeholder={`默认：${chat.limits.delegation_depth ?? 2} 层`}
                value={depthInput}
                onChange={(e) => setDepthInput(e.target.value)}
              />
            </label>
          </div>
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            token 预算填 0 表示无限制。委派深度 1 表示只有总指挥能派任务，2 表示子 Bot 还能再向下一层；
            1–5 层。两项都只影响之后开始的执行，运行时长不设上限。
          </p>
          <div className="mt-2 flex items-center gap-2">
            <button className="btn-primary" onClick={saveLimits} disabled={busy === "limits"}>
              {busy === "limits" ? <Loader2 size={13} className="animate-spin" /> : null}
              保存限额
            </button>
            {Object.keys(chat.limits_override || {}).length > 0 && (
              <button className="btn-ghost" onClick={resetLimits} disabled={busy === "limits"}>
                恢复默认
              </button>
            )}
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
            <ShieldCheck size={13} className="text-warn" /> 文件写入
          </div>
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line px-3 py-2.5">
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 accent-[var(--accent)]"
              checked={requireWrite}
              disabled={busy === "write"}
              onChange={(e) => setWritePersist(e.target.checked)}
            />
            <span className="min-w-0">
              <span className="block text-[12.5px]">写入文件前需要审批（L2）</span>
              <span className="mt-0.5 block text-[10.5px] leading-relaxed text-faint">
                Bot 在沙箱里写文件时先暂停，等你批准。只影响当前群聊。
              </span>
            </span>
            {busy === "write" && <Loader2 size={12} className="ml-auto animate-spin text-faint" />}
          </label>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
              <ShieldCheck size={13} className="text-ok" /> 始终允许的工具
            </div>
            <span className="text-[10.5px] text-faint">{allowed.length} 个</span>
          </div>
          {allowed.length ? (
            <div className="space-y-1.5">
              {allowed.map((tool) => (
                <div
                  key={tool}
                  className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-2"
                >
                  <code className="text-[11.5px] text-ink">{tool}</code>
                  <span className="min-w-0 flex-1 truncate text-[10.5px] text-faint">
                    {tools[tool]?.description || (tools[tool]?.level === "L3" ? "L3 工具" : "")}
                  </span>
                  <button
                    onClick={() => removeAllowed(tool)}
                    disabled={busy === "allow"}
                    className="shrink-0 rounded p-1 text-faint hover:bg-panel2 hover:text-danger"
                    title="撤销"
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-[11.5px] leading-relaxed text-faint">
              还没有始终允许的工具。在审批卡片上点击「在本群聊始终允许」后会出现在这里。
            </div>
          )}
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            支持 L1 / L2 / L3 工具，仅对当前群聊生效。撤销后恢复该工具默认的审批规则。
          </p>
        </div>
      </div>
    </Modal>
  );
}
