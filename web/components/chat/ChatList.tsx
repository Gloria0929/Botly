"use client";

import { useEffect, useState } from "react";
import { Loader2, MessageSquarePlus, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import Avatar from "@/components/ui/Avatar";
import Select from "@/components/ui/Select";
import type { Bot, ChatSummary } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

export default function ChatList({
  chats,
  loading = false,
  activeId,
  onSelect,
  onCreated,
  onClose,
}: {
  chats: ChatSummary[];
  loading?: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreated: (id: string) => void;
  onClose?: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex h-full min-h-0 flex-col border-r border-line bg-panel">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <span className="text-[12.5px] font-medium text-muted">群聊</span>
        <span className="text-[10.5px] text-faint">{chats.length}</span>
        <button
          onClick={() => setOpen(true)}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-lg text-muted hover:bg-panel2 hover:text-ink"
          title="新建群聊"
        >
          <Plus size={15} />
        </button>
        {onClose && (
          <button onClick={onClose} className="p-1 text-faint hover:text-ink lg:hidden">
            <X size={15} />
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {loading && chats.length === 0 && (
          <div className="flex justify-center px-3 py-10">
            <Loader2 size={14} className="animate-spin text-faint" />
          </div>
        )}
        {!loading && chats.length === 0 && (
          <div className="px-3 py-10 text-center text-[12px] leading-relaxed text-faint">
            还没有群聊
            <br />
            点右上角 + 创建一个
          </div>
        )}
        {chats.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`mb-0.5 w-full rounded-lg px-2.5 py-2 text-left transition-colors ${
              activeId === c.id ? "bg-panel2" : "hover:bg-panel2/60"
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  c.run_status === "running" || c.run_status === "queued"
                    ? "animate-pulseSoft bg-accent"
                    : c.run_status === "waiting"
                      ? "bg-warn"
                      : "bg-transparent"
                }`}
              />
              <span className="truncate text-[12.5px] font-medium">{c.name}</span>
              <span className="ml-auto shrink-0 text-[10px] text-faint">
                {timeAgo(c.last_active_at)}
              </span>
            </div>
            <div className="mt-0.5 truncate pl-3.5 text-[11px] text-faint">
              {c.preview || `${c.member_count} 个成员`}
            </div>
          </button>
        ))}
      </div>

      {open && (
        <NewChatDialog
          onClose={() => setOpen(false)}
          onCreated={(id) => {
            setOpen(false);
            onCreated(id);
          }}
        />
      )}
    </div>
  );
}

function NewChatDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [sup, setSup] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.bots().then((bs) => {
      const alive = bs.filter((b) => !b.deleted);
      setBots(alive);
      setPicked(alive.map((b) => b.id));
      const s = alive.find((b) => b.role === "总指挥") || alive[0];
      setSup(s?.id || "");
    });
  }, []);

  const submit = async () => {
    if (!name.trim()) return toast("请填写群聊名称", "error");
    if (!picked.length) return toast("至少选择一个成员", "error");
    setLoading(true);
    try {
      const c = await api.createChat({
        name: name.trim(),
        member_ids: picked,
        supervisor_bot_id: sup,
      });
      onCreated(c.id);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-line bg-panel shadow-card">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <MessageSquarePlus size={15} className="text-accent" />
          <span className="text-[13px] font-medium">新建群聊</span>
          <button onClick={onClose} className="ml-auto p-1 text-faint hover:text-ink">
            <X size={15} />
          </button>
        </div>

        <div className="space-y-4 p-4">
          <div>
            <label className="label">群聊名称</label>
            <input
              autoFocus
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：市场调研"
            />
          </div>

          <div>
            <label className="label">成员</label>
            <div className="space-y-1">
              {bots.map((b) => (
                <label
                  key={b.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-panel2"
                >
                  <input
                    type="checkbox"
                    checked={picked.includes(b.id)}
                    onChange={(e) =>
                      setPicked(
                        e.target.checked ? [...picked, b.id] : picked.filter((i) => i !== b.id)
                      )
                    }
                    className="accent-[var(--accent)]"
                  />
                  <Avatar value={b.avatar} size={20} />
                  <span className="text-[12.5px]">{b.name}</span>
                  <span className="ml-auto text-[10.5px] text-faint">{b.role}</span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <label className="label">总指挥</label>
            <Select
              value={sup}
              onChange={setSup}
              options={bots
                .filter((b) => picked.includes(b.id))
                .map((b) => ({
                  value: b.id,
                  label: `${b.name}（${b.role}）`,
                  avatar: b.avatar,
                }))}
              placeholder="选择总指挥"
            />
            <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
              总指挥负责拆解任务与派活，额外拥有 delegate 与计划工具。
            </p>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={submit} disabled={loading}>
            {loading && <Loader2 size={13} className="animate-spin" />} 创建
          </button>
        </div>
      </div>
    </div>
  );
}
