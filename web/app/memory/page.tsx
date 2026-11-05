"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Brain, Check, Loader2, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import PageHeader from "@/components/ui/PageHeader";
import Avatar from "@/components/ui/Avatar";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api, errText, toastError } from "@/lib/api";
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
  const [duplicates, setDuplicates] = useState<{ a: Memory; b: Memory; score: number }[]>([]);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [draft, setDraft] = useState({ title: "", content: "", category: "" });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState({ title: "", content: "", category: "" });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [formError, setFormError] = useState("");
  const [editError, setEditError] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const editSavingRef = useRef(false);
  const confirm = useConfirm();

  useEffect(() => {
    api.bots().then((b) => setBots(b.filter((x) => !x.deleted))).catch(() => {});
    api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    api.memoryCandidates().then((d) => setCandidates(d.candidates)).catch(() => {});
  }, []);

  // 搜索防抖，避免每次按键都请求
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  const acceptCandidate = async (id: string) => {
    try {
      await api.acceptMemoryCandidate(id);
      setCandidates((prev) => prev.filter((c) => c.id !== id));
      toast("已保存到记忆", "ok");
      load();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      toastError(e);
    }
  };

  const rejectCandidate = async (id: string) => {
    try {
      await api.rejectMemoryCandidate(id);
      setCandidates((prev) => prev.filter((c) => c.id !== id));
    } catch (e: any) {
      toastError(e, "忽略失败，请稍后重试");
    }
  };

  const load = () => {
    setLoading(true);
    api
      .memories(
        tab === "shared"
          ? { scope: "shared", q: debounced || undefined }
          : { scope: "bot", bot_id: tab, q: debounced || undefined }
      )
      .then((d) => {
        setItems(d.memories);
        setLimit(d.limit);
        setInjected(d.injected_length);
        setTotalLength(d.total_length);
        setTruncated(d.truncated);
      })
      .catch((e) => toastError(e, "记忆加载失败"))
      .finally(() => setLoading(false));
  };
  useEffect(load, [tab, debounced]);

  // 疑似重复：切换 tab 或删除后刷新
  const loadDuplicates = () => {
    api
      .memoryDuplicates(tab === "shared" ? { scope: "shared" } : { scope: "bot", bot_id: tab })
      .then((d) => setDuplicates(d.duplicates))
      .catch(() => setDuplicates([]));
  };
  useEffect(loadDuplicates, [tab]);

  const add = async () => {
    if (savingRef.current) return;
    if (!draft.content.trim()) { setFormError("请填写需要记住的内容。"); return; }
    setFormError("");
    savingRef.current = true;
    setSaving(true);
    try {
      await api.createMemory({
        scope: tab === "shared" ? "shared" : "bot",
        bot_id: tab === "shared" ? null : tab,
        title: draft.title.trim(),
        content: draft.content.trim(),
        category: draft.category,
      });
      setDraft({ title: "", content: "", category: "" });
      setAdding(false);
      toast("已添加", "ok");
      load();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      setFormError(errText(e, "添加失败，请稍后重试"));
      toastError(e);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const saveEdit = async (id: string) => {
    if (editSavingRef.current) return;
    if (!editDraft.content.trim()) { setEditError("请填写需要记住的内容。"); return; }
    setEditError("");
    editSavingRef.current = true;
    setEditSaving(true);
    try {
      await api.patchMemory(id, { ...editDraft, title: editDraft.title.trim(), content: editDraft.content.trim() });
      setEditing(null);
      load();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      setEditError(errText(e, "保存失败，请稍后重试"));
      toastError(e);
    } finally {
      editSavingRef.current = false;
      setEditSaving(false);
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
    try {
      await api.deleteMemory(id);
      load();
      loadDuplicates();
      api.memoryStats().then((d) => setStats(d.bots)).catch(() => {});
    } catch (e: any) {
      toastError(e, "删除失败，请稍后重试");
    }
  };

  const over = truncated;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <PageHeader
            eyebrow="CONTEXT / MEMORY"
            title="记忆"
            description="让团队记住重要的偏好、约定和项目背景，每次协作都能接续上下文。"
            actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={14} />添加记忆</button>}
          />

          <div className="memory-layout">
          <nav className="memory-scopes" aria-label="记忆范围">
            <p className="mb-3 px-2 text-[11px] font-medium text-faint max-md:hidden">记忆空间</p>
            <TabBtn active={tab === "shared"} onClick={() => setTab("shared")}>
              共享记忆
            </TabBtn>
            {bots.map((b) => (
              <TabBtn key={b.id} active={tab === b.id} onClick={() => setTab(b.id)}>
                <span className="inline-flex items-center gap-1"><Avatar value={b.avatar} size={14} />{b.name}</span>
              </TabBtn>
            ))}
          </nav>
          <div className="min-w-0">
          <div className="relative">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <input
              className="field pl-9"
              placeholder="搜索标题或内容…"
              aria-label="搜索记忆"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="card mt-4 flex items-center gap-2.5 px-3.5 py-2.5">
            <Brain size={14} className="text-accent" />
            <span className="text-[11.5px] text-muted">
              当前注入长度 {injected.toLocaleString()} / {limit.toLocaleString()} 字
            </span>
            <div className="ml-auto h-1.5 w-32 overflow-hidden rounded-full bg-panel3">
              <div
                className={`h-full rounded-full transition-all ${over ? "bg-danger" : "bg-accent"}`}
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
                  <div key={c.id} className="rounded-lg border border-accent/25 bg-accent/[0.04] p-3.5">
                    <div className="flex flex-wrap items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {c.title && <div className="text-[12.5px] font-medium">{c.title}</div>}
                          {c.category && (
                            <span className="chip border-line text-faint">{c.category}</span>
                          )}
                          <span className="chip border-line text-faint">
                            {c.scope === "bot"
                              ? `${bots.find((b) => b.id === c.bot_id)?.name || "Bot"} 的私有记忆`
                              : "共享记忆"}
                          </span>
                        </div>
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

          {duplicates.length > 0 && !debounced && (
            <div className="mt-4">
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                <Sparkles size={13} className="text-amber-500" />
                <h2 className="text-[13px] font-medium">疑似重复记忆</h2>
                <span className="text-[10.5px] text-faint">
                  {duplicates.length} 组 · 内容高度相似，可删除其一
                </span>
              </div>
              <div className="space-y-2">
                {duplicates.map((d, i) => (
                  <div key={i} className="rounded-lg border border-line bg-panel2/40 p-3">
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="truncate text-[12px] text-muted">{d.a.content}</div>
                        <div className="truncate text-[12px] text-muted">{d.b.content}</div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <span className="text-[10px] tabular-nums text-faint">
                          相似 {Math.round(d.score * 100)}%
                        </span>
                        <button
                          className="btn-ghost px-2 py-1 text-[11px]"
                          onClick={() => remove(d.b.id)}
                        >
                          <Trash2 size={12} /> 删除下一条
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <Modal open={adding} onClose={() => setAdding(false)} title="添加记忆" subtitle={tab === "shared" ? "共享记忆 · 所有 Bot 可见" : `私有记忆 · ${bots.find(b => b.id === tab)?.name || "当前 Bot"}`} width="max-w-xl">
            <form onSubmit={e => { e.preventDefault(); void add(); }}>
              <MemoryFields value={draft} onChange={value => { setDraft(value); setFormError(""); }} disabled={saving} error={formError} onSave={add} />
              <div className="mt-6 flex justify-end gap-2 border-t border-line pt-4">
                <button className="btn-ghost" type="button" onClick={() => setAdding(false)}>取消</button>
                <button className="btn-primary" type="submit" disabled={saving}>{saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}{saving ? "正在添加…" : "添加记忆"}</button>
              </div>
            </form>
          </Modal>

          <div className="mt-4 space-y-2">
            {loading && <Loader2 size={15} className="animate-spin text-faint" />}
            {!loading && !items.length && (
              <div className="empty-state py-10">
                <div className="empty-icon">
                  <Brain size={19} />
                </div>
                <div className="mt-3 text-[12.5px] font-medium">还没有记忆条目</div>
                <p className="mt-1 text-[11.5px] text-muted">
                  写下偏好、背景或约束，之后每次执行都会带上这些内容。
                </p>
              </div>
            )}
            {items.map((m) => (
              <div key={m.id} className="card card-hover p-3.5">
                {editing === m.id ? (
                  <form onSubmit={e => { e.preventDefault(); void saveEdit(m.id); }} className="p-1 sm:p-2">
                    <h3 className="mb-5 text-[14px] font-semibold">编辑记忆</h3>
                    <MemoryFields value={editDraft} onChange={value => { setEditDraft(value); setEditError(""); }} disabled={editSaving} error={editError} onSave={() => saveEdit(m.id)} />
                    <div className="mt-5 flex justify-end gap-2 border-t border-line pt-4">
                      <button type="button" className="btn-ghost" disabled={editSaving} onClick={() => setEditing(null)}><X size={13} />取消</button>
                      <button type="submit" className="btn-primary" disabled={editSaving}>{editSaving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}{editSaving ? "正在保存…" : "保存修改"}</button>
                    </div>
                  </form>
                ) : (
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {m.title && <div className="text-[12.5px] font-medium">{m.title}</div>}
                        {m.category && (
                          <span className="chip border-line text-faint">{m.category}</span>
                        )}
                        {m.source === "bot" && (
                          <span className="chip border-accent/40 text-accent">Bot 自主保存</span>
                        )}
                      </div>
                      <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-muted">
                        {m.content}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        aria-label={`编辑记忆${m.title || ""}`}
                        onClick={() => {
                          setEditError("");
                          setEditing(m.id);
                          setEditDraft({ title: m.title, content: m.content, category: m.category || "" });
                        }}
                        className="inline-flex h-9 items-center justify-center rounded-lg px-2 text-[11.5px] text-muted transition-colors hover:bg-panel2 hover:text-ink"
                      >
                        编辑
                      </button>
                      <button type="button" aria-label={`删除记忆${m.title || ""}`} onClick={() => remove(m.id)} className="icon-btn hover:text-danger">
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {stats.length > 0 && (
            <div className="mt-8">
              <div className="mb-2 flex items-end justify-between">
                <h2 className="section-title">各 Bot 注入长度</h2>
                <span className="text-[10.5px] text-faint">共享 + 私有记忆</span>
              </div>
              <div className="card divide-y divide-line/70 overflow-hidden">
                {stats.map((s) => (
                  <div key={s.bot_id} className="flex items-center gap-3 px-3.5 py-2.5 text-[11.5px]">
                    <span className="w-20 truncate">{s.bot_name}</span>
                    <span className="text-faint">共享 {s.shared_length}</span>
                    <span className="text-faint">私有 {s.private_length}</span>
                    <span className={`ml-auto tabular-nums ${s.over_limit ? "text-danger" : "text-muted"}`}>
                      {s.injected_length ?? s.total} / {limit}{s.over_limit ? " · 已截取" : ""}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          </div>
          </div>
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
      aria-pressed={active}
      className={`segmented-item ${active ? "segmented-item-active" : ""}`}
    >
      {children}
    </button>
  );
}


type MemoryDraft = { title: string; content: string; category: string };
const MEMORY_CATEGORIES = ["", "偏好", "约定", "项目背景", "事实", "其他"];

function MemoryFields({ value, onChange, disabled, error, onSave }: {
  value: MemoryDraft;
  onChange: (value: MemoryDraft) => void;
  disabled: boolean;
  error: string;
  onSave: () => void | Promise<void>;
}) {
  const id = useId();
  // Preserve categories saved by a Bot, even when they are outside the presets.
  const categories = MEMORY_CATEGORIES.includes(value.category) ? MEMORY_CATEGORIES : [...MEMORY_CATEGORIES, value.category];
  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-5 disabled:opacity-60">
      <div>
        <label htmlFor={`${id}-title`} className="label text-ink">标题 <span className="ml-1 font-normal text-faint">可选</span></label>
        <input id={`${id}-title`} className="field" maxLength={120} placeholder="用一句话概括，方便之后查找" value={value.title} onChange={e => onChange({ ...value, title: e.target.value })} />
      </div>
      <fieldset className="min-w-0" aria-describedby={`${id}-category-hint`}>
        <legend className="label text-ink">分类</legend>
        <div className="flex flex-wrap gap-2">
          {categories.map(category => <label key={category} className={`relative inline-flex max-w-full cursor-pointer items-center gap-1.5 rounded-lg border px-3 py-2 text-[12px] transition-colors ${value.category === category ? "border-line2 bg-accent/10 text-accent" : "border-line text-muted hover:border-line2 hover:bg-panel2"}`}>
            <input type="radio" name={`${id}-category`} value={category} checked={value.category === category} onChange={() => onChange({ ...value, category })} className="sr-only" />
            <Check size={12} className={`shrink-0 ${value.category === category ? "opacity-100" : "opacity-0"}`} aria-hidden="true" /><span className="break-words [overflow-wrap:anywhere]">{category || "未分类"}</span>
          </label>)}
        </div>
        <p id={`${id}-category-hint`} className="mt-2 text-[11px] text-faint">选择一个分类，便于整理记忆；也可以保持未分类。</p>
      </fieldset>
      <div>
        <label htmlFor={`${id}-content`} className="label text-ink">记忆内容 <span className="ml-1 text-accent">*</span></label>
        <textarea id={`${id}-content`} rows={5} className="field min-h-[140px] resize-y leading-7" placeholder="例如：回答时优先使用简洁的中文，先给结论，再说明原因。" value={value.content} aria-required="true" aria-invalid={!!error} aria-describedby={`${id}-content-hint${error ? ` ${id}-error` : ""}`} onChange={e => onChange({ ...value, content: e.target.value })} onKeyDown={e => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void onSave(); }
        }} />
        <div id={`${id}-content-hint`} className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-faint"><span>Enter 换行 · Ctrl / ⌘ + Enter 保存</span><span className="tabular-nums">{value.content.length.toLocaleString()} 字</span></div>
        {error && <p id={`${id}-error`} role="alert" className="mt-3 rounded-lg border border-danger/20 bg-danger/5 px-3 py-2 text-[12px] text-danger">{error}</p>}
      </div>
    </fieldset>
  );
}
