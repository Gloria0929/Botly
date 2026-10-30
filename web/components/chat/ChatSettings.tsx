"use client";

import { useEffect, useState } from "react";
import { Gauge, Loader2, ShieldCheck, Workflow, X } from "lucide-react";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
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
  const [tools, setTools] = useState<Record<string, { description: string; level: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    setMode(chat.run_mode);
    setRequireWrite(chat.require_approval_for_write);
    setAllowed(chat.always_allow_tools || []);
  }, [chat.id, chat.run_mode, chat.require_approval_for_write, chat.always_allow_tools]);

  // 限额输入单独同步：只跟随服务端的覆盖值，避免群聊刷新（数组引用变化）
  // 把用户正在输入的内容清掉。
  useEffect(() => {
    setBudgetInput(chat.limits_override?.token_budget != null ? String(chat.limits_override.token_budget) : "");
  }, [chat.id, chat.limits_override?.token_budget]);

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
    try {
      await api.patchChat(chat.id, body);
      toast(okMsg, "ok");
      onSaved();
    } catch (e: any) {
      toast(e.message, "error");
      // 失败时回滚为服务端状态
      setMode(chat.run_mode);
      setRequireWrite(chat.require_approval_for_write);
      setAllowed(chat.always_allow_tools || []);
      setBudgetInput(chat.limits_override?.token_budget != null ? String(chat.limits_override.token_budget) : "");
    } finally {
      setBusy(null);
    }
  };

  const saveLimits = () => {
    // 只保留 token 预算；旧版本可能存过其它键，这里顺手清掉
    const next: Record<string, number> = {};
    const budget = budgetInput.trim();
    if (budget) {
      const value = Number(budget);
      if (!Number.isInteger(value) || (value !== 0 && value < 1000)) {
        return toast("token 预算应为 0（无限制）或至少 1000", "error");
      }
      next.token_budget = value;
    }
    patch({ limits_override: next }, "limits", "已更新本群聊的 token 预算");
  };

  const resetLimits = () => {
    setBudgetInput("");
    patch({ limits_override: {} }, "limits", "已恢复为默认预算");
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
          </div>
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            token 预算填 0 表示无限制；只影响之后开始的执行。运行时长不设上限。
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
              还没有始终允许的工具。审批卡片上勾选「在本群聊始终允许」后会出现在这里。
            </div>
          )}
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            只对 L1 / L2 工具生效；L3（外部副作用）每次都必须审批，不能加入白名单。
          </p>
        </div>
      </div>
    </Modal>
  );
}
