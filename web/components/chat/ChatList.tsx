"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Loader2, MessageSquare, MessageSquarePlus, Plus, Search, X } from "lucide-react";
import { api, toastError } from "@/lib/api";
import Avatar from "@/components/ui/Avatar";
import Select from "@/components/ui/Select";
import Modal from "@/components/ui/Modal";
import type { Bot, ChatSummary, ChatTemplate } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

export default function ChatList({
  chats,
  loading = false,
  activeId,
  onSelect,
  onCreated,
  onClose,
  onNewChat,
}: {
  chats: ChatSummary[];
  loading?: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreated: (id: string) => void;
  onClose?: () => void;
  onNewChat?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const search = query.trim().toLowerCase();
  const filteredChats = chats.filter(c => `${c.name} ${c.preview || ""}`.toLowerCase().includes(search));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const week = new Date(today);
  week.setDate(week.getDate() - 6);
  const groups = search ? [{ label: "搜索结果", items: filteredChats }] : [
    { label: "今天", items: [] as ChatSummary[] },
    { label: "昨天", items: [] as ChatSummary[] },
    { label: "过去 7 天", items: [] as ChatSummary[] },
    { label: "更早", items: [] as ChatSummary[] },
  ];
  if (!search) filteredChats.forEach((chat) => {
    const iso = chat.last_active_at;
    const date = iso ? new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime() : 0;
    const index = date >= +today ? 0 : date >= +yesterday ? 1 : date >= +week ? 2 : 3;
    groups[index].items.push(chat);
  });
  const newChat = () => onNewChat ? onNewChat() : setOpen(true);

  return (
    <div className="chat-list flex h-full min-h-0 flex-col border-r border-line">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-line/70 px-5">
        <span className="text-[14px] font-semibold tracking-[-0.02em] text-ink">团队会话</span>
        {onClose && (
          <button onClick={onClose} aria-label="关闭会话列表" className="icon-btn ml-auto h-7 w-7 lg:hidden">
            <X size={16} />
          </button>
        )}
      </header>

      <div className="space-y-2.5 px-3.5 pb-2 pt-4">
        <button onClick={newChat} className="chat-list-create flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-line bg-panel text-[12px] font-medium text-ink transition-colors hover:border-line2 hover:bg-panel3/40"
          title="新建团队会话" aria-label="新建团队会话">
          <Plus size={15} strokeWidth={1.7} />新建会话
        </button>
        <label className="chat-list-search flex items-center gap-2 rounded-lg border border-transparent px-2.5 transition-colors focus-within:border-line2 focus-within:bg-panel">
          <Search size={14} strokeWidth={1.7} className="shrink-0 text-faint" />
          <input aria-label="搜索会话" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索会话" className="h-8 min-w-0 w-full bg-transparent text-[11.5px] outline-none placeholder:text-faint" />
          {query && <button onClick={() => setQuery("")} aria-label="清空搜索" className="shrink-0 rounded p-0.5 text-faint hover:text-ink"><X size={12} /></button>}
        </label>
      </div>

      <nav aria-label="群聊列表" className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-4">
        {loading && chats.length === 0 && (
          <div className="space-y-2 pt-4" role="status" aria-label="正在加载会话">
            {[0, 1, 2].map(i => <div key={i} className="flex animate-pulse items-center gap-3 rounded-xl p-3"><div className="h-8 w-8 shrink-0 rounded-lg bg-line/50" /><div className="flex-1"><div className="h-2.5 w-2/3 rounded bg-line/70" /><div className="mt-2.5 h-2 w-full rounded bg-line/40" /></div></div>)}
          </div>
        )}
        {!loading && chats.length === 0 && (
          <div className="px-3 py-10 text-center text-[12px] leading-relaxed text-faint">
            <MessageSquarePlus size={24} className="mx-auto mb-3 text-muted" />
            <p className="font-medium text-ink">你的第一个团队会话</p>
            <p className="mt-2">把成员聚在一起，开始协作。</p>
            <button className="btn-soft mt-4" onClick={newChat}><Plus size={14} />新建会话</button>
          </div>
        )}
        {search && !filteredChats.length && <div className="px-3 py-10 text-center text-[12px] text-muted"><Search size={20} className="mx-auto mb-3 text-faint" /><p>没有匹配的会话</p><button className="mt-2 text-[11px] text-ink underline underline-offset-4" onClick={() => setQuery("")}>清空搜索</button></div>}
        {groups.filter(group => group.items.length).map(group => (
          <section key={group.label} className="mb-2" aria-label={group.label}>
            <div className="flex items-center justify-between px-2.5 pb-2 pt-4 text-[10.5px] text-faint">
              <h2 className="font-medium">{group.label}</h2>
              <span className="text-[10px] tabular-nums">{group.items.length}</span>
            </div>
            <div className="space-y-1">
              {group.items.map((c) => {
                const working = ["running", "queued", "waiting_children"].includes(c.run_status || "");
                const waiting = c.run_status === "waiting";
                return <button key={c.id} onClick={() => onSelect(c.id)} aria-current={activeId === c.id ? "page" : undefined}
                  aria-label={c.name} title={c.name}
                  className="chat-list-item group grid w-full min-w-0 grid-cols-[30px_minmax(0,1fr)] items-center gap-2.5 rounded-xl border border-transparent text-left">
                  <span className="chat-list-symbol relative flex h-[30px] w-[30px] items-center justify-center rounded-lg" aria-hidden="true">
                    <MessageSquare size={15} strokeWidth={1.6} />
                    {(working || waiting) && <span className={`absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border-2 border-panel ${waiting ? "bg-warn" : "animate-pulseSoft bg-ink"}`} />}
                  </span>
                  <span className="min-w-0">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium leading-5 text-ink">{c.name}</span>
                      {(working || waiting) && <span className={`shrink-0 text-[9.5px] ${waiting ? "text-warn" : "text-muted"}`}>{waiting ? "待确认" : "协作中"}</span>}
                    </span>
                    <span className="mt-0.5 flex min-w-0 items-center gap-2 text-[10.5px] leading-[18px] text-faint">
                      <span className="min-w-0 flex-1 truncate">{c.preview?.replace(/\s+/g, " ").trim() || `${c.member_count} 位成员 · 开始协作`}</span>
                      <span className="shrink-0 text-[9.5px] tabular-nums">{timeAgo(c.last_active_at)}</span>
                    </span>
                  </span>
                </button>;
              })}
            </div>
          </section>
        ))}
      </nav>

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

export function NewChatDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [templates, setTemplates] = useState<ChatTemplate[]>([]);
  const [mode, setMode] = useState<"templates" | "custom">("templates");
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState(false);
  const [creatingTemplate, setCreatingTemplate] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [sup, setSup] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const submitting = useRef(false);

  const loadTemplates = () => {
    setTemplatesLoading(true);
    setTemplatesError(false);
    api.chatTemplates().then(setTemplates).catch(() => setTemplatesError(true))
      .finally(() => setTemplatesLoading(false));
  };

  useEffect(() => {
    loadTemplates();
    api.bots().then((bs) => {
      const alive = bs.filter((b) => !b.deleted && b.enabled !== false);
      setBots(alive);
      setPicked(alive.map((b) => b.id));
      const s = alive.find((b) => b.role === "总指挥") || alive[0];
      setSup(s?.id || "");
    }).catch((e) => toastError(e, "成员列表加载失败"));
  }, []);

  const createFromTemplate = async (template: ChatTemplate) => {
    if (submitting.current) return;
    submitting.current = true;
    setLoading(true);
    setCreatingTemplate(template.id);
    try {
      const chat = await api.createChatFromTemplate(template.id);
      toast(chat.model_setup_required ? "团队已创建，请到智能体页面配置模型后开始协作" : `已创建「${chat.name}」`, "ok");
      onCreated(chat.id);
    } catch (e: any) {
      toastError(e);
    } finally {
      submitting.current = false;
      setLoading(false);
      setCreatingTemplate(null);
    }
  };

  const submit = async () => {
    if (submitting.current) return;
    if (!name.trim()) return toast("请填写群聊名称", "error");
    if (!picked.length) return toast("至少选择一个成员", "error");
    submitting.current = true;
    setLoading(true);
    try {
      const c = await api.createChat({
        name: name.trim(),
        member_ids: picked,
        supervisor_bot_id: sup,
      });
      onCreated(c.id);
    } catch (e: any) {
      toastError(e);
    } finally {
      submitting.current = false;
      setLoading(false);
    }
  };

  return (
    <Modal open onClose={() => { if (!loading) onClose(); }} title="新建团队会话" subtitle="选一个模板，一键组建团队；也可以自己选择成员。" width="max-w-2xl"
      footer={<><button className="btn-ghost" onClick={onClose} disabled={loading}>取消</button>{mode === "custom" && <button className="btn-primary" onClick={submit} disabled={loading || !picked.length}>{loading && <Loader2 size={13} className="animate-spin" />}创建会话</button>}</>}>
      <div className="mb-5 flex gap-1 rounded-lg bg-panel2 p-1" role="tablist" aria-label="创建方式">
        {([['templates', '使用模板'], ['custom', '自定义团队']] as const).map(([value, label]) => (
          <button key={value} role="tab" aria-selected={mode === value} disabled={loading} onClick={() => setMode(value)}
            className={`flex-1 rounded-md px-3 py-2 text-[12px] font-medium transition-colors ${mode === value ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink"}`}>
            {label}
          </button>
        ))}
      </div>
      {mode === "templates" ? (
        <div>
          {templatesLoading && <div className="flex items-center justify-center gap-2 py-12 text-[12px] text-muted" role="status"><Loader2 size={16} className="animate-spin" />正在加载模板…</div>}
          {templatesError && <div className="rounded-lg bg-panel2 p-5 text-center text-[12px] text-muted">模板加载失败<button className="ml-3 text-accent hover:underline" onClick={loadTemplates}>重试</button></div>}
          {!templatesLoading && !templatesError && (
            <div className="grid gap-3 sm:grid-cols-2">
              {templates.map((template) => (
                <button key={template.id} disabled={loading} onClick={() => createFromTemplate(template)}
                  aria-label={`一键创建${template.name}`} aria-busy={creatingTemplate === template.id}
                  className="group flex min-w-0 flex-col rounded-xl border border-line p-4 text-left transition-colors hover:border-accent/40 hover:bg-panel2/60 disabled:cursor-wait disabled:opacity-60">
                  <div className="flex w-full items-center gap-2.5"><Avatar value={template.avatar} size={32} alt={template.name} /><span className="text-[13px] font-semibold text-ink">{template.name}</span><span className="ml-auto font-mono text-[10px] text-faint">{template.members.length} 人</span></div>
                  <p className="mb-4 mt-3 text-[11.5px] leading-relaxed text-muted">{template.description}</p>
                  <p className="mt-auto text-[10.5px] leading-relaxed text-faint">{template.members.map((member) => member.name).join(" · ")}</p>
                  <span className="mt-3 flex items-center gap-1 text-[11px] font-medium text-accent">
                    {creatingTemplate === template.id ? <><Loader2 size={12} className="animate-spin" />正在组建…</> : <>一键创建<ArrowUpRight size={12} /></>}
                  </span>
                </button>
              ))}
            </div>
          )}
          <p className="mt-4 text-[11px] leading-relaxed text-faint">优先复用已启用的成员，缺少的角色自动补齐。创建后可随时调整成员与模型。</p>
        </div>
      ) : (
        <div className="space-y-5">
          <div>
            <label htmlFor="new-chat-name" className="label">会话名称</label>
            <input
              autoFocus
              id="new-chat-name"
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：市场调研"
            />
          </div>

          <div>
            <label className="label">成员</label>
            {!bots.length && <p className="rounded-lg bg-panel2 p-3 text-[12px] text-muted">还没有可选成员，请先到智能体页面创建团队。</p>}
            <div className="max-h-56 space-y-1 overflow-y-auto">
              {bots.map((b) => (
                <label
                  key={b.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-panel2"
                >
                  <input
                    type="checkbox"
                    checked={picked.includes(b.id)}
                    onChange={(e) => {
                      const next = e.target.checked ? [...picked, b.id] : picked.filter((i) => i !== b.id);
                      setPicked(next);
                      if (!next.includes(sup)) setSup(next[0] || "");
                    }}
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
              总指挥负责拆解任务与派任务，额外拥有 delegate 与计划工具。
            </p>
          </div>
        </div>
      )}
    </Modal>
  );
}
