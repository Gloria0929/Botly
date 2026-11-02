"use client";

import { useEffect, useState } from "react";
import { Braces, Loader2, Plug, RefreshCw, Trash2 } from "lucide-react";
import Modal from "@/components/ui/Modal";
import PageHeader from "@/components/ui/PageHeader";
import Select from "@/components/ui/Select";
import { useConfirm } from "@/components/ui/Confirm";
import { api, toastError, errText } from "@/lib/api";
import type { McpServer } from "@/lib/types";
import { toast } from "@/lib/ui";

const EMPTY = {
  name: "",
  transport: "http",
  url: "",
  command: "",
  args: "",
  env: "",
  token: "",
  level: "L3",
  enabled: true,
};

/**
 * TOL-05：MCP 服务接入。
 * 服务级指定权限级别（默认 L3）；工具在「检测」时拉到本地缓存，Bot / 技能里即可勾选。
 */
export default function McpSection() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<(typeof EMPTY & { id?: string }) | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; ok: boolean; text: string } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState<{
    created: any[];
    skipped: { name: string; reason: string }[];
    errors: { name: string; reason: string }[];
  } | null>(null);
  const confirm = useConfirm();

  const load = () =>
    api.mcpServers().then(setServers).catch((e) => toastError(e, "MCP 服务加载失败")).finally(() => setLoading(false));
  useEffect(() => {
    load();
  }, []);

  const openNew = () => {
    setSaveError(null);
    setEditing({ ...EMPTY });
  };
  const openEdit = (s: McpServer) =>
    setEditing({
      id: s.id,
      name: s.name,
      transport: s.transport || "http",
      url: s.url,
      command: s.command || "",
      args: (s.args || []).join("\n"),
      env: Object.entries(s.env || {})
        .map(([k, v]) => `${k}=${v}`)
        .join("\n"),
      token: "",
      level: s.level,
      enabled: s.enabled,
    });

  const save = async () => {
    if (!editing?.name?.trim()) return toast("请填写服务名称", "error");
    const isStdio = editing.transport === "stdio";
    if (isStdio && !editing.command?.trim()) return toast("请填写启动命令", "error");
    if (!isStdio && !editing.url?.trim()) return toast("请填写 MCP 地址", "error");
    const parseArgs = () =>
      (editing.args || "")
        .split(/\s+/)
        .map((x) => x.trim())
        .filter(Boolean);
    const parseEnv = () => {
      const env: Record<string, string> = {};
      for (const line of (editing.env || "").split(/\n/)) {
        const idx = line.indexOf("=");
        if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      }
      return env;
    };
    setSaving(true);
    setSaveError(null);
    try {
      const body: any = {
        name: editing.name.trim(),
        transport: editing.transport,
        level: editing.level,
        enabled: editing.enabled,
        ...(isStdio
          ? { command: editing.command.trim(), args: parseArgs(), env: parseEnv() }
          : { url: editing.url.trim() }),
      };
      if (!isStdio && editing.token) body.token = editing.token;
      if (editing.id) {
        await api.patchMcpServer(editing.id, body);
      } else {
        body.token = isStdio ? (editing.token || null) : (editing.token || null);
        await api.createMcpServer(body);
      }
      toast("已保存，正在检测工具", "ok");
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

  const importJson = async () => {
    if (!importText.trim()) return toast("请粘贴 JSON 配置", "error");
    let obj: any;
    try {
      obj = JSON.parse(importText);
    } catch {
      return toast("JSON 解析失败，请检查格式", "error");
    }
    setImportBusy(true);
    setImportResult(null);
    try {
      const r = await api.importMcpServers(obj);
      setImportResult(r);
      const failed = r.errors.length;
      toast(
        `导入完成：新增 ${r.created.length}，跳过 ${r.skipped.length}，失败 ${failed}`,
        failed ? "error" : "ok"
      );
      load();
    } catch (e: any) {
      toastError(e);
    } finally {
      setImportBusy(false);
    }
  };

  const test = async (s: McpServer) => {
    setTesting(s.id);
    setTestResult(null);
    try {
      const r = await api.testMcpServer(s.id);
      setTestResult({
        id: s.id,
        ok: r.ok,
        text: r.ok ? `检测通过，共 ${r.tools.length} 个工具` : r.error || "检测失败",
      });
      if (r.ok) toast("检测通过", "ok");
      load();
    } catch (e: any) {
      setTestResult({ id: s.id, ok: false, text: errText(e, "连接测试失败") });
      toastError(e);
    } finally {
      setTesting(null);
    }
  };

  const remove = async (s: McpServer) => {
    const ok = await confirm({
      title: `删除 MCP 服务「${s.name}」？`,
      description: "已勾选它工具的 Bot 与技能会自动移除这些工具，历史消息不受影响。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteMcpServer(s.id);
      toast("已删除", "ok");
      load();
    } catch (e: any) {
      toastError(e);
    }
  };

  const statusChip = (s: McpServer) => {
    if (s.status === "ok")
      return <span className="chip border-ok/40 text-ok">已连接 · {s.tool_count} 个工具</span>;
    if (s.status === "error") return <span className="chip border-danger/40 text-danger">检测失败</span>;
    return <span className="chip text-faint">未检测</span>;
  };

  return (
    <section>
      <PageHeader
        eyebrow="INTEGRATIONS / MCP"
        title="MCP 工具"
        description="接入 MCP 服务（HTTP 或本地 stdio 进程）后，它的工具会出现在 Bot 与技能的「可用工具」里。默认按 L3 处理：每次调用都要审批。"
        actions={
          <>
            <button className="btn-ghost" onClick={() => { setImportOpen(true); setImportText(""); setImportResult(null); }}>
              <Braces size={13} /> 从 JSON 导入
            </button>
            <button className="btn-primary" onClick={openNew}>
              <Plug size={13} /> 添加服务
            </button>
          </>
        }
      />
      <div className="card mt-6 p-3.5">
        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 size={15} className="animate-spin text-faint" />
          </div>
        ) : servers.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center">
            <div className="empty-icon">
              <Plug size={19} />
            </div>
            <div className="mt-3 text-[12.5px] font-medium">还没有接入 MCP 服务</div>
            <p className="mx-auto mt-1 max-w-md text-[11.5px] leading-relaxed text-muted">
              可接远程的 Streamable HTTP 端点，或本机的 stdio 进程，
              例如把内部工具、数据库查询封装成 MCP 后接进来。
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {servers.map((s) => (
              <div key={s.id} className="rounded-lg border border-line bg-panel2/30 px-3.5 py-3 transition-colors hover:border-line2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[12.5px] font-medium">{s.name}</span>
                  {statusChip(s)}
                  <span className="chip text-[10px]">{s.level}</span>
                  {!s.enabled && <span className="chip text-faint">已停用</span>}
                  <div className="ml-auto flex items-center gap-2">
                    <button
                      className="btn-ghost px-2 py-1 text-[11px]"
                      onClick={() => test(s)}
                      disabled={testing === s.id}
                    >
                      {testing === s.id ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <RefreshCw size={12} />
                      )}
                      检测
                    </button>
                    <button className="text-[11.5px] text-muted hover:text-ink" onClick={() => openEdit(s)}>
                      编辑
                    </button>
                    <button className="text-faint hover:text-danger" onClick={() => remove(s)}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <div
                  className="mt-1 truncate font-mono text-[10.5px] text-faint"
                  title={(s.transport === "stdio" ? [s.command, ...(s.args || [])].join(" ") : s.url) || ""}
                >
                  {s.transport === "stdio"
                    ? [s.command, ...(s.args || [])].filter(Boolean).join(" ")
                    : s.url}
                </div>
                {s.last_error && (
                  <div className="mt-1 text-[10.5px] leading-relaxed text-danger">
                    {s.last_error}
                  </div>
                )}
                {testResult?.id === s.id && (
                  <div
                    className={`mt-1 text-[10.5px] leading-relaxed ${
                      testResult.ok ? "text-ok" : "text-danger"
                    }`}
                  >
                    {testResult.text}
                  </div>
                )}
                {s.tools.length > 0 && (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-[10.5px] text-faint hover:text-ink">
                      查看 {s.tools.length} 个工具
                    </summary>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {s.tools.map((t) => (
                        <span key={t.name} className="chip font-mono text-[10px]" title={t.description}>
                          {t.name}
                        </span>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="mt-2 text-[10.5px] leading-relaxed text-faint">
          默认只允许公网地址；自建的本机 MCP 服务需要把环境变量{" "}
          <code className="rounded bg-panel2 px-1">BOTLY_MCP_ALLOW_LOCAL</code> 设为 1。
        </p>
      </div>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={editing?.id ? "编辑 MCP 服务" : "添加 MCP 服务"}
        subtitle="保存后会立即检测 tools/list，通常 1–10 秒"
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setEditing(null)}>
              取消
            </button>
            <button className="btn-primary" onClick={save} disabled={saving}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Plug size={13} />} 保存
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
            <div>
              <label className="label">服务名称（同一账号下唯一）</label>
              <input
                className="field"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="例如：内部知识库"
              />
            </div>
            <div>
              <label className="label">传输方式</label>
              <Select
                value={editing.transport}
                onChange={(v) => setEditing({ ...editing, transport: v })}
                options={[
                  { value: "http", label: "HTTP", description: "远程地址（Streamable HTTP / SSE）" },
                  { value: "stdio", label: "stdio", description: "本地进程，标准输入输出通信" },
                ]}
              />
            </div>

            {editing.transport === "stdio" ? (
              <>
                <div>
                  <label className="label">启动命令</label>
                  <input
                    className="field font-mono text-[12px]"
                    value={editing.command || ""}
                    onChange={(e) => setEditing({ ...editing, command: e.target.value })}
                    placeholder="npx -y @some/mcp 或 python script.py"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="label">参数（空格或换行分隔）</label>
                    <textarea
                      className="field h-20 resize-none font-mono text-[11.5px]"
                      value={editing.args || ""}
                      onChange={(e) => setEditing({ ...editing, args: e.target.value })}
                      placeholder="--port 9000"
                    />
                  </div>
                  <div>
                    <label className="label">环境变量（K=V 每行）</label>
                    <textarea
                      className="field h-20 resize-none font-mono text-[11.5px]"
                      value={editing.env || ""}
                      onChange={(e) => setEditing({ ...editing, env: e.target.value })}
                      placeholder="API_KEY=xxx"
                    />
                  </div>
                </div>
              </>
            ) : (
              <div>
                <label className="label">MCP 地址（Streamable HTTP / SSE）</label>
                <input
                  className="field font-mono text-[12px]"
                  value={editing.url}
                  onChange={(e) => setEditing({ ...editing, url: e.target.value })}
                  placeholder="https://example.com/mcp"
                />
              </div>
            )}

            <div className="grid grid-cols-[1fr_1fr] gap-3">
              {editing.transport !== "stdio" && (
                <div>
                  <label className="label">访问令牌（可选）</label>
                  <input
                    className="field"
                    type="password"
                    value={editing.token}
                    onChange={(e) => setEditing({ ...editing, token: e.target.value })}
                    placeholder={editing.id ? "留空表示不修改" : "作为 Authorization: Bearer 发送"}
                  />
                </div>
              )}
              <div>
                <label className="label">权限级别</label>
                <Select
                  value={editing.level}
                  onChange={(v) => setEditing({ ...editing, level: v })}
                  options={[
                    { value: "L3", label: "L3 · 每次审批", description: "外部副作用，默认" },
                    { value: "L2", label: "L2 · 沙箱级", description: "群聊可要求审批" },
                    { value: "L1", label: "L1 · 只读", description: "无需审批，请确认服务无副作用" },
                  ]}
                />
              </div>
            </div>
            <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-line px-3 py-2.5">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-[var(--accent)]"
                checked={editing.enabled}
                onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
              />
              <span className="text-[12.5px]">启用这个服务</span>
              <span className="ml-auto text-[10.5px] text-faint">停用后工具不再下发给 Bot</span>
            </label>
          </div>
        )}
      </Modal>

      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title={
          <span className="flex items-center gap-2">
            <Braces size={14} className="text-accent" /> 从 JSON 导入 MCP 服务
          </span>
        }
        subtitle="支持 Claude Desktop 的 mcpServers 格式；stdio 用 command/args/env，远程用 url/headers"
        width="max-w-lg"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setImportOpen(false)}>
              取消
            </button>
            <button className="btn-primary" onClick={importJson} disabled={importBusy}>
              {importBusy ? <Loader2 size={13} className="animate-spin" /> : <Braces size={13} />} 导入
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <textarea
            className="field h-64 resize-none font-mono text-[11.5px] leading-relaxed"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder={'{\n  "mcpServers": {\n    "github": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-github"],\n      "env": { "GITHUB_TOKEN": "..." }\n    },\n    "远程知识库": {\n      "url": "https://example.com/mcp",\n      "headers": { "Authorization": "Bearer ..." },\n      "level": "L3"\n    }\n  }\n}'}
          />
          {importResult && (
            <div className="space-y-1 text-[11.5px] leading-relaxed">
              {importResult.created.length > 0 && (
                <div className="text-ok">
                  新增 {importResult.created.length} 个：{importResult.created.map((c) => c.name).join("、")}
                </div>
              )}
              {importResult.skipped.map((s) => (
                <div key={s.name} className="text-faint">
                  跳过「{s.name}」：{s.reason}
                </div>
              ))}
              {importResult.errors.map((e) => (
                <div key={e.name} className="break-words text-danger">
                  「{e.name}」失败：{e.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </section>
  );
}
