"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, Plus, Save, Sparkles, Trash2, Wrench, Search, ArrowUpRight, Blocks, CircleCheck, Plug, LayoutGrid, List, Cpu } from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import AvatarPicker from "@/components/ui/AvatarPicker";
import Select from "@/components/ui/Select";
import Modal from "@/components/ui/Modal";
import PageHeader from "@/components/ui/PageHeader";
import StatGrid from "@/components/ui/StatGrid";
import { useConfirm } from "@/components/ui/Confirm";
import { api, errText, toastError } from "@/lib/api";
import { modelCapability } from "@/lib/fmt";
import type { Bot, BotTemplate, Provider, Skill } from "@/lib/types";
import { toast } from "@/lib/ui";

const EMPTY: Partial<Bot> = {
  name: "",
  avatar: "bot",
  role: "通用助手",
  system_prompt: "",
  provider_id: null,
  model: null,
  temperature: 0.4,
  tools: ["web_search", "web_read", "file_read", "file_write", "file_list"],
  skills: [],
};

function BotStatusSwitch({ bot, onToggle }: { bot: Bot; onToggle: (bot: Bot) => void }) {
  const enabled = bot.enabled !== false;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={`${bot.name}的启用状态`}
      title={`${enabled ? "停用" : "启用"}${bot.name}`}
      onClick={() => onToggle(bot)}
      className="inline-flex min-h-8 items-center gap-2 rounded-md text-[11px] text-muted"
    >
      <span aria-hidden="true" className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${enabled ? "bg-accent" : "bg-line2"}`}>
        <span className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full transition-transform ${enabled ? "translate-x-4 bg-accentink" : "bg-panel"}`} />
      </span>
      <span>{enabled ? "已启用" : "已停用"}</span>
    </button>
  );
}

export default function BotsPage() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [filter, setFilter] = useState<"all" | "enabled" | "disabled">("all");
  const visibleBots = bots.filter(b => !b.deleted && (filter === "all" || (filter === "enabled" ? b.enabled !== false : b.enabled === false)) && `${b.name} ${b.role} ${b.model || ""}`.toLowerCase().includes(query.toLowerCase()));
  const [botsLoading, setBotsLoading] = useState(true);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [tools, setTools] = useState<
    {
      name: string;
      description: string;
      level: string;
      available?: boolean;
      reason?: string | null;
      source?: "builtin" | "mcp";
    }[]
  >([]);
  const [templates, setTemplates] = useState<BotTemplate[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [editing, setEditing] = useState<Partial<Bot> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const confirm = useConfirm();

  const load = () => {
    api.bots().then(setBots).catch((e) => toastError(e, "Bot 列表加载失败")).finally(() => setBotsLoading(false));
    api.providers().then(setProviders).catch(() => {});
    api.tools().then(setTools).catch((e) => toastError(e, "工具清单加载失败"));
    api.templates().then(setTemplates).catch(() => {});
    api.skills().then(setSkills).catch(() => {});
  };
  useEffect(load, []);

  // BOT-06：停用只是「不再参与新执行」，历史消息与成员关系都保留
  const toggleEnabled = async (b: Bot) => {
    const next = b.enabled === false;
    setBots((prev) => prev.map((x) => (x.id === b.id ? { ...x, enabled: next } : x)));
    try {
      await api.patchBot(b.id, { enabled: next });
      toast(next ? `已启用「${b.name}」` : `已停用「${b.name}」`, "ok");
    } catch (e: any) {
      setBots((prev) => prev.map((x) => (x.id === b.id ? { ...x, enabled: !next } : x)));
      toastError(e, "操作失败");
    }
  };

  // 未配置搜索 Key 时 web_search 仍可选（执行时回退 DuckDuckGo），
  // 这里只过滤后端明确标记为不可用的工具。
  const onlyAvailable = (names: string[]) =>
    names.filter((n) => tools.find((t) => t.name === n)?.available !== false);

  const applyTemplate = (t: BotTemplate) => {
    if (!editing) return;
    const used = new Set(bots.filter((b) => !b.deleted).map((b) => b.name));
    let name = t.name;
    let i = 2;
    while (used.has(name)) name = `${t.name} ${i++}`;
    setEditing({
      ...editing,
      name,
      avatar: t.avatar,
      role: t.role,
      system_prompt: t.system_prompt,
      tools: onlyAvailable(t.tools),
    });
  };

  const refreshProviders = () => {
    // 首屏加载时后端可能刚重启、厂商列表拉空了；打开弹窗前再拉一次，避免
    // 「明明配了厂商，新建时却选不到」。
    api.providers().then(setProviders).catch((e) => toastError(e, "厂商列表加载失败"));
  };

  const newBot = () => {
    refreshProviders();
    setEditing({ ...EMPTY, tools: onlyAvailable(EMPTY.tools || []) });
  };

  const save = async () => {
    // 校验失败不能只弹底部 toast：弹窗底部按钮和页面底部 toast 经常一起被挡住，
    // 用户看到的就是「点保存没反应」。这里同时写进弹窗内联错误并滚到可见位置。
    const fail = (msg: string) => {
      setSaveError(msg);
      toast(msg, "error");
      setTimeout(() => {
        document.getElementById("bot-save-error")?.scrollIntoView({ block: "nearest" });
      }, 0);
    };
    if (!editing?.name?.trim()) return fail("请填写名字");
    if (!editing.provider_id) {
      return fail(
        providers.length ? "请选择模型厂商" : "还没有可用的模型厂商，请先到设置页添加一个"
      );
    }
    if (!editing.model?.trim()) return fail("请选择模型（手动输入模型名后需按回车确认）");
    setSaving(true);
    setSaveError(null);
    try {
      const body = {
        name: editing.name.trim(),
        avatar: editing.avatar,
        role: editing.role,
        system_prompt: editing.system_prompt,
        persona: editing.persona || "",
        proactivity: editing.proactivity ?? 0.5,
        provider_id: editing.provider_id || null,
        model: editing.model || null,
        temperature: editing.temperature ?? 0.4,
        tools: editing.tools || [],
        skills: editing.skills || [],
      };
      if (editing.id) await api.patchBot(editing.id, body);
      else await api.createBot(body);
      toast("已保存", "ok");
      setEditing(null);
      setSaveError(null);
      load();
    } catch (e: any) {
      setSaveError(errText(e, "保存失败，请稍后重试"));
      toastError(e);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (b: Bot) => {
    const ok = await confirm({
      title: `删除 Bot「${b.name}」？`,
      description: "历史消息会保留并显示为「已删除的 Bot」，该操作不可撤销。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteBot(b.id);
      toast("已删除", "ok");
      load();
    } catch (e: any) {
      toastError(e);
    }
  };

  const providerOptions = useMemo(
    () =>
      providers.map((p) => ({
        value: p.id,
        label: p.name || p.vendor_name,
        description: `${p.models.length} 个模型${p.kind === "mock" ? " · 演示" : ""}`,
        avatar: p.logo,
        color: p.color,
      })),
    [providers]
  );

  const currentProvider = providers.find((x) => x.id === editing?.provider_id);
  const currentModel = currentProvider?.models.find((m) => m.name === editing?.model);
  const currentCap = modelCapability(currentModel);

  const modelOptions = useMemo(() => {
    const p = providers.find((x) => x.id === editing?.provider_id);
    return (p?.models || []).map((m) => {
      const cap = modelCapability(m);
      return {
        value: m.name,
        label: m.name,
        description: [
          m.input_price ? `输入 $${m.input_price}/M` : null,
          cap.text,
        ]
          .filter(Boolean)
          .join(" · "),
        // MOD-02：明确不支持流式 / 工具调用的模型在本产品里跑不动任何一步，
        // 置灰避免误选；真要绑定仍可在下拉里手动输入模型名。
        disabled: cap.bad,
      };
    });
  }, [providers, editing?.provider_id]);

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <PageHeader
            eyebrow="AGENT / BOTS"
            title="智能体团队"
            description="为团队配置不同角色、模型和工具，让每个智能体各司其职。"
            actions={
              <button className="btn-primary" onClick={newBot}>
                <Plus size={14} /> 创建智能体
              </button>
            }
          />

          <StatGrid items={[
            { label: "团队智能体", value: botsLoading ? "—" : bots.filter(b => !b.deleted).length, icon: Blocks, note: "按角色组织你的 AI 团队" },
            { label: "已启用", value: botsLoading ? "—" : bots.filter(b => !b.deleted && b.enabled !== false).length, icon: CircleCheck, note: "可参与新的协作任务" },
            { label: "模型接入", value: providers.length, icon: Cpu, note: "已配置的模型服务商" },
            { label: "可用工具", value: tools.filter(t => t.available !== false).length, icon: Plug, note: "内置工具与 MCP 扩展" },
          ]} />
          <section aria-label="智能体列表">
            <div className="collection-toolbar">
              <div className="collection-tabs" aria-label="智能体状态筛选">
                {([{ value: "all", label: "全部智能体" }, { value: "enabled", label: "已启用" }, { value: "disabled", label: "已停用" }] as const).map(item => <button key={item.value} aria-pressed={filter === item.value} onClick={() => setFilter(item.value)} className="collection-tab">{item.label}</button>)}
              </div>
              <div className="flex w-full items-center gap-3 sm:w-auto">
                <label className="collection-search min-w-0 flex-1"><Search size={14} className="shrink-0 text-faint" /><input aria-label="搜索智能体" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索名称、角色或模型" /></label>
                <div className="flex shrink-0 rounded-lg border border-line bg-panel p-0.5" aria-label="显示方式">
                  <button aria-label="卡片视图" aria-pressed={view === "grid"} className={`icon-btn h-8 w-8 ${view === "grid" ? "bg-accent/10 text-accent" : ""}`} onClick={() => setView("grid")}><LayoutGrid size={15} /></button>
                  <button aria-label="列表视图" aria-pressed={view === "list"} className={`icon-btn h-8 w-8 ${view === "list" ? "bg-accent/10 text-accent" : ""}`} onClick={() => setView("list")}><List size={16} /></button>
                </div>
              </div>
            </div>
            {view === "grid" ? <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {visibleBots.map(b => <article key={b.id} className="agent-card">
                <div className="agent-card-head flex items-start gap-3">
                  <Avatar value={b.avatar} size={44} alt={b.name} className="!border-accent/15 !bg-accent/10 !text-accent" />
                  <div className="min-w-0 flex-1"><button className="text-left text-[15px] font-semibold hover:text-accent" onClick={() => { refreshProviders(); setEditing(b); }}>{b.name}</button><p className="mt-1 text-[12px] text-muted">{b.role}</p></div>
                  <button aria-label={`删除${b.name}`} className="icon-btn -mr-2 -mt-1 hover:text-danger" onClick={() => remove(b)}><Trash2 size={14} /></button>
                </div>
                <div className="agent-card-body">
                  <p className="mb-5 line-clamp-2 min-h-[42px] text-[12px] leading-[21px] text-muted">{b.system_prompt || "配置角色提示词，为智能体明确职责和工作方式。"}</p>
                  <div className="flex items-center gap-2 rounded-lg bg-panel2/70 px-3 py-2.5 text-[11px]"><Cpu size={14} className="shrink-0 text-accent" /><span className="min-w-0 truncate font-mono">{b.model || "尚未绑定模型"}</span><span className="ml-auto shrink-0 text-faint">模型</span></div>
                  <div className="mt-4 flex items-center gap-4 text-[11px] text-muted"><span className="flex items-center gap-1.5"><Wrench size={13} />{b.tools?.length || 0} 个工具</span><span className="flex items-center gap-1.5"><Sparkles size={13} />{b.skills?.length || 0} 项技能</span></div>
                </div>
                <div className="agent-card-foot">
                  <BotStatusSwitch bot={b} onToggle={toggleEnabled} />
                  <button className="flex min-h-8 items-center gap-1.5 text-[12px] font-medium text-accent hover:underline" onClick={() => { refreshProviders(); setEditing(b); }}>配置智能体<ArrowUpRight size={13} /></button>
                </div>
              </article>)}
            </div> : <div className="overflow-hidden rounded-xl border border-line bg-panel">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-center">
                <thead className="border-b border-line bg-panel2/50 text-[11px] font-medium text-muted"><tr><th scope="col" className="px-5 py-3 font-medium">成员 / 角色</th><th scope="col" className="px-4 py-3 font-medium">模型接入</th><th scope="col" className="px-4 py-3 font-medium">能力</th><th scope="col" className="px-4 py-3 font-medium">状态</th><th scope="col" className="px-5 py-3 font-medium">管理</th></tr></thead>
                <tbody className="divide-y divide-line [&_td]:align-middle">
                  {visibleBots.map(b => <tr key={b.id} className="group transition-colors hover:bg-panel2/40">
                    <td className="px-5 py-5"><div className="flex items-center justify-center gap-3"><Avatar value={b.avatar} size={38} alt={b.name} /><div className="min-w-0"><button className="text-[13px] font-semibold hover:text-accent" onClick={() => { refreshProviders(); setEditing(b); }}>{b.name}</button><p className="mt-1 text-[11px] text-muted">{b.role}</p></div></div></td>
                    <td className="px-4 py-5"><p className="mx-auto max-w-[200px] truncate font-mono text-[11px]">{b.model || "尚未绑定模型"}</p><p className="mt-1 text-[11px] text-faint">{providers.find(p => p.id === b.provider_id)?.name || providers.find(p => p.id === b.provider_id)?.vendor_name || "未配置接入"}</p></td>
                    <td className="px-4 py-5"><div className="flex justify-center gap-2 text-[11px] text-muted"><span className="flex items-center gap-1.5"><Wrench size={12} />{b.tools?.length || 0} 工具</span><span className="flex items-center gap-1.5"><Sparkles size={12} />{b.skills?.length || 0} 技能</span></div><p className="mx-auto mt-1.5 max-w-[220px] truncate text-[11px] text-faint">{b.system_prompt || "尚未设置角色提示词"}</p></td>
                    <td className="px-4 py-5"><BotStatusSwitch bot={b} onToggle={toggleEnabled} /></td>
                    <td className="px-5 py-5"><div className="flex items-center justify-center gap-2"><button className="btn btn-sm text-muted hover:bg-panel2 hover:text-ink" onClick={() => { refreshProviders(); setEditing(b); }}>配置<ArrowUpRight size={12} /></button><button aria-label={`删除${b.name}`} className="icon-btn hover:text-danger" onClick={() => remove(b)}><Trash2 size={14} /></button></div></td>
                  </tr>)}
                </tbody>
              </table>
            </div>
            </div>}
            {!botsLoading && bots.length > 0 && !visibleBots.length && <div className="py-14 text-center text-[13px] text-muted">没有匹配的智能体，试试其他名称或筛选条件。</div>}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 px-1 py-2 text-[11px] text-faint"><span>显示 {visibleBots.length} 个成员</span><span>修改配置后，下次执行生效</span></div>
          </section>

          {botsLoading && (
            <div className="mt-10 flex justify-center">
              <Loader2 size={16} className="animate-spin text-faint" />
            </div>
          )}
          {!botsLoading && !bots.length && (
            <div className="empty-state mt-10">
              <div className="empty-icon">
                <Sparkles size={19} />
              </div>
              <div className="mt-3 text-[13px] font-medium">还没有 Bot</div>
              <p className="mx-auto mt-1 max-w-xs text-[12px] leading-relaxed text-muted">
                每个 Bot 负责一类工作。先创建几个角色，再把任务交给总指挥调度。
              </p>
              <button className="btn-primary mt-4" onClick={newBot}>
                <Plus size={14} /> 创建智能体
              </button>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        width="max-w-lg"
        title={
          <span className="flex items-center gap-2">
            <Wrench size={14} className="text-accent" />
            {editing?.id ? "编辑 Bot" : "新建 Bot"}
          </span>
        }
        footer={
          <>
            <button className="btn-ghost" onClick={() => setEditing(null)}>
              取消
            </button>
            <button className="btn-primary" onClick={save} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存
            </button>
          </>
        }
      >
        {editing && (
          <div className="space-y-4">
            {saveError && (
              <div
                id="bot-save-error"
                className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger"
              >
                {saveError}
              </div>
            )}
            {!providers.length && (
              <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[11.5px] leading-relaxed text-warn">
                还没有可用的模型厂商，Bot 需要绑定模型才能创建。请先到{" "}
                <Link href="/settings" className="underline">
                  设置页
                </Link>{" "}
                添加厂商后再回来。
              </div>
            )}
            {!editing.id && templates.length > 0 && (
              <div>
                <label className="label">从模板开始</label>
                <div className="flex flex-wrap gap-1.5">
                  {templates.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => applyTemplate(t)}
                      title={t.description}
                      className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[12px] transition-colors ${
                        editing.role === t.role
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-line text-muted hover:border-accent/40 hover:text-ink"
                      }`}
                    >
                      <Avatar value={t.avatar} size={16} />
                      {t.name}
                    </button>
                  ))}
                </div>
                <p className="mt-1 text-[10.5px] text-faint">
                  选中后会自动填好名字、角色、提示词与工具，可继续修改
                </p>
              </div>
            )}

            <div>
              <label className="label">头像</label>
              <AvatarPicker
                value={editing.avatar || "bot"}
                onChange={(v) => setEditing({ ...editing, avatar: v })}
              />
            </div>

            <div className="grid grid-cols-[1fr_1fr] gap-3">
              <div>
                <label className="label">名字（用于 @，同一账号下唯一）</label>
                <input
                  className="field"
                  value={editing.name || ""}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  placeholder="例如：研究员"
                />
              </div>
              <div>
                <label className="label">角色</label>
                <input
                  className="field"
                  value={editing.role || ""}
                  onChange={(e) => setEditing({ ...editing, role: e.target.value })}
                  placeholder="例如：研究员 / 架构师 / 总指挥"
                />
              </div>
            </div>

            <div>
              <label className="label">角色提示词</label>
              <textarea
                className="field h-28 resize-none leading-relaxed"
                value={editing.system_prompt || ""}
                onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
                placeholder="描述这个 Bot 的职责、方法与边界。"
              />
            </div>

            <div>
              <label className="label">性格与说话方式</label>
              <textarea className="field h-20 resize-none" maxLength={4000}
                value={editing.persona || ""}
                onChange={(e) => setEditing({ ...editing, persona: e.target.value })}
                placeholder="例如：严谨但不拘谨，喜欢用短句说明结论，偶尔开个玩笑。" />
              <label className="label mt-3">积极度（{(editing.proactivity ?? 0.5).toFixed(1)}）</label>
              <input aria-label="积极度" type="range" min={0} max={1} step={0.1}
                value={editing.proactivity ?? 0.5}
                onChange={(e) => setEditing({ ...editing, proactivity: Number(e.target.value) })}
                className="w-full accent-[var(--accent)]" />
              <p className="mt-1 text-[10.5px] text-faint">自由群聊中，越高越愿意参与讨论。被 @ 时仍会回应。</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">模型厂商</label>
                <Select
                  value={editing.provider_id || ""}
                  onChange={(v) => setEditing({ ...editing, provider_id: v || null, model: null })}
                  options={providerOptions}
                  placeholder="选择厂商"
                  emptyText="还没有添加厂商，请先到设置页添加"
                />
              </div>
              <div>
                <label className="label">模型</label>
                <Select
                  value={editing.model || ""}
                  onChange={(v) => setEditing({ ...editing, model: v })}
                  options={modelOptions}
                  placeholder={editing.provider_id ? "选择或输入模型名" : "请先选择厂商"}
                  searchable
                  freeInput
                  disabled={!editing.provider_id}
                  emptyText="该厂商没有可用模型，可直接输入模型名"
                />
                {editing.provider_id && !editing.model && (
                  <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                    可直接输入模型名，输入后按回车确认。
                  </p>
                )}
                {currentCap.bad && (
                  <p className="mt-1 text-[10.5px] leading-relaxed text-warn">
                    该模型{currentCap.text}，执行会失败，也不能担任总指挥。
                  </p>
                )}
                {currentCap.unknown && !!editing.model && (
                  <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                    该模型还没检测过能力；可在设置页点「检测能力」后再选。
                  </p>
                )}
              </div>
            </div>

            <div>
              <label className="label">温度（{(editing.temperature ?? 0.4).toFixed(1)}）</label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={editing.temperature ?? 0.4}
                onChange={(e) => setEditing({ ...editing, temperature: Number(e.target.value) })}
                className="w-full accent-[var(--accent)]"
              />
            </div>

            <div>
              <label className="label">可用工具</label>
              <div className="space-y-1 rounded-lg border border-line p-2">
                {tools.map((t) => {
                  const off = t.available === false;
                  return (
                    <label
                      key={t.name}
                      className={`block rounded px-1 py-1.5 ${
                        off ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-panel2"
                      }`}
                      title={off ? t.reason || "" : t.description}
                    >
                      {/* 勾选框和名称放在同一行做 flex 居中，说明文字缩进对齐到名称左侧 */}
                      <span className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)] disabled:cursor-not-allowed"
                          disabled={off}
                          checked={(editing.tools || []).includes(t.name)}
                          onChange={(e) =>
                            setEditing({
                              ...editing,
                              tools: e.target.checked
                                ? [...(editing.tools || []), t.name]
                                : (editing.tools || []).filter((x) => x !== t.name),
                            })
                          }
                        />
                        <span className="min-w-0 font-mono text-[11.5px]">{t.name}</span>
                        <span
                          className={`shrink-0 text-[10px] ${
                            t.level === "L3"
                              ? "text-warn"
                              : t.level === "L2"
                                ? "text-accent"
                                : "text-faint"
                          }`}
                        >
                          {t.level}
                        </span>
                        {t.source === "mcp" && (
                          <span className="chip shrink-0 border-accent/40 text-[9.5px] text-accent">
                            MCP
                          </span>
                        )}
                        {off && (
                          <span className="chip shrink-0 border-warn/40 text-warn">
                            未配置，暂不可用
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block pl-[22px] text-[10.5px] leading-relaxed text-faint">
                        {t.description}
                      </span>
                      {off && (
                        <span className="mt-0.5 block pl-[22px] text-[10.5px] text-warn">
                          {t.reason}{" "}
                          <Link href="/settings" className="underline hover:text-ink">
                            去设置页配置
                          </Link>
                        </span>
                      )}
                      {!off && t.reason && (
                        <span className="mt-0.5 block pl-[22px] text-[10.5px] leading-relaxed text-faint">
                          {t.reason}{" "}
                          <Link href="/settings" className="underline hover:text-ink">
                            配置 Key 更稳定
                          </Link>
                        </span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>

            <div>
              <label className="label">技能</label>
              <div className="space-y-1 rounded-lg border border-line p-2">
                {skills.length === 0 && (
                  <div className="px-1 py-2 text-[11px] leading-relaxed text-faint">
                    还没有技能，可到「技能」页新建，再回来绑定。
                  </div>
                )}
                {skills.map((sk) => (
                  <label
                    key={sk.id}
                    className="block cursor-pointer rounded px-1 py-1.5 hover:bg-panel2"
                    title={sk.description}
                  >
                    <span className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                        checked={(editing.skills || []).includes(sk.id)}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            skills: e.target.checked
                              ? [...(editing.skills || []), sk.id]
                              : (editing.skills || []).filter((x) => x !== sk.id),
                          })
                        }
                      />
                      <Sparkles size={12} className="shrink-0 text-accent" />
                      <span className="min-w-0 truncate text-[12px]">{sk.name}</span>
                      {!sk.enabled && <span className="chip shrink-0 text-faint">已停用</span>}
                      {!!sk.tools.length && (
                        <span className="ml-auto shrink-0 text-[10px] text-faint">
                          {sk.tools.length} 个工具
                        </span>
                      )}
                    </span>
                    {sk.description && (
                      <span className="mt-0.5 block pl-[38px] text-[10.5px] leading-relaxed text-faint">
                        {sk.description}
                      </span>
                    )}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                技能的提示词会注入系统提示，技能里的工具自动并入上面的可用工具。
              </p>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
