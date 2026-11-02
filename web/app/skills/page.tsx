"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, Github, Link2, Loader2, Plus, Save, Sparkles, Trash2, Upload } from "lucide-react";
import AppShell from "@/components/AppShell";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/components/ui/Confirm";
import { api } from "@/lib/api";
import type { Skill } from "@/lib/types";
import { toast } from "@/lib/ui";

type ToolRow = {
  name: string;
  description: string;
  level: string;
  available?: boolean;
  reason?: string | null;
  source?: "builtin" | "mcp";
};

const EMPTY: Partial<Skill> = {
  name: "",
  description: "",
  prompt: "",
  tools: [],
  enabled: true,
};

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<ToolRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Partial<Skill> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [clawhubSlug, setClawhubSlug] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const confirm = useConfirm();

  const load = () => {
    api.skills().then(setSkills).catch(() => {}).finally(() => setLoading(false));
    api.tools().then(setTools).catch(() => {});
  };
  useEffect(load, []);

  const grouped = useMemo(
    () => ({
      builtin: tools.filter((t) => t.source !== "mcp"),
      mcp: tools.filter((t) => t.source === "mcp"),
    }),
    [tools]
  );

  const toggleTool = (name: string) => {
    if (!editing) return;
    const current = editing.tools || [];
    setEditing({
      ...editing,
      tools: current.includes(name)
        ? current.filter((x) => x !== name)
        : [...current, name],
    });
  };

  const save = async () => {
    if (!editing?.name?.trim()) return toast("请填写技能名称", "error");
    setSaving(true);
    setSaveError(null);
    try {
      const body = {
        name: editing.name.trim(),
        description: editing.description || "",
        prompt: editing.prompt || "",
        tools: editing.tools || [],
        enabled: editing.enabled !== false,
      };
      if (editing.id) await api.patchSkill(editing.id, body);
      else await api.createSkill(body);
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

  const finishImport = (skill: Skill) => {
    toast(`已导入「${skill.name}」`, "ok");
    setImportOpen(false);
    setImportUrl("");
    setImportFile(null);
    setClawhubSlug("");
    load();
  };

  const importFromGithub = async () => {
    if (!githubRepo.trim()) return toast("请填写 GitHub 仓库（owner/repo）", "error");
    setImportBusy(true);
    setImportError(null);
    try {
      finishImport(await api.importSkillFromGithub(githubRepo.trim()));
    } catch (e: any) {
      setImportError(e.message || "导入失败，请稍后重试");
      toast(e.message, "error");
    } finally {
      setImportBusy(false);
    }
  };

  const importFromUrl = async () => {
    if (!importUrl.trim()) return toast("请填写技能包地址", "error");
    setImportBusy(true);
    try {
      finishImport(await api.importSkillUrl(importUrl.trim()));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setImportBusy(false);
    }
  };

  const importFromClawhub = async () => {
    if (!clawhubSlug.trim()) return toast("请填写技能名称（slug）", "error");
    setImportBusy(true);
    try {
      finishImport(await api.importSkillFromClawhub(clawhubSlug.trim()));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setImportBusy(false);
    }
  };

  const importFromFile = async () => {
    if (!importFile) return toast("请选择技能包文件", "error");
    setImportBusy(true);
    try {
      finishImport(await api.importSkillFile(importFile));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setImportBusy(false);
    }
  };

  const remove = async (s: Skill) => {
    const ok = await confirm({
      title: `删除技能「${s.name}」？`,
      description: "已绑定它的 Bot 会自动解除绑定，历史消息不受影响。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteSkill(s.id);
      toast("已删除", "ok");
      load();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const toolLabel = (t: ToolRow) => (
    <span className="flex items-center gap-2">
      <input
        type="checkbox"
        className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
        checked={(editing?.tools || []).includes(t.name)}
        onChange={() => toggleTool(t.name)}
      />
      <span className="min-w-0 truncate font-mono text-[11.5px]">{t.name}</span>
      {t.source === "mcp" && (
        <span className="chip shrink-0 border-accent/40 text-[9.5px] text-accent">MCP</span>
      )}
      <span
        className={`shrink-0 text-[10px] ${
          t.level === "L3" ? "text-warn" : t.level === "L2" ? "text-accent" : "text-faint"
        }`}
      >
        {t.level}
      </span>
    </span>
  );

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <div className="flex items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">技能</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                技能 = 一段可复用的方法提示词 + 一组工具。绑定到 Bot 后自动注入提示词并合并工具。
              </p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <button className="btn-ghost" onClick={() => setImportOpen(true)}>
                <Download size={14} /> 导入技能
              </button>
              <button className="btn-primary" onClick={() => setEditing({ ...EMPTY })}>
                <Plus size={14} /> 新建技能
              </button>
            </div>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {skills.map((s) => (
              <div key={s.id} className="card p-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent/12 text-accent">
                    <Sparkles size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13.5px] font-medium">{s.name}</span>
                      {s.source && s.source !== "manual" && (
                        <span className="chip border-accent/40 text-[9.5px] text-accent">
                          导入 · {s.source === "github" ? "GitHub" : s.source.toUpperCase()}
                        </span>
                      )}
                      {!s.enabled && <span className="chip text-faint">已停用</span>}
                    </div>
                    <div className="mt-0.5 text-[11.5px] text-faint">
                      {s.bot_count ? `${s.bot_count} 个 Bot 在用` : "还没有 Bot 使用"}
                      {s.tools.length ? ` · ${s.tools.length} 个工具` : ""}
                    </div>
                  </div>
                  <button
                    onClick={() => setEditing(s)}
                    className="text-[11.5px] text-muted hover:text-ink"
                  >
                    编辑
                  </button>
                  <button onClick={() => remove(s)} className="text-faint hover:text-danger">
                    <Trash2 size={14} />
                  </button>
                </div>
                <p className="mt-3 line-clamp-2 text-[11.5px] leading-relaxed text-muted">
                  {s.description || s.prompt || "（无说明）"}
                </p>
                <div className="mt-3 flex flex-wrap gap-1">
                  {s.tools.slice(0, 6).map((t) => (
                    <span key={t} className="chip font-mono">
                      {t}
                    </span>
                  ))}
                  {s.tools.length > 6 && (
                    <span className="chip text-faint">+{s.tools.length - 6}</span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {loading && (
            <div className="mt-10 flex justify-center">
              <Loader2 size={16} className="animate-spin text-faint" />
            </div>
          )}
          {!loading && !skills.length && (
            <div className="mt-10 rounded-xl border border-dashed border-line px-4 py-10 text-center text-[12.5px] leading-relaxed text-faint">
              还没有技能。把常用的工作方法（例如「调研先列来源再下结论」）保存成技能，
              绑定到 Bot 就能复用。
            </div>
          )}
        </div>
      </div>

      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title={
          <span className="flex items-center gap-2">
            <Download size={14} className="text-accent" />
            导入外部技能
          </span>
        }
        subtitle="支持带 SKILL.md 的 ZIP 技能包、单个 SKILL.md，或 SKILL.md / GitHub 仓库地址"
        width="max-w-lg"
      >
        <div className="space-y-5">
          {importError && (
            <div className="flex items-start gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
              <span className="mt-0.5 shrink-0">⚠</span>
              <span className="min-w-0 break-words">{importError}</span>
            </div>
          )}
          <div className="rounded-lg border border-accent/30 bg-accent/[0.06] p-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
              <Download size={13} className="text-accent" /> 从 ClawHub 下载
            </div>
            <div className="flex gap-2">
              <input
                className="field min-w-0 flex-1 font-mono text-[12px]"
                value={clawhubSlug}
                onChange={(e) => setClawhubSlug(e.target.value)}
                placeholder="owner/slug 或技能名，如 fangkelvin/find-skills-skill"
                onKeyDown={(e) => e.key === "Enter" && importFromClawhub()}
              />
              <button
                className="btn-primary shrink-0"
                onClick={importFromClawhub}
                disabled={importBusy}
              >
                {importBusy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                下载并导入
              </button>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
              填 owner/slug（或技能名）后从 ClawHub 拉取 ZIP 并导入；重名会自动加后缀。
            </p>
          </div>

          <div className="rounded-lg border border-line p-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
              <Github size={13} className="text-accent" /> 从 GitHub 仓库导入
            </div>
            <div className="flex gap-2">
              <input
                className="field min-w-0 flex-1 font-mono text-[12px]"
                value={githubRepo}
                onChange={(e) => setGithubRepo(e.target.value)}
                placeholder="owner/repo，如 zhaoxinghua09-cell/coding-agent"
                onKeyDown={(e) => e.key === "Enter" && importFromGithub()}
              />
              <button
                className="btn-primary shrink-0"
                onClick={importFromGithub}
                disabled={importBusy}
              >
                {importBusy ? <Loader2 size={13} className="animate-spin" /> : <Github size={13} />}
                导入
              </button>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
              会递归查找仓库里的 SKILL.md（取路径最浅的），并把同目录的参考文件一并导入。
            </p>
          </div>

          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
              <Link2 size={13} className="text-accent" /> 从 URL 导入
            </div>
            <div className="flex gap-2">
              <input
                className="field min-w-0 flex-1 font-mono text-[12px]"
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
                placeholder="https://github.com/owner/repo 或 https://.../SKILL.md"
              />
              <button className="btn-primary shrink-0" onClick={importFromUrl} disabled={importBusy}>
                {importBusy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                导入
              </button>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
              支持 GitHub 仓库链接（自动读取默认分支的 SKILL.md）、raw 文件地址和 ZIP 地址。
              只放 SKILL.md、不带附件的技能，导入后也可以在这里编辑提示词。
            </p>
          </div>

          <div className="border-t border-line pt-4">
            <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium">
              <Upload size={13} className="text-accent" /> 上传技能包
            </div>
            <input
              type="file"
              accept=".zip,.md,text/markdown,application/zip"
              onChange={(e) => setImportFile(e.target.files?.[0] || null)}
              className="block w-full cursor-pointer rounded-lg border border-line bg-panel2/40 px-3 py-2 text-[11.5px] text-muted file:mr-3 file:rounded-md file:border-0 file:bg-accent/15 file:px-2.5 file:py-1 file:text-[11px] file:text-accent"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                className="btn-primary"
                onClick={importFromFile}
                disabled={importBusy || !importFile}
              >
                {importBusy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                上传并导入
              </button>
              <span className="truncate text-[10.5px] text-faint">
                {importFile ? importFile.name : "ZIP 需包含 SKILL.md；单个 .md 也可直接导入"}
              </span>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
              ZIP 上限 5 MB、最多 100 个文件；包内参考文件会随技能保存，Bot 可用
              skill_file_read 读取。脚本不会被执行（当前没有代码沙箱）。
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        width="max-w-lg"
        title={
          <span className="flex items-center gap-2">
            <Sparkles size={14} className="text-accent" />
            {editing?.id ? "编辑技能" : "新建技能"}
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
            <div className="grid grid-cols-[1fr_1fr] gap-3">
              <div>
                <label className="label">名称（同一账号下唯一）</label>
                <input
                  className="field"
                  value={editing.name || ""}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  placeholder="例如：调研规范"
                />
              </div>
              <div>
                <label className="label">一句话说明</label>
                <input
                  className="field"
                  value={editing.description || ""}
                  onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                  placeholder="会出现在 Bot 的技能列表里"
                />
              </div>
            </div>

            <div>
              <label className="label">方法提示词</label>
              <textarea
                className="field h-40 resize-none leading-relaxed"
                value={editing.prompt || ""}
                onChange={(e) => setEditing({ ...editing, prompt: e.target.value })}
                placeholder="写下这个技能的工作方法、步骤和边界。绑定后会追加到 Bot 的系统提示词。"
              />
              <p className="mt-1 text-[10.5px] text-faint">
                提示词按「技能名 + 说明 + 内容」注入；多个技能合计最多注入 6,000 字。
              </p>
            </div>

            {!!(editing.files || []).length && (
              <div>
                <label className="label">技能包参考文件</label>
                <div className="flex flex-wrap gap-1 rounded-lg border border-line p-2">
                  {(editing.files || []).map((f) => (
                    <span key={f} className="chip font-mono text-[10px]">
                      {f}
                    </span>
                  ))}
                </div>
                <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                  绑定到 Bot 后可用 skill_file_read 读取这些文件；重新导入会新建一个技能，不会覆盖当前内容。
                </p>
              </div>
            )}

            <div>
              <label className="label">附带工具</label>
              <div className="max-h-56 space-y-2 overflow-y-auto rounded-lg border border-line p-2">
                {tools.length === 0 && (
                  <div className="px-1 py-2 text-[11px] text-faint">还没有可用工具</div>
                )}
                {grouped.builtin.length > 0 && (
                  <div>
                    <div className="px-1 pb-1 text-[10px] uppercase tracking-wide text-faint">
                      内置工具
                    </div>
                    {grouped.builtin.map((t) => (
                      <label
                        key={t.name}
                        className="block cursor-pointer rounded px-1 py-1.5 hover:bg-panel2"
                        title={t.description}
                      >
                        {toolLabel(t)}
                        <span className="mt-0.5 block pl-[22px] text-[10.5px] leading-relaxed text-faint">
                          {t.description}
                        </span>
                      </label>
                    ))}
                  </div>
                )}
                {grouped.mcp.length > 0 && (
                  <div>
                    <div className="px-1 pb-1 text-[10px] uppercase tracking-wide text-faint">
                      MCP 工具
                    </div>
                    {grouped.mcp.map((t) => (
                      <label
                        key={t.name}
                        className="block cursor-pointer rounded px-1 py-1.5 hover:bg-panel2"
                        title={t.description}
                      >
                        {toolLabel(t)}
                        <span className="mt-0.5 block pl-[22px] text-[10.5px] leading-relaxed text-faint">
                          {t.description}
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
                技能里的工具会并入绑定 Bot 的可用工具；L3 工具执行时仍会逐次审批。
              </p>
            </div>

            <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line px-3 py-2.5">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-[var(--accent)]"
                checked={editing.enabled !== false}
                onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
              />
              <span className="text-[12.5px]">启用这个技能</span>
              <span className="ml-auto text-[10.5px] text-faint">停用后已绑定的 Bot 也不再收到</span>
            </label>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
