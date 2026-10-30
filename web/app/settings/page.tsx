"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Loader2,
  Plus,
  Save,
  Trash2,
  Zap,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import AvatarPicker from "@/components/ui/AvatarPicker";
import Select from "@/components/ui/Select";
import { useConfirm } from "@/components/ui/Confirm";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { capLabel, modelCapability } from "@/lib/fmt";
import type { Me, Provider, Vendor } from "@/lib/types";
import { toast } from "@/lib/ui";

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [settings, setSettings] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Vendor | null>(null);
  const [pricing, setPricing] = useState<Provider | null>(null);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [probing, setProbing] = useState<string | null>(null);
  const [vendorId, setVendorId] = useState("");
  const confirm = useConfirm();
  const [pwd, setPwd] = useState({ current: "", next: "" });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [del, setDel] = useState({ password: "", email: "" });
  const [deleting, setDeleting] = useState(false);
  const [notifyPerm, setNotifyPerm] = useState("default");

  const load = () => {
    api.me().then(setMe).catch(() => {});
    api.providers().then(setProviders).catch(() => {}).finally(() => setProvidersLoading(false));
    api.settings().then(setSettings).catch(() => {});
  };

  useEffect(() => {
    load();
    api.vendors().then(setVendors).catch(() => {});
    if (typeof Notification !== "undefined") setNotifyPerm(Notification.permission);
  }, []);

  const saveSettings = async (patch: any, msg = "已保存") => {
    setBusy(true);
    try {
      const s = await api.patchSettings(patch);
      setSettings(s);
      toast(msg, "ok");
      return true;
    } catch (e: any) {
      toast(e.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submitDelete = async () => {
    if (!del.password) return toast("请输入当前密码", "error");
    if (del.email.trim().toLowerCase() !== (me?.email || "").toLowerCase()) {
      return toast("确认邮箱与当前账号不一致", "error");
    }
    setDeleting(true);
    try {
      await api.deleteAccount(del.password, del.email.trim());
      toast("账号已删除，正在退出", "ok");
      window.location.href = "/login";
    } catch (e: any) {
      toast(e.message, "error");
      setDeleting(false);
    }
  };

  const usedVendors = useMemo(
    () => new Set(providers.map((p) => p.vendor_id).filter(Boolean) as string[]),
    [providers]
  );

  const vendorOptions = useMemo(
    () =>
      vendors.map((v) => ({
        value: v.id,
        label: v.name,
        description: `${v.region === "cn" ? "国内" : v.region === "intl" ? "海外" : ""}${
          v.base_url ? ` · ${v.base_url.replace("https://", "")}` : " · 需自己填 base_url"
        }`,
        avatar: v.logo,
        color: v.color,
      })),
    [vendors]
  );

  const selectedVendor = vendors.find((v) => v.id === vendorId) || null;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-8">
          <h1 className="text-[20px] font-semibold tracking-tight">设置</h1>
          <p className="mt-1 text-[12.5px] text-muted">
            模型费用由你自带的 Key 承担，平台不提供统一额度。
          </p>

          {/* 模型厂商 */}
          <Section
            title="模型厂商"
            desc="选一个厂商、填 API Key 即可。base_url 已预置，模型列表会自动获取；具体用哪个模型在 Bot 里选。"
          >
            <div className="flex flex-col gap-2 sm:flex-row">
              <Select
                className="flex-1"
                value={vendorId}
                onChange={setVendorId}
                options={vendorOptions}
                placeholder="选择模型厂商"
                searchable
              />
              <button
                className="btn-primary shrink-0"
                disabled={!selectedVendor}
                onClick={() => setPicked(selectedVendor)}
              >
                <Plus size={13} /> 添加
              </button>
            </div>
            {selectedVendor && (
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-faint">
                <span>{selectedVendor.note}</span>
                {selectedVendor.base_url && (
                  <span className="font-mono text-[10.5px] text-muted">
                    {selectedVendor.base_url}
                  </span>
                )}
                {usedVendors.has(selectedVendor.id) && (
                  <span className="chip border-ok/40 text-ok">已添加过</span>
                )}
              </div>
            )}
          </Section>

          {/* 已添加 */}
          <Section
            title="已添加的厂商"
            desc="「刷新模型」重新获取厂商的模型列表；「测试」检测首个模型；「检测能力」批量检测流式与工具调用，结果会用于 Bot 与群聊的选模型提示。"
          >
            <div className="space-y-2">
              {providers.map((p) => (
                <div key={p.id} className="rounded-lg border border-line px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <Avatar value={p.logo} color={p.color} size={24} alt={p.vendor_name} />
                    <span className="text-[12.5px] font-medium">{p.name || p.vendor_name}</span>
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        p.status === "ok"
                          ? "bg-ok"
                          : p.status === "error"
                            ? "bg-danger"
                            : "bg-faint"
                      }`}
                    />
                    {p.kind === "mock" && <span className="chip">演示</span>}
                    <span className="text-[10.5px] text-faint">
                      {p.models.length} 个模型
                      {p.api_key_last4 && ` · ****${p.api_key_last4}`}
                    </span>
                    {p.capabilities && p.capabilities.known > 0 && (
                      <span
                        className={`text-[10.5px] ${
                          p.capabilities.no_tool_calling || p.capabilities.no_streaming
                            ? "text-warn"
                            : "text-faint"
                        }`}
                        title="能力取决于具体模型；未检测的模型不拦截，但可能执行失败"
                      >
                        {p.capabilities.known}/{p.capabilities.total} 已检测
                        {p.capabilities.no_tool_calling > 0 &&
                          ` · ${p.capabilities.no_tool_calling} 个不支持工具调用`}
                        {p.capabilities.no_streaming > 0 &&
                          ` · ${p.capabilities.no_streaming} 个不支持流式`}
                      </span>
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      {p.kind !== "mock" && (
                        <button
                          className="text-[11.5px] text-muted hover:text-ink"
                          onClick={() => setEditingProvider(p)}
                          title="修改名称、base_url 或重新输入 API Key"
                        >
                          编辑
                        </button>
                      )}
                      <button
                        className="text-[11.5px] text-muted hover:text-ink"
                        onClick={() => setPricing(p)}
                        title="填写模型单价，用于用量页估算费用"
                      >
                        价格
                      </button>
                      <button
                        className="text-[11.5px] text-muted hover:text-ink"
                        onClick={async () => {
                          try {
                            const r = await api.refreshProvider(p.id);
                            toast(r.message, r.detected ? "ok" : "error");
                            load();
                          } catch (e: any) {
                            toast(e.message, "error");
                          }
                        }}
                      >
                        刷新模型
                      </button>
                      {p.kind !== "mock" && (
                        <button
                          className="text-[11.5px] text-muted hover:text-ink disabled:opacity-50"
                          disabled={probing === p.id}
                          title="批量检测流式输出与工具调用能力"
                          onClick={async () => {
                            setProbing(p.id);
                            try {
                              const r = await api.probeProvider(p.id);
                              toast(r.message, "ok");
                              load();
                            } catch (e: any) {
                              toast(e.message, "error");
                            } finally {
                              setProbing(null);
                            }
                          }}
                        >
                          {probing === p.id ? "检测中…" : "检测能力"}
                        </button>
                      )}
                      <button
                        className="text-[11.5px] text-muted hover:text-ink"
                        onClick={async () => {
                          try {
                            const r = await api.testProvider(p.id);
                            const caps =
                              r.ok && r.model
                                ? ` · ${capLabel(r.streaming, "流式")} · ${capLabel(
                                    r.tool_calling,
                                    "工具调用"
                                  )}`
                                : "";
                            toast(r.ok ? `「${r.model}」连接正常${caps}` : r.message, r.ok ? "ok" : "error");
                            load();
                          } catch (e: any) {
                            toast(e.message, "error");
                          }
                        }}
                      >
                        测试
                      </button>
                      <button
                        className="text-faint hover:text-danger"
                        onClick={async () => {
                          const ok = await confirm({
                            title: `删除「${p.vendor_name}」？`,
                            description: "使用该厂商的 Bot 会被置为不可用，需要重新选择模型。",
                            confirmText: "删除",
                            danger: true,
                          });
                          if (!ok) return;
                          try {
                            await api.deleteProvider(p.id, true);
                            toast("已删除", "ok");
                            load();
                          } catch (e: any) {
                            toast(e.message, "error");
                          }
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              {providersLoading && !providers.length && (
                <div className="flex justify-center rounded-lg border border-dashed border-line px-3 py-6">
                  <Loader2 size={14} className="animate-spin text-faint" />
                </div>
              )}
              {!providersLoading && !providers.length && (
                <div className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-[12px] text-faint">
                  还没有添加厂商。至少添加一个后才能发起执行。
                </div>
              )}
            </div>
          </Section>

          {/* 默认限额 */}
          <Section title="默认限额" desc="设置单次任务的 token 预算与审批有效期；群聊可单独覆盖 token 预算。">
            {settings && (
              <div className="grid gap-5 sm:grid-cols-2 sm:gap-8">
                <NumField
                  label="token 预算"
                  value={settings.token_budget}
                  onSave={(v) => saveSettings({ token_budget: v })}
                  allowUnlimited
                  disabled={busy}
                />
                <NumField
                  label="审批/提问有效期（小时）"
                  value={settings.approval_ttl_hours ?? 24}
                  onSave={(v) => saveSettings({ approval_ttl_hours: v })}
                />
              </div>
            )}
          </Section>

          {/* 搜索 */}
          <Section
            title="网页搜索"
            desc="未配置时使用免密钥的公共搜索，稳定性较差。推荐填入 Tavily 等搜索服务的 API Key。"
          >
            <input
              className="field"
              type="password"
              placeholder={
                settings?.search_configured ? "已配置，输入新 Key 可替换" : "搜索服务 API Key"
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  saveSettings({ search_api_key: (e.target as HTMLInputElement).value }, "已保存");
                  (e.target as HTMLInputElement).value = "";
                }
              }}
            />
            <p className="mt-1.5 text-[10.5px] text-faint">回车保存。留空并保存可清除。</p>
          </Section>

          {/* 通知 */}
          <Section
            title="浏览器通知"
            desc="只在页面打开或处于后台标签时生效；被拒绝时审批仍会显示在页面内通知中心。想恢复授权，请点地址栏左侧的站点图标 → 通知 → 允许，然后刷新页面。"
          >
            <div className="flex flex-wrap items-center gap-3">
              <button
                className="btn-ghost"
                onClick={async () => {
                  if (typeof Notification === "undefined") return toast("当前浏览器不支持", "error");
                  const p = await Notification.requestPermission();
                  setNotifyPerm(p);
                  toast(
                    p === "granted" ? "已开启" : "已拒绝，将回退到页面内通知中心",
                    p === "granted" ? "ok" : "info"
                  );
                }}
              >
                {notifyPerm === "granted" ? <Check size={13} /> : <Zap size={13} />}
                {notifyPerm === "granted" ? "已授权" : "开启通知"}
              </button>
              <label className="flex cursor-pointer items-center gap-2 text-[12px] text-muted">
                <input
                  type="checkbox"
                  className="accent-[var(--accent)]"
                  checked={settings?.notifications_enabled ?? true}
                  onChange={(e) => saveSettings({ notifications_enabled: e.target.checked })}
                />
                允许系统通知（浏览器弹窗）
              </label>
              <span className="text-[10.5px] text-faint">页面内通知中心不受此开关影响</span>
            </div>
          </Section>

          {/* 账号 */}
          <Section title="账号" desc={me?.email}>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <input
                className="field"
                type="password"
                placeholder="当前密码"
                value={pwd.current}
                onChange={(e) => setPwd({ ...pwd, current: e.target.value })}
              />
              <input
                className="field"
                type="password"
                placeholder="新密码（至少 8 位）"
                value={pwd.next}
                onChange={(e) => setPwd({ ...pwd, next: e.target.value })}
              />
              <button
                className="btn-soft"
                onClick={async () => {
                  if (pwd.next.length < 8) return toast("新密码至少 8 位", "error");
                  try {
                    await api.changePassword(pwd.current, pwd.next);
                    toast("密码已更新", "ok");
                    setPwd({ current: "", next: "" });
                  } catch (e: any) {
                    toast(e.message, "error");
                  }
                }}
              >
                修改密码
              </button>
            </div>
            <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
              <span className="text-[10.5px] text-faint">退出后需要重新登录才能继续使用</span>
              <button
                className="btn-ghost"
                onClick={async () => {
                  await api.logout().catch(() => {});
                  window.location.href = "/login";
                }}
              >
                退出登录
              </button>
            </div>
          </Section>

          {/* ACC-07 删除账号 */}
          <Section
            title="危险操作"
            desc="删除账号会立即退出登录并停用账号，数据保留 30 天，期间可联系管理员恢复；到期后彻底清除。"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[11.5px] text-muted">
                到期后删除全部群聊、Bot、记忆、文件、模型接入与调用日志，无法恢复。
              </span>
              <button className="btn-danger" onClick={() => setDeleteOpen(true)}>
                删除账号
              </button>
            </div>
          </Section>

          <p className="mt-8 text-[10.5px] leading-relaxed text-faint">
            Botly v1.0 · 数据与密钥按账号隔离 · 调用日志保留 30 天
          </p>
        </div>
      </div>

      <Modal
        open={deleteOpen}
        onClose={() => !deleting && setDeleteOpen(false)}
        title="删除账号"
        subtitle="账号会立即停用并退出登录，数据保留 30 天；到期后彻底清除，无法恢复。"
        footer={
          <>
            <button className="btn-ghost" disabled={deleting} onClick={() => setDeleteOpen(false)}>
              取消
            </button>
            <button className="btn-danger" disabled={deleting} onClick={submitDelete}>
              {deleting ? "删除中…" : "确认删除"}
            </button>
          </>
        }
      >
        <p className="text-[11.5px] leading-relaxed text-muted">
          删除后无法登录，正在进行的执行会立即停止。30 天内可联系管理员恢复；
          超过保留期后，群聊、Bot、记忆、文件与模型接入都会从磁盘和数据库彻底清除。
        </p>
        <label className="label mt-4">当前密码</label>
        <input
          className="field"
          type="password"
          autoComplete="current-password"
          placeholder="输入当前密码"
          value={del.password}
          onChange={(e) => setDel({ ...del, password: e.target.value })}
        />
        <label className="label mt-3">确认邮箱</label>
        <input
          className="field"
          placeholder={me?.email || "输入当前账号邮箱"}
          value={del.email}
          onChange={(e) => setDel({ ...del, email: e.target.value })}
        />
        <p className="mt-2 text-[10.5px] text-faint">请输入 {me?.email} 完整邮箱以确认。</p>
      </Modal>

      {picked && (
        <VendorDialog
          vendor={picked}
          onClose={() => setPicked(null)}
          onDone={() => {
            setPicked(null);
            load();
          }}
        />
      )}

      {pricing && (
        <PriceDialog
          provider={pricing}
          onClose={() => setPricing(null)}
          onDone={() => {
            setPricing(null);
            load();
          }}
        />
      )}

      {editingProvider && (
        <ProviderEditDialog
          provider={editingProvider}
          onClose={() => setEditingProvider(null)}
          onDone={() => {
            setEditingProvider(null);
            load();
          }}
        />
      )}
    </AppShell>
  );
}

/* ------------------------------------------------------------------ */
/* 模型单价：用于用量页的费用估算                                        */
/* ------------------------------------------------------------------ */
const toPrice = (v: string): number | null => {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : null;
};


function PriceDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: Provider;
  onClose: () => void;
  onDone: () => void;
}) {
  const [rows, setRows] = useState(
    provider.models.map((m) => ({
      name: m.name,
      input: m.input_price === null || m.input_price === undefined ? "" : String(m.input_price),
      output: m.output_price === null || m.output_price === undefined ? "" : String(m.output_price),
      streaming: m.streaming ?? null,
      tool_calling: m.tool_calling ?? null,
    }))
  );
  const [q, setQ] = useState("");
  const [saving, setSaving] = useState(false);

  const shown = rows
    .map((r, i) => ({ ...r, i }))
    .filter((r) => r.name.toLowerCase().includes(q.trim().toLowerCase()));

  const save = async () => {
    setSaving(true);
    try {
      await api.patchProvider(provider.id, {
        models: rows.map((r) => ({
          name: r.name,
          input_price: toPrice(r.input),
          output_price: toPrice(r.output),
        })),
      });
      toast("单价已保存", "ok");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`${provider.vendor_name} 的模型单价`}
      subtitle="USD / 百万 token；新模型会自动填入厂商参考价，可自行修改，留空表示不参与计费估算"
      width="max-w-xl"
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存
          </button>
        </>
      }
    >
      {rows.length > 8 && (
        <input
          className="field mb-2"
          placeholder="搜索模型"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      )}
      <div className="grid grid-cols-[1fr_78px_78px] gap-2 border-b border-line px-1 pb-1.5 text-[10.5px] text-faint">
        <span>模型</span>
        <span className="text-right">输入 $/M</span>
        <span className="text-right">输出 $/M</span>
      </div>
      <div className="max-h-[46vh] overflow-y-auto">
        {shown.map((r) => (
          <div
            key={r.name}
            className="grid grid-cols-[1fr_78px_78px] items-center gap-2 border-b border-line px-1 py-1.5 last:border-0"
          >
            <span className="min-w-0">
              <span className="block truncate font-mono text-[11.5px] text-muted" title={r.name}>
                {r.name}
              </span>
              <span
                className={`block truncate text-[10px] ${
                  modelCapability(r).bad ? "text-warn" : "text-faint"
                }`}
                title="在厂商条目上点「检测能力」可更新"
              >
                {modelCapability(r).text}
              </span>
            </span>
            <input
              className="field px-1.5 py-1 text-right text-[11.5px]"
              inputMode="decimal"
              placeholder="—"
              value={r.input}
              onChange={(e) => {
                const next = [...rows];
                next[r.i] = { ...next[r.i], input: e.target.value };
                setRows(next);
              }}
            />
            <input
              className="field px-1.5 py-1 text-right text-[11.5px]"
              inputMode="decimal"
              placeholder="—"
              value={r.output}
              onChange={(e) => {
                const next = [...rows];
                next[r.i] = { ...next[r.i], output: e.target.value };
                setRows(next);
              }}
            />
          </div>
        ))}
        {!shown.length && (
          <div className="py-8 text-center text-[11.5px] text-faint">没有匹配的模型</div>
        )}
      </div>
    </Modal>
  );
}

function ProviderEditDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: Provider;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim()) return toast("名称不能为空", "error");
    if (!baseUrl.trim()) return toast("base_url 不能为空", "error");
    setSaving(true);
    try {
      const changedBase = baseUrl.trim() !== provider.base_url;
      await api.patchProvider(provider.id, {
        name: name.trim(),
        base_url: baseUrl.trim(),
        ...(key.trim() ? { api_key: key.trim() } : {}),
      });
      toast("已保存", "ok");
      onDone();
      // base_url 或 Key 变了，后台重新探测模型；失败只提示，不影响已保存
      if (changedBase || key.trim()) {
        api
          .refreshProvider(provider.id)
          .then((r) => toast(r.message, r.detected ? "ok" : "error"))
          .catch((e) => toast(e.message, "error"));
      }
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`编辑「${provider.name}」`}
      subtitle="Key 留空表示不修改；保存后只显示末 4 位"
      width="max-w-lg"
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={save} disabled={saving}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[12px] text-muted">名称</span>
          <input className="field" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] text-muted">base_url</span>
          <input
            className="field font-mono text-[12px]"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[12px] text-muted">
            API Key（当前 ****{provider.api_key_last4 || "无"}）
          </span>
          <input
            className="field"
            type="password"
            placeholder="留空则保持不变"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </label>
      </div>
    </Modal>
  );
}

function VendorDialog({
  vendor,
  onClose,
  onDone,
}: {
  vendor: Vendor;
  onClose: () => void;
  onDone: () => void;
}) {
  const custom = vendor.id === "custom";
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(vendor.base_url);
  const [models, setModels] = useState("");
  const [logo, setLogo] = useState(vendor.logo);
  const [advanced, setAdvanced] = useState(custom);
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    try {
      const body: any = { vendor_id: vendor.id, api_key: key || undefined };
      if (logo && logo !== vendor.logo) body.logo = logo;
      if (advanced && baseUrl) body.base_url = baseUrl.trim();
      if (models.trim()) {
        body.models = models
          .split(/[,，\n]/)
          .map((s) => s.trim())
          .filter(Boolean)
          .map((name) => ({ name }));
      }
      const r = await api.createProvider(body);
      toast(r.message, r.detected ? "ok" : "info");
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`添加 ${vendor.name}`}
      subtitle={custom ? "填写兼容 OpenAI 的 base_url 与模型名" : "只需填写 API Key，其余自动完成"}
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={submit} disabled={loading}>
            {loading ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} 添加
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="label">厂商标识</label>
          <AvatarPicker value={logo} onChange={setLogo} />
        </div>

        <div>
          <label className="label">
            API Key
            {custom && <span className="ml-1 font-normal text-faint">（可选）</span>}
          </label>
          <input
            autoFocus={!custom}
            className="field font-mono"
            type="password"
            placeholder={vendor.key_hint}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {custom && (
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
              自建网关接口需要鉴权就填；不需要可留空。
            </p>
          )}
          {vendor.requires_endpoint && (
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-warn">{vendor.note}</p>
          )}
        </div>

        <button
          onClick={() => setAdvanced(!advanced)}
          className="flex items-center gap-1 text-[11.5px] text-muted hover:text-ink"
        >
          <ChevronDown
            size={12}
            className={`transition-transform ${advanced ? "rotate-180" : ""}`}
          />
          高级选项
        </button>

        {advanced && (
          <div className="space-y-3 rounded-lg border border-line bg-panel2/40 p-3">
            <div>
              <label className="label">base_url</label>
              <input
                className="field font-mono text-[12px]"
                placeholder="https://api.example.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
              <p className="mt-1 text-[10.5px] text-faint">
                一般不用改。自建网关或代理时填自己的地址。
              </p>
            </div>
            <div>
              <label className="label">模型名（可选，逗号分隔）</label>
              <input
                className="field font-mono text-[12px]"
                placeholder="探测失败时可手动填写"
                value={models}
                onChange={(e) => setModels(e.target.value)}
              />
            </div>
          </div>
        )}

        <p className="text-[10.5px] leading-relaxed text-faint">
          保存时会调用 <code className="text-muted">GET {baseUrl || "base_url"}/models</code>{" "}
          自动获取可用模型，并对首个模型真实探测流式输出与工具调用能力；拿不到模型列表就用预置列表，不影响保存。
        </p>
      </div>
    </Modal>
  );
}

function Section({
  title,
  desc,
  action,
  children,
}: {
  title: string;
  desc?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-7">
      <div className="mb-2.5 flex items-end gap-3">
        <div>
          <h2 className="text-[13.5px] font-medium">{title}</h2>
          {desc && <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted">{desc}</p>}
        </div>
        <div className="ml-auto">{action}</div>
      </div>
      <div className="card p-3.5">{children}</div>
    </section>
  );
}

function NumField({
  label,
  value,
  onSave,
  allowUnlimited = false,
  disabled = false,
}: {
  label: string;
  value: number;
  onSave: (v: number) => void | Promise<boolean | void>;
  allowUnlimited?: boolean;
  disabled?: boolean;
}) {
  const [v, setV] = useState(allowUnlimited && value === 0 ? 300000 : value);
  const [unlimited, setUnlimited] = useState(allowUnlimited && value === 0);
  useEffect(() => {
    setUnlimited(allowUnlimited && value === 0);
    if (!allowUnlimited || value > 0) setV(value);
  }, [value, allowUnlimited]);
  return (
    <div className="min-w-0">
      <div className="mb-2 flex min-h-5 items-center justify-between gap-3">
        <label className="text-[12px] font-medium text-muted">{label}</label>
        {allowUnlimited && (
          <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11.5px] text-muted">
            <input type="checkbox" checked={unlimited} disabled={disabled}
              className="h-3.5 w-3.5 accent-[var(--accent)]"
              onChange={async (e) => {
                const next = e.target.checked;
                setUnlimited(next);
                const saved = await onSave(next ? 0 : (v >= 1000 ? v : 300000));
                if (saved === false) setUnlimited(!next);
              }} />
            无限制
          </label>
        )}
      </div>
      {unlimited ? (
        <div className="flex h-10 items-center rounded-lg border border-dashed border-line px-3 text-[12px] text-faint">
          不限制 token 用量
        </div>
      ) : (
        <div className="flex gap-2">
          <input
            className="field h-10 min-w-0 flex-1"
            type="number"
            aria-label={label}
            min={allowUnlimited ? 1000 : undefined}
            disabled={disabled}
            value={v}
            onChange={(e) => setV(Number(e.target.value))}
          />
          <button className="btn-ghost h-10 w-10 shrink-0 p-0" aria-label={`保存${label}`} onClick={() => onSave(v)}
            disabled={disabled || v === value || (allowUnlimited && (!Number.isInteger(v) || v < 1000))}>
            <Save size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
