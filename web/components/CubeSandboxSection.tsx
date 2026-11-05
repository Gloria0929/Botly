"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  Loader2,
  Pencil,
  Plug,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { api, errText } from "@/lib/api";
import { toast } from "@/lib/ui";
import { timeAgo } from "@/lib/fmt";
import type { CubeTemplate, SandboxStatus } from "@/lib/types";
import {
  formatCubeProxy,
  normalizeCubeAddresses,
  parseCubeProxy,
  withHttpScheme,
} from "@/lib/cube-connection";
import { useConfirm } from "@/components/ui/Confirm";
import Select from "@/components/ui/Select";

const emptyForm = {
  api_url: "",
  template_id: "",
  api_key: "",
  proxy_address: "",
  sandbox_domain: "cube.app",
};
type Form = typeof emptyForm;
const formFrom = (state?: SandboxStatus): Form =>
  normalizeCubeAddresses({
    api_url: state?.api_url || "",
    template_id: state?.template_id || "",
    api_key: "",
    proxy_address: formatCubeProxy(
      state?.proxy_node_ip || "",
      state?.proxy_port || 80,
    ),
    sandbox_domain: state?.sandbox_domain || "cube.app",
  });
const bodyFrom = (form: Form, clearKey = false) => {
  const { proxy_address, ...normalized } = normalizeCubeAddresses(form);
  return {
    ...normalized,
    template_id: normalized.template_id.trim(),
    ...parseCubeProxy(proxy_address),
    api_key: clearKey ? "" : normalized.api_key.trim() || undefined,
  };
};

export default function CubeSandboxSection({
  state,
  onSaved,
}: {
  state?: SandboxStatus;
  onSaved: () => void;
}) {
  const confirm = useConfirm();
  const [connection, setConnection] = useState(state);
  const [form, setForm] = useState<Form>(() => formFrom(state));
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | "disconnect" | null>(null);
  const [feedback, setFeedback] = useState<{
    text: string;
    ok: boolean;
  } | null>(null);
  const [clearKey, setClearKey] = useState(false);
  const [templates, setTemplates] = useState<CubeTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [manualTemplate, setManualTemplate] = useState(false);
  const [templateRefresh, setTemplateRefresh] = useState(0);
  const configured = connection?.provider === "cube";
  const showForm = !configured || editing;
  const templateApiUrl = withHttpScheme(form.api_url);
  const templateApiKey = clearKey ? "" : form.api_key.trim() || undefined;

  useEffect(() => {
    setConnection(state);
  }, [state]);
  useEffect(() => {
    if (!editing) {
      setForm(formFrom(connection));
      setClearKey(false);
    }
  }, [
    connection?.api_url,
    connection?.template_id,
    connection?.proxy_node_ip,
    connection?.proxy_port,
    connection?.sandbox_domain,
    editing,
  ]);

  useEffect(() => {
    if (!showForm) return;
    setTemplates([]);
    setTemplatesError(null);
    if (!templateApiUrl) {
      setTemplatesLoading(false);
      return;
    }
    let active = true;
    const controller = new AbortController();
    setTemplatesLoading(true);
    const timer = setTimeout(() => {
      api
        .cubeTemplates(
          { api_url: templateApiUrl, api_key: templateApiKey },
          controller.signal,
        )
        .then((result) => {
          if (active) setTemplates(result.templates);
        })
        .catch((error) => {
          if (active) setTemplatesError(errText(error, "模板加载失败"));
        })
        .finally(() => {
          if (active) setTemplatesLoading(false);
        });
    }, 700);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [
    showForm,
    templateApiUrl,
    templateApiKey,
    connection?.api_key_configured,
    templateRefresh,
  ]);

  useEffect(() => {
    if (!configured || editing || busy) return;
    let active = true;
    const refresh = () => {
      if (document.visibilityState === "visible") {
        api
          .sandbox()
          .then((next) => {
            if (active) setConnection(next);
          })
          .catch(() => {});
      }
    };
    const timer = setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [configured, editing, busy]);

  const action = async (kind: "save" | "test") => {
    const values = showForm ? form : formFrom(connection);
    if (!values.api_url.trim() || !values.template_id.trim()) {
      setFeedback({
        text: "请填写 Cube 后端地址，并选择或填写模板",
        ok: false,
      });
      return;
    }
    setBusy(kind);
    setFeedback(null);
    setForm(normalizeCubeAddresses);
    try {
      if (kind === "test") {
        const result = await api.testCube(
          bodyFrom(values, showForm && clearKey),
        );
        if (result.sandbox) setConnection(result.sandbox);
        setFeedback({ text: result.message, ok: true });
      } else {
        const saved = await api.saveCube(bodyFrom(values, clearKey));
        setConnection(saved);
        setForm(formFrom(saved));
        setClearKey(false);
        setEditing(false);
        setFeedback({
          text:
            saved.verification === "passed"
              ? "连接已保存，验证结果已保留"
              : "连接已保存，点击「验证连接」检查远端环境",
          ok: true,
        });
        onSaved();
      }
    } catch (error) {
      setFeedback({ text: errText(error, "Cube 连接操作失败"), ok: false });
      if (kind === "test" && configured) {
        const latest = await api.sandbox().catch(() => null);
        if (latest) setConnection(latest);
      }
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    const ok = await confirm({
      title: "断开 Cube 后端？",
      description:
        "将释放当前账号的远端沙箱，保留已同步的群聊文件。请先完成或停止正在执行的任务。",
      confirmText: "断开并释放",
      danger: true,
    });
    if (!ok) return;
    setBusy("disconnect");
    setFeedback(null);
    try {
      await api.disconnectCube();
      setConnection(await api.sandbox());
      setEditing(false);
      onSaved();
      toast("已断开 Cube 后端", "ok");
    } catch (error) {
      setFeedback({ text: errText(error, "断开失败"), ok: false });
    } finally {
      setBusy(null);
    }
  };

  const edit = () => {
    setForm(formFrom(connection));
    setClearKey(false);
    setFeedback(null);
    setManualTemplate(false);
    setEditing(true);
  };
  const verification =
    connection?.verification ||
    (connection?.last_check_error
      ? "failed"
      : connection?.last_checked_at
        ? "passed"
        : "untested");
  const label = !connection?.available
    ? "已停用"
    : verification === "passed"
      ? "验证通过"
      : verification === "failed"
        ? "验证失败"
        : "已配置，待验证";
  const tone =
    !connection?.available || verification === "failed"
      ? "text-danger"
      : verification === "untested"
        ? "text-warn"
        : "text-ok";
  const StatusIcon =
    verification === "passed" && connection?.available
      ? CheckCircle2
      : CircleAlert;
  const sessions = connection?.sessions || [];
  const latest = sessions.find((session) => session.last_executed_at);

  const field = (
    key: keyof Form,
    label: string,
    placeholder: string,
    type = "text",
  ) => (
    <label className="block text-[11.5px] text-muted">
      <span className="mb-1.5 block">{label}</span>
      <input
        className="field"
        type={type}
        value={form[key]}
        placeholder={placeholder}
        disabled={!!busy}
        onBlur={() => {
          if (key === "api_url" || key === "proxy_address")
            setForm(normalizeCubeAddresses);
        }}
        onChange={(event) => {
          const value = event.target.value;
          const backendChanged =
            key === "api_url" && withHttpScheme(value) !== templateApiUrl;
          if (backendChanged) setManualTemplate(false);
          setForm((f) => ({
            ...f,
            [key]: value,
            ...(backendChanged ? { template_id: "" } : {}),
          }));
        }}
      />
    </label>
  );
  const detail = (label: string, value: string, mono = false) => (
    <div className="min-w-0">
      <dt className="text-[11px] text-faint">{label}</dt>
      <dd
        className={`mt-1 break-all text-[12px] leading-relaxed text-ink ${mono ? "font-mono text-[11.5px]" : ""}`}
      >
        {value}
      </dd>
    </div>
  );

  if (!connection)
    return (
      <div className="flex items-center gap-2 py-3 text-[12px] text-faint">
        <Loader2 size={14} className="animate-spin" />
        加载沙箱配置…
      </div>
    );

  return (
    <div className="space-y-4">
      {configured && !editing ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-panel2 text-muted">
              <Server size={19} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h3 className="text-[14px] font-medium text-ink">
                  CubeSandbox
                </h3>
                <span
                  role="status"
                  className={`inline-flex items-center gap-1.5 text-[11px] ${tone}`}
                >
                  <StatusIcon size={13} />
                  {label}
                </span>
              </div>
            </div>
            <button
              className="btn-ghost shrink-0"
              disabled={!!busy}
              onClick={edit}
            >
              <Pencil size={12} />
              编辑连接
            </button>
          </div>

          <dl className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            {detail("后端地址", connection.api_url || "", true)}
            {detail("模板 ID", connection.template_id || "", true)}
            {detail(
              "代理节点",
              connection.proxy_node_ip
                ? formatCubeProxy(
                    connection.proxy_node_ip,
                    connection.proxy_port || 80,
                  )
                : "使用沙箱域名解析",
              true,
            )}
            {detail(
              "API Key",
              connection.api_key_configured ? "已加密保存" : "未配置密钥",
            )}
          </dl>

          {(connection.last_check_error || connection.reason) && (
            <p className="break-words rounded-lg bg-danger/5 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
              {connection.last_check_error || connection.reason}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <button
              className="btn-ghost"
              disabled={!!busy || !connection.available}
              onClick={() => action("test")}
            >
              {busy === "test" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Plug size={13} />
              )}
              {busy === "test" ? "正在验证…" : "验证连接"}
            </button>
            <span className="text-[11px] text-faint">
              {connection.last_checked_at
                ? `上次验证 ${timeAgo(connection.last_checked_at)}`
                : "配置已保存，尚未验证可用性"}
            </span>
            <button
              className="btn-ghost ml-auto text-danger"
              disabled={!!busy}
              onClick={disconnect}
            >
              {busy === "disconnect" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Unplug size={13} />
              )}
              断开连接
            </button>
          </div>

          <div className="border-t border-line pt-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h4 className="text-[12px] font-medium text-muted">
                远端运行记录
              </h4>
              <span className="text-[11px] text-faint">
                {connection.session_count || 0} 个会话沙箱
                {latest
                  ? ` · 最近执行 ${timeAgo(latest.last_executed_at)}`
                  : ""}
              </span>
            </div>
            {sessions.length ? (
              <div className="mt-3 space-y-3">
                {sessions.map((session) => (
                  <div key={session.chat_id} className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[12px] text-ink">
                        {session.chat_name}
                      </p>
                      <p className="mt-0.5 break-all font-mono text-[10.5px] text-faint">
                        {session.sandbox_id}
                      </p>
                      <p className="mt-1 text-[11px] text-faint">
                        {session.last_executed_at
                          ? `实际执行 ${timeAgo(session.last_executed_at)} · 退出码 ${session.last_exit_code ?? "—"}`
                          : "沙箱已创建，尚无执行完成记录"}
                      </p>
                    </div>
                    <Link
                      className="inline-flex shrink-0 items-center gap-1 text-[11px] text-muted hover:text-ink"
                      href={`/chat?chat=${encodeURIComponent(session.chat_id)}`}
                    >
                      查看会话
                      <ArrowUpRight size={12} />
                    </Link>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-[11.5px] leading-relaxed text-faint">
                首次调用{" "}
                <code className="font-mono text-[11px]">code_exec</code> 或{" "}
                <code className="font-mono text-[11px]">run_command</code>{" "}
                时创建。普通聊天不会启动沙箱。
              </p>
            )}
            <p className="mt-3 text-[11px] leading-relaxed text-faint">
              代码和命令在 Cube 中运行。请在
              <Link
                href="/bots"
                className="mx-1 text-muted underline underline-offset-2 hover:text-ink"
              >
                智能体工具设置
              </Link>
              中启用对应工具。
            </p>
          </div>
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <span className="chip">
              {editing
                ? "编辑 Cube 连接"
                : connection.local_test
                  ? "Docker 本机测试"
                  : "未接入"}
            </span>
            <a
              className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted hover:text-ink"
              href="https://cubesandbox.com/zh/guide/quickstart"
              target="_blank"
              rel="noreferrer"
            >
              部署文档
              <ExternalLink size={12} />
            </a>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {field("api_url", "Cube 后端地址", "http://你的服务器:3000")}
            {field(
              "api_key",
              "API Key（后端开启鉴权时填写）",
              connection.api_key_configured ? "已保存，留空保留原密钥" : "可选",
              "password",
            )}
            {field(
              "proxy_address",
              "代理地址（可选）",
              "例如 http://203.0.113.10:18080",
            )}
            {field("sandbox_domain", "沙箱域名", "cube.app")}
            <div className="sm:col-span-2">
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[11.5px] text-muted">
                <label htmlFor={manualTemplate ? "cube-template" : undefined}>
                  沙箱模板
                </label>
                <div className="flex items-center gap-3">
                  <button
                    className="inline-flex items-center gap-1 hover:text-ink"
                    disabled={!!busy || templatesLoading || !templateApiUrl}
                    onClick={() => setTemplateRefresh((value) => value + 1)}
                  >
                    <RefreshCw
                      size={11}
                      className={templatesLoading ? "animate-spin" : ""}
                    />
                    刷新模板
                  </button>
                  <button
                    className="hover:text-ink"
                    disabled={!!busy}
                    onClick={() => setManualTemplate((value) => !value)}
                  >
                    {manualTemplate ? "从列表选择" : "手动填写 ID"}
                  </button>
                </div>
              </div>
              {manualTemplate ? (
                <input
                  id="cube-template"
                  className="field"
                  value={form.template_id}
                  disabled={!!busy}
                  placeholder="已部署的模板 ID 或别名"
                  onChange={(event) =>
                    setForm((f) => ({ ...f, template_id: event.target.value }))
                  }
                />
              ) : (
                <Select
                  ariaLabel="沙箱模板"
                  value={form.template_id}
                  disabled={
                    !!busy ||
                    templatesLoading ||
                    (!templates.length && !form.template_id)
                  }
                  onChange={(value) =>
                    setForm((f) => ({ ...f, template_id: value }))
                  }
                  placeholder={
                    templatesLoading ? "正在检测模板…" : "请选择沙箱模板"
                  }
                  searchable
                  options={[
                    ...(form.template_id &&
                    !templates.some(
                      (template) => template.id === form.template_id,
                    )
                      ? [
                          {
                            value: form.template_id,
                            label: `当前模板：${form.template_id}`,
                            description: "未在当前列表中找到",
                          },
                        ]
                      : []),
                    ...templates.map((template) => ({
                      value: template.id,
                      label: template.name || template.id,
                      description: [
                        template.name ? template.id : "",
                        template.status,
                      ]
                        .filter(Boolean)
                        .join(" · "),
                    })),
                  ]}
                />
              )}
              <p
                role="status"
                className={`mt-1.5 text-[11px] leading-relaxed ${templatesError ? "text-danger" : "text-faint"}`}
              >
                {!templateApiUrl
                  ? "填写后端地址后自动检测模板；后端开启鉴权时需填写密钥。"
                  : templatesLoading
                    ? "正在从 Cube 后端加载模板…"
                    : templatesError
                      ? `${templatesError}，可刷新重试或手动填写 ID。`
                      : templates.length
                        ? `检测到 ${templates.length} 个模板，选择后可测试连接。`
                        : "后端暂无模板，请先部署模板，再刷新列表。"}
              </p>
            </div>
          </div>
          <p className="text-[11px] leading-relaxed text-faint">
            代理地址保留协议和端口；省略协议时补上 http://，省略端口时使用
            80，留空使用沙箱域名解析。
          </p>
          {connection.api_key_configured && (
            <label className="flex items-center gap-2 text-[11px] text-muted">
              <input
                type="checkbox"
                checked={clearKey}
                disabled={!!busy}
                onChange={(event) => setClearKey(event.target.checked)}
              />
              移除已保存的 API Key
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              className="btn-primary"
              disabled={!!busy}
              onClick={() => action("save")}
            >
              {busy === "save" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Save size={13} />
              )}
              保存连接
            </button>
            <button
              className="btn-ghost"
              disabled={!!busy}
              onClick={() => action("test")}
            >
              {busy === "test" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Plug size={13} />
              )}
              测试连接
            </button>
            {editing && (
              <button
                className="btn-ghost"
                disabled={!!busy}
                onClick={() => {
                  setEditing(false);
                  setFeedback(null);
                }}
              >
                取消编辑
              </button>
            )}
          </div>
          <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-faint">
            <ShieldCheck size={13} className="mt-0.5 shrink-0" />
            配置仅供当前账号使用。测试会创建并释放临时沙箱，Docker
            仅用于本机测试。
          </p>
        </>
      )}
      {feedback && (
        <p
          role="status"
          className={`break-words rounded-lg border px-3 py-2 text-[11.5px] leading-relaxed ${feedback.ok ? "border-ok/25 bg-ok/5 text-ok" : "border-danger/25 bg-danger/5 text-danger"}`}
        >
          {feedback.text}
        </p>
      )}
    </div>
  );
}
