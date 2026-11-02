"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, Plus, Save, Sparkles, Trash2, Wrench } from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import AvatarPicker from "@/components/ui/AvatarPicker";
import Select from "@/components/ui/Select";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api } from "@/lib/api";
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

export default function BotsPage() {
  const [bots, setBots] = useState<Bot[]>([]);
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
    api.bots().then(setBots).catch(() => {}).finally(() => setBotsLoading(false));
    api.providers().then(setProviders).catch(() => {});
    api.tools().then(setTools).catch(() => {});
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
      toast(e?.message || "操作失败", "error");
    }
  };

  // TOL-01：没配搜索 Key 时不要把 web_search 带进新 Bot / 模板默认值
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

  const newBot = () =>
    setEditing({ ...EMPTY, tools: onlyAvailable(EMPTY.tools || []) });

  const save = async () => {
    if (!editing?.name?.trim()) return toast("请填写名字", "error");
    if (!editing.provider_id) return toast("请选择模型厂商", "error");
    if (!editing.model?.trim()) return toast("请选择或填写模型", "error");
    setSaving(true);
    setSaveError(null);
    try {
      const body = {
        name: editing.name.trim(),
        avatar: editing.avatar,
        role: editing.role,
        system_prompt: editing.system_prompt,
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
      setSaveError(e.message || "保存失败，请稍后重试");
      toast(e.message, "error");
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
      toast(e.message, "error");
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
        <div className="mx-auto max-w-4xl px-6 py-8">
          <div className="flex items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">Bot 管理</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                每个 Bot = 角色提示词 + 模型 + 工具集。编辑只影响之后开始的执行。
              </p>
            </div>
            <button className="btn-primary ml-auto" onClick={newBot}>
              <Plus size={14} /> 新建 Bot
            </button>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {bots.map((b) => (
              <div key={b.id} className={`card p-4 ${b.enabled === false ? "opacity-60" : ""}`}>
                <div className="flex items-start gap-3">
                  <Avatar value={b.avatar} size={40} alt={b.name} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13.5px] font-medium">{b.name}</span>
                      {b.role === "总指挥" && (
                        <span className="chip border-accent/40 text-accent">总指挥</span>
                      )}
                      {b.enabled === false && <span className="chip text-faint">已停用</span>}
                    </div>
                    <div className="mt-0.5 text-[11.5px] text-faint">
                      {b.role} · {b.model || "未绑定模型"}
                    </div>
                  </div>
                  <button
                    onClick={() => toggleEnabled(b)}
                    title={b.enabled === false ? "启用" : "停用"}
                    aria-label={b.enabled === false ? "启用" : "停用"}
                    className={`relative mt-1 h-5 w-9 shrink-0 rounded-full transition-colors ${
                      b.enabled === false ? "bg-line" : "bg-ok/70"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                        b.enabled === false ? "left-0.5" : "left-[18px]"
                      }`}
                    />
                  </button>
                  <button
                    onClick={() => setEditing(b)}
                    className="text-[11.5px] text-muted hover:text-ink"
                  >
                    编辑
                  </button>
                  <button onClick={() => remove(b)} className="text-faint hover:text-danger">
                    <Trash2 size={14} />
                  </button>
                </div>
                <p className="mt-3 line-clamp-2 text-[11.5px] leading-relaxed text-muted">
                  {b.system_prompt || "（无提示词）"}
                </p>
                <div className="mt-3 flex flex-wrap gap-1">
                  {(() => {
                    const tools = b.tools || [];
                    const builtin = tools.filter((t) => !t.startsWith("mcp__"));
                    const mcp = tools.filter((t) => t.startsWith("mcp__"));
                    const shown = builtin.slice(0, 3);
                    const extra = builtin.length - shown.length;
                    return (
                      <>
                        {shown.map((t) => (
                          <span key={t} className="chip font-mono">
                            {t}
                          </span>
                        ))}
                        {extra > 0 && (
                          <span className="chip text-faint" title={builtin.join("、")}>
                            +{extra}
                          </span>
                        )}
                        {mcp.length > 0 && (
                          <span
                            className="chip border-accent/40 text-accent"
                            title={mcp.join("、")}
                          >
                            MCP ×{mcp.length}
                          </span>
                        )}
                      </>
                    );
                  })()}
                  {(b.skills || []).map((sid) => {
                    const skill = skills.find((x) => x.id === sid);
                    return skill ? (
                      <span key={sid} className="chip border-accent/40 text-accent">
                        <Sparkles size={9} /> {skill.name}
                      </span>
                    ) : null;
                  })}
                </div>
              </div>
            ))}
          </div>

          {botsLoading && (
            <div className="mt-10 flex justify-center">
              <Loader2 size={16} className="animate-spin text-faint" />
            </div>
          )}
          {!botsLoading && !bots.length && (
            <div className="mt-10 text-center text-[12.5px] text-faint">还没有 Bot</div>
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
              <div className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
                {saveError}
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
                  placeholder="例如：研究员 / 写手 / 总指挥"
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
