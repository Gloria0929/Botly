"use client";

import { useEffect, useMemo, useState } from "react";
import { Brain, Check, Loader2, Plus, Sparkles, Trash2, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useConfirm } from "@/components/ui/Confirm";
import { api } from "@/lib/api";
import type { Bot, Memory, MemoryCandidate } from "@/lib/types";
import { toast } from "@/lib/ui";

export default function MemoryPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [tab, setTab] = useState<string>("shared");
  const [items, setItems] = useState<Memory[]>([]);
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [limit, setLimit] = useState(4000);
  const [injected, setInjected] = useState(0);
  const [totalLength, setTotalLength] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [stats, setStats] = useState<any[]>([]);
  const [draft, setDraft] = useState({ title: "", content: "" });
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState({ title: "", content: "" });
  const [loading, setLoading] = useState(false);
  const confirm = useConfirm();

  useEffect(() => {
    api.bots().then((b) => setBots(b.filter((x) => !x.deleted))).catch(() => {});
    api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    api.memoryCandidates().then((d) => setCandidates(d.candidates)).catch(() => {});
  }, []);

  const acceptCandidate = async (id: string) => {
    try {
      await api.acceptMemoryCandidate(id);
      setCandidates((prev) => prev.filter((c) => c.id !== id));
      toast("已保存到记忆", "ok");
      load();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const rejectCandidate = async (id: string) => {
    await api.rejectMemoryCandidate(id).catch(() => {});
    setCandidates((prev) => prev.filter((c) => c.id !== id));
  };

  const load = () => {
    setLoading(true);
    api
      .memories(tab === "shared" ? { scope: "shared" } : { scope: "bot", bot_id: tab })
      .then((d) => {
        setItems(d.memories);
        setLimit(d.limit);
        setInjected(d.injected_length);
        setTotalLength(d.total_length);
        setTruncated(d.truncated);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, [tab]);

  const add = async () => {
    if (!draft.content.trim()) return toast("内容不能为空", "error");
    try {
      await api.createMemory({
        scope: tab === "shared" ? "shared" : "bot",
        bot_id: tab === "shared" ? null : tab,
        title: draft.title,
        content: draft.content,
      });
      setDraft({ title: "", content: "" });
      toast("已添加", "ok");
      load();
      api.memoryStats().then((d) => setStats(d.bots));
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const saveEdit = async (id: string) => {
    try {
      await api.patchMemory(id, editDraft);
      setEditing(null);
      load();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const remove = async (id: string) => {
    const ok = await confirm({
      title: "删除这条记忆？",
      description: "删除后不会再注入给 Bot，历史对话不受影响。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    await api.deleteMemory(id).catch(() => {});
    load();
    api.memoryStats().then((d) => setStats(d.bots));
  };

  const over = truncated;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-8">
          <h1 className="text-[20px] font-semibold tracking-tight">记忆</h1>
          <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
            共享记忆对所有 Bot 可见；私有记忆只注入到对应 Bot。第一版只有你能编辑，Bot 不能自行写入。
          </p>

          <div className="mt-5 flex flex-wrap gap-1.5">
            <TabBtn active={tab === "shared"} onClick={() => setTab("shared")}>
              共享记忆
            </TabBtn>
            {bots.map((b) => (
              <TabBtn key={b.id} active={tab === b.id} onClick={() => setTab(b.id)}>
                {b.avatar} {b.name}
              </TabBtn>
            ))}
          </div>

          <div className="mt-4 flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2">
            <Brain size={13} className="text-accent" />
            <span className="text-[11.5px] text-muted">
              当前注入长度 {injected.toLocaleString()} / {limit.toLocaleString()} 字
            </span>
            <div className="ml-auto h-1.5 w-32 overflow-hidden rounded-full bg-panel2">
              <div
                className={`h-full ${over ? "bg-danger" : "bg-accent"}`}
                style={{ width: `${Math.min(100, (injected / limit) * 100)}%` }}
              />
            </div>
          </div>
          {over && (
            <p className="mt-1.5 text-[11px] text-danger">
              已保存 {totalLength.toLocaleString()} 字，超过注入上限。共享与私有记忆合计按更新时间选取最近内容，原始记忆仍会保留。
            </p>
          )}

          {candidates.length > 0 && (
            <div className="mt-4">
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <Sparkles size={13} className="text-accent" />
                <h2 className="text-[13px] font-medium">待确认的记忆候选</h2>
                <span className="text-[10.5px] text-faint">
                  {candidates.length} 条 · 执行结束后自动提取，确认后才写入
                </span>
              </div>
              <div className="space-y-2">
                {candidates.map((c) => (
                  <div key={c.id} className="rounded-xl border border-accent/25 bg-accent/[0.04] p-3.5">
                    <div className="flex flex-wrap items-start gap-3">
                      <div className="min-w-0 flex-1">
                        {c.title && <div className="text-[12.5px] font-medium">{c.title}</div>}
                        <div className="mt-0.5 whitespace-pre-wrap text-[12.5px] leading-relaxed text-muted">
                          {c.content}
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1.5">
                        <button
                          className="btn-primary px-2.5 py-1 text-[11.5px]"
                          onClick={() => acceptCandidate(c.id)}
                        >
                          <Check size={12} /> 保存
                        </button>
                        <button
                          className="btn-ghost px-2.5 py-1 text-[11.5px]"
                          onClick={() => rejectCandidate(c.id)}
                        >
                          <X size={12} /> 忽略
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card mt-4 p-3.5">
            <div className="grid gap-2 sm:grid-cols-[160px_1fr]">
              <input
                className="field"
                placeholder="标题（可选）"
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
              <input
                className="field"
                placeholder="例如：我偏好简洁的中文表达，不要使用感叹号。"
                value={draft.content}
                onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                onKeyDown={(e) => e.key === "Enter" && add()}
              />
            </div>
            <div className="mt-2 flex justify-end">
              <button className="btn-primary" onClick={add}>
                <Plus size={13} /> 添加
              </button>
            </div>
          </div>

          <div className="mt-4 space-y-2">
            {loading && <Loader2 size={15} className="animate-spin text-faint" />}
            {!loading && !items.length && (
              <div className="py-10 text-center text-[12px] text-faint">还没有记忆条目</div>
            )}
            {items.map((m) => (
              <div key={m.id} className="card p-3.5">
                {editing === m.id ? (
                  <div className="space-y-2">
                    <input
                      className="field"
                      value={editDraft.title}
                      onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                    />
                    <textarea
                      className="field h-24 resize-none"
                      value={editDraft.content}
                      onChange={(e) => setEditDraft({ ...editDraft, content: e.target.value })}
                    />
                    <div className="flex justify-end gap-2">
                      <button className="btn-ghost" onClick={() => setEditing(null)}>
                        <X size={13} /> 取消
                      </button>
                      <button className="btn-primary" onClick={() => saveEdit(m.id)}>
                        <Check size={13} /> 保存
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      {m.title && <div className="text-[12.5px] font-medium">{m.title}</div>}
                      <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-muted">
                        {m.content}
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        setEditing(m.id);
                        setEditDraft({ title: m.title, content: m.content });
                      }}
                      className="shrink-0 text-[11.5px] text-muted hover:text-ink"
                    >
                      编辑
                    </button>
                    <button onClick={() => remove(m.id)} className="shrink-0 text-faint hover:text-danger">
                      <Trash2 size={13} />
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>

          {stats.length > 0 && (
            <div className="mt-8">
              <h2 className="text-[13px] font-medium text-muted">各 Bot 注入长度</h2>
              <div className="mt-2 divide-y divide-line rounded-lg border border-line">
                {stats.map((s) => (
                  <div key={s.bot_id} className="flex items-center gap-3 px-3 py-2 text-[11.5px]">
                    <span className="w-20 truncate">{s.bot_name}</span>
                    <span className="text-faint">共享 {s.shared_length}</span>
                    <span className="text-faint">私有 {s.private_length}</span>
                    <span className={`ml-auto ${s.over_limit ? "text-danger" : "text-muted"}`}>
                      {s.injected_length ?? s.total} / {limit}{s.over_limit ? " · 已截取" : ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors ${
        active
          ? "border-accent/50 bg-accent/10 text-accent"
          : "border-line text-muted hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}
