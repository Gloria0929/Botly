"use client";

import { useEffect, useMemo, useState } from "react";
import { Crown, Loader2, Plus, Search, UserMinus, UserPlus } from "lucide-react";
import Avatar from "@/components/ui/Avatar";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api } from "@/lib/api";
import { modelCapability } from "@/lib/fmt";
import type { Bot, Chat, Provider } from "@/lib/types";
import { toast } from "@/lib/ui";

/**
 * 群聊成员管理：加入 / 移出成员、更换总指挥。
 * 规则与后端一致——总指挥必须先交接才能移出，执行中的成员不能移出。
 */
export default function MembersModal({
  open,
  chat,
  onClose,
  onChanged,
}: {
  open: boolean;
  chat: Chat | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const confirm = useConfirm();

  useEffect(() => {
    if (!open) return;
    setQuery("");
    api
      .bots()
      .then((bs) => setBots(bs.filter((b) => !b.deleted)))
      .catch(() => {});
    // MOD-02：用已探测的模型能力决定谁能当总指挥
    api.providers().then(setProviders).catch(() => {});
  }, [open]);

  /** 该 Bot 的模型是否明确不能当总指挥（不支持工具调用 / 流式输出）。 */
  const capOf = (bot: Bot) => {
    const p = providers.find((x) => x.id === bot.provider_id);
    const model = p?.models.find((m) => m.name === bot.model);
    return modelCapability(model);
  };

  const members = chat?.members || [];
  const memberIds = useMemo(() => new Set(members.map((m) => m.id)), [members]);
  const available = useMemo(() => bots.filter((b) => !memberIds.has(b.id)), [bots, memberIds]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return available;
    return available.filter(
      (b) => b.name.toLowerCase().includes(q) || b.role.toLowerCase().includes(q)
    );
  }, [available, query]);

  const add = async (bot: Bot) => {
    if (!chat) return;
    setBusy(bot.id);
    try {
      await api.addMembers(chat.id, [bot.id]);
      toast(`已把「${bot.name}」加入群聊`, "ok");
      onChanged();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(null);
    }
  };

  const promote = async (bot: Bot) => {
    if (!chat || busy) return;
    setBusy(bot.id);
    try {
      await api.patchChat(chat.id, { supervisor_bot_id: bot.id });
      toast(`「${bot.name}」已设为总指挥，下一次执行生效`, "ok");
      onChanged();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (bot: Bot) => {
    if (!chat || busy) return;
    const ok = await confirm({
      title: `把「${bot.name}」移出群聊？`,
      description: "移出后总指挥不会再派活给它，历史消息与产物都会保留。",
      confirmText: "移出",
      danger: true,
    });
    if (!ok) return;
    setBusy(bot.id);
    try {
      await api.removeMember(chat.id, bot.id);
      toast(`已把「${bot.name}」移出群聊`, "ok");
      onChanged();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="群聊成员"
      subtitle="消息默认发给总指挥；@某个成员可以让它直接处理。"
      width="max-w-lg"
    >
      <div className="space-y-5">
        <section>
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-[12px] font-medium text-muted">成员</span>
            <span className="text-[10.5px] text-faint">{members.length}</span>
          </div>
          <div className="overflow-hidden rounded-xl border border-line">
            {members.map((m) => {
              const isSup = chat?.supervisor_bot_id === m.id;
              const working = m.status === "working" || m.status === "waiting";
              const cap = capOf(m);
              return (
                <div
                  key={m.id}
                  className="flex items-center gap-2.5 border-b border-line px-2.5 py-2 last:border-b-0 hover:bg-panel2/60"
                >
                  <Avatar value={m.avatar} size={28} alt={m.name} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-[12.5px] font-medium">{m.name}</span>
                      {isSup && (
                        <span className="chip border-accent/30 bg-accent/10 text-accent">
                          <Crown size={9} /> 总指挥
                        </span>
                      )}
                      {working && (
                        <span className="text-[10px] text-warn">
                          {m.status === "waiting" ? "等待处理" : "执行中"}
                        </span>
                      )}
                    </div>
                    <div className="truncate text-[10.5px] text-faint">
                      {m.role}
                      {cap.bad && <span className="text-warn"> · {cap.text}</span>}
                    </div>
                  </div>
                  {!isSup && (
                    <button
                      onClick={() => promote(m)}
                      disabled={!!busy || cap.bad}
                      className="rounded-md px-1.5 py-1 text-[10.5px] text-muted transition-colors hover:bg-panel hover:text-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted"
                      title={cap.bad ? `该模型${cap.text}，不能担任总指挥` : "设为群聊总指挥"}
                    >
                      设为总指挥
                    </button>
                  )}
                  <button
                    onClick={() => remove(m)}
                    disabled={!!busy || isSup || members.length <= 1}
                    title={isSup ? "请先把总指挥换成其他成员" : "移出群聊"}
                    className="rounded-md p-1.5 text-faint transition-colors hover:bg-danger/10 hover:text-danger disabled:cursor-not-allowed disabled:opacity-25 disabled:hover:bg-transparent disabled:hover:text-faint"
                  >
                    {busy === m.id ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <UserMinus size={13} />
                    )}
                  </button>
                </div>
              );
            })}
            {!members.length && (
              <div className="px-3 py-6 text-center text-[11.5px] text-faint">暂无成员</div>
            )}
          </div>
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            总指挥负责拆解任务并派活；更换总指挥对下一次执行生效。
          </p>
        </section>

        <section>
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-[12px] font-medium text-muted">加入成员</span>
            <span className="text-[10.5px] text-faint">{available.length}</span>
          </div>

          {available.length > 3 && (
            <div className="relative mb-1.5">
              <Search
                size={12}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索 Bot 名字或角色"
                className="field py-1.5 pl-7 text-[12px]"
              />
            </div>
          )}

          <div className="max-h-[260px] overflow-y-auto rounded-xl border border-line">
            {filtered.map((b) => (
              <button
                key={b.id}
                onClick={() => add(b)}
                disabled={!!busy}
                className="flex w-full items-center gap-2.5 border-b border-line px-2.5 py-2 text-left transition-colors last:border-b-0 hover:bg-panel2 disabled:opacity-50"
              >
                <Avatar value={b.avatar} size={28} alt={b.name} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px]">{b.name}</div>
                  <div className="truncate text-[10.5px] text-faint">
                    {b.role}
                    {!b.provider_id && <span className="text-warn"> · 未配置模型</span>}
                  </div>
                </div>
                {busy === b.id ? (
                  <Loader2 size={13} className="animate-spin text-accent" />
                ) : (
                  <span className="flex items-center gap-1 text-[11px] text-accent">
                    <Plus size={11} /> 加入
                  </span>
                )}
              </button>
            ))}

            {!filtered.length && (
              <div className="flex flex-col items-center gap-2 px-4 py-7 text-center">
                <UserPlus size={16} className="text-faint" />
                <div className="text-[11.5px] leading-relaxed text-faint">
                  {available.length
                    ? "没有匹配的 Bot"
                    : bots.length
                      ? "所有 Bot 都已在群里"
                      : "还没有其他 Bot，去「Bot」页新建一个"}
                </div>
              </div>
            )}
          </div>
          <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
            需要新角色？先到{" "}
            <a href="/bots" className="text-accent hover:underline">
              Bot 页
            </a>{" "}
            新建，再回来加入。
          </p>
        </section>
      </div>
    </Modal>
  );
}
