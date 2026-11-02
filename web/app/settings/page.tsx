"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Loader2,
  Mail,
  MailCheck,
  Plus,
  Save,
  Trash2,
  Zap,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import AvatarPicker from "@/components/ui/AvatarPicker";
import { GithubMark, GoogleMark } from "@/components/ui/BrandIcons";
import Select from "@/components/ui/Select";
import { useConfirm } from "@/components/ui/Confirm";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import {
  isIOS,
  pushState as getPushState,
  subscribePush,
  unsubscribePush,
  type PushSupport,
} from "@/lib/push";
import { capLabel, modelCapability, timeAgo } from "@/lib/fmt";
import type { Bot, Me, Provider, Vendor } from "@/lib/types";
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
  // 每个厂商同一时间只允许一个动作（刷新/测试/检测），按钮显示进行中文案
  const [providerBusy, setProviderBusy] = useState<{ id: string; action: string } | null>(null);
  // 检测结果明细：批量检测最多 12 个模型，只在 toast 里报个数字看不出哪些不可用
  const [probeResult, setProbeResult] = useState<{
    provider: string;
    message: string;
    results: {
      model: string;
      ok: boolean;
      message?: string;
      streaming?: boolean | null;
      tool_calling?: boolean | null;
    }[];
  } | null>(null);
  // MOD-04：删除厂商前先看受影响的 Bot，二选一（去改绑 / 确认置为不可用）
  const [deleteTarget, setDeleteTarget] = useState<Provider | null>(null);
  const [deleteAffected, setDeleteAffected] = useState<Bot[]>([]);
  const [deleteBusy, setDeleteBusy] = useState(false);
  // ACC-06：注册/首次登录后的引导（未配置厂商时提示先配模型）
  const [welcome, setWelcome] = useState(false);
  const [vendorId, setVendorId] = useState("");
  const confirm = useConfirm();
  const [pwd, setPwd] = useState({ current: "", next: "" });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [del, setDel] = useState({ password: "", email: "" });
  const [deleting, setDeleting] = useState(false);
  const [notifyPerm, setNotifyPerm] = useState("default");
  const [sendingVerify, setSendingVerify] = useState(false);
  const [pushSt, setPushSt] = useState<PushSupport | null>(null);
  const [pushBusy, setPushBusy] = useState(false);
  // 第三方登录绑定状态：available 是「后端配了这个 provider」，linked 是「当前账号已绑」
  const [oauth, setOauth] = useState<{
    available: { google: boolean; github: boolean };
    redirect_uri: Record<string, string>;
    linked: Record<string, { name?: string | null; email?: string | null }>;
  } | null>(null);

  // ACC-09：解绑第三方登录；解绑后只能用邮箱密码登录
  const unbindOauth = async (p: "google" | "github") => {
    const label = p === "google" ? "Google" : "GitHub";
    const ok = await confirm({
      title: `解除 ${label} 绑定？`,
      description: "解绑后需要用邮箱密码登录。忘记密码可在登录页点「忘记密码」重置。",
      confirmText: "解除绑定",
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await api.unbindOauth(p);
      setOauth((prev) =>
        prev
          ? {
              ...prev,
              linked: Object.fromEntries(
                Object.entries(prev.linked).filter(([k]) => k !== p)
              ),
            }
          : prev
      );
      toast(`已解除 ${label} 绑定`, "ok");
      if (r.remaining === 0) {
        toast("已没有第三方登录方式，请确认邮箱密码可以登录", "info");
      }
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const copyText = (text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => toast("已复制", "ok"),
      () => toast("复制失败，请手动选中", "error")
    );
  };

  // ACC-08：发一封验证邮件；本地没配 SMTP 时后端会回 dev_link，直接复制给用户
  const sendVerify = async () => {
    setSendingVerify(true);
    try {
      const r = await api.sendVerification();
      if (r.dev_link) {
        await navigator.clipboard?.writeText(r.dev_link).catch(() => {});
        toast("开发模式：验证链接已复制，粘贴到浏览器打开", "ok");
      } else {
        toast(`验证邮件已发送到 ${r.email}`, "ok");
      }
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSendingVerify(false);
    }
  };

  const load = () => {
    api.me().then(setMe).catch(() => {});
    api.providers().then(setProviders).catch(() => {}).finally(() => setProvidersLoading(false));
    api.settings().then(setSettings).catch(() => {});
  };

  useEffect(() => {
    load();
    api.vendors().then(setVendors).catch(() => {});
    api.oauthAccounts().then(setOauth).catch(() => {});
    if (typeof Notification !== "undefined") setNotifyPerm(Notification.permission);
    getPushState().then(setPushSt).catch(() => {});
    // ACC-06：注册 / 首次登录会带 ?welcome=1
    if (typeof window !== "undefined") {
      const q = new URLSearchParams(window.location.search);
      setWelcome(q.get("welcome") === "1");
      // 第三方登录绑定回来
      const bound = q.get("oauth_bound");
      if (bound) toast(`已绑定 ${bound === "google" ? "Google" : "GitHub"} 账号`, "ok");
      const oauthErr = q.get("oauth_error");
      if (oauthErr) toast(oauthErr, "error");
    }
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

  // NTF-06：Web Push（页面关闭后由 Service Worker 接收推送）
  const enablePush = async () => {
    setPushBusy(true);
    try {
      await subscribePush();
      setPushSt("subscribed");
      toast("已开启 Web Push，关闭页面后也能收到提醒", "ok");
    } catch (e: any) {
      toast(e.message, "error");
      getPushState().then(setPushSt).catch(() => {});
    } finally {
      setPushBusy(false);
    }
  };

  const disablePush = async () => {
    setPushBusy(true);
    try {
      await unsubscribePush();
      setPushSt("idle");
      toast("已关闭 Web Push", "info");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setPushBusy(false);
    }
  };

  const testPush = async () => {
    try {
      await api.pushTest();
      toast("测试通知已发送：页面全部关闭时才会弹系统通知", "ok");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  // 单个厂商的耗时操作：同一时间只跑一个，按钮上显示进行中文案
  const withProviderBusy = async (p: Provider, action: string, fn: () => Promise<void>) => {
    if (providerBusy) return;
    setProviderBusy({ id: p.id, action });
    try {
      await fn();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setProviderBusy(null);
    }
  };

  const refreshProvider = (p: Provider) =>
    withProviderBusy(p, "refresh", async () => {
      const r = await api.refreshProvider(p.id);
      toast(r.message, r.detected ? "ok" : "error");
      load();
    });

  const testProvider = (p: Provider) =>
    withProviderBusy(p, "test", async () => {
      const r = await api.testProvider(p.id);
      const caps =
        r.ok && r.model
          ? ` · ${capLabel(r.streaming, "流式")} · ${capLabel(r.tool_calling, "工具调用")}`
          : "";
      toast(r.ok ? `「${r.model}」连接正常${caps}` : r.message, r.ok ? "ok" : "error");
      load();
    });

  const probeProvider = async (p: Provider) => {
    setProbing(p.id);
    await withProviderBusy(p, "probe", async () => {
      const r = await api.probeProvider(p.id);
      setProbeResult({
        provider: p.name || p.vendor_name,
        message: r.message,
        results: r.results || [],
      });
      toast(r.message, "ok");
      load();
    });
    setProbing(null);
  };

  // MOD-04：删除厂商前先查受影响的 Bot，让用户选「去改绑」还是「确认置为不可用」
  const askDeleteProvider = async (p: Provider) => {
    try {
      const bots = await api.bots();
      const affected = bots.filter((b) => b.provider_id === p.id && !b.deleted);
      if (!affected.length) {
        const ok = await confirm({
          title: `删除「${p.vendor_name}」？`,
          description: "删除后需要重新添加才能继续使用该厂商，已绑定的模型选择也会失效。",
          confirmText: "删除",
          danger: true,
        });
        if (!ok) return;
        await withProviderBusy(p, "delete", async () => {
          await api.deleteProvider(p.id, false);
          toast("已删除", "ok");
          load();
        });
        return;
      }
      setDeleteAffected(affected);
      setDeleteTarget(p);
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const forceDeleteProvider = async () => {
    if (!deleteTarget) return;
    const p = deleteTarget;
    setDeleteBusy(true);
    try {
      await api.deleteProvider(p.id, true);
      toast(
        `已删除「${p.vendor_name}」。${deleteAffected.length} 个 Bot 已置为不可用，请尽快重新绑定模型`,
        "ok"
      );
      setDeleteTarget(null);
      setDeleteAffected([]);
      load();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setDeleteBusy(false);
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

          {/* ACC-06：未配置模型接入时的引导。配好之前发起执行会被后端拒绝，
              与其让用户在建群聊时撞到 409，不如在这里说清楚下一步做什么。 */}
          {!providersLoading && !providers.length && (
            <div className="mt-4 rounded-lg border border-accent/40 bg-accent/5 px-3.5 py-3">
              <div className="flex items-center gap-2 text-[12.5px] font-medium text-accent">
                <AlertTriangle size={14} />
                {welcome ? "欢迎！还差一步：先配置模型接入" : "还差一步：先配置模型接入"}
              </div>
              <p className="mt-1 text-[11.5px] leading-relaxed text-muted">
                在下面选一个厂商、填 API Key，模型列表会自动获取（也可以手动填）。配好之后回到群聊，
                总指挥才能开始工作；在此之前发起执行会提示「尚未绑定模型接入」。
              </p>
            </div>
          )}

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
                    {p.last_checked_at && (
                      <span
                        className="hidden text-[10.5px] text-faint sm:inline"
                        title="上次刷新/测试/检测的时间"
                      >
                        上次检测 {timeAgo(p.last_checked_at)}
                      </span>
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      {(() => {
                        const busy = providerBusy?.id === p.id ? providerBusy.action : null;
                        const anyBusy = Boolean(providerBusy);
                        return (
                          <>
                            {p.kind !== "mock" && (
                              <button
                                className="text-[11.5px] text-muted hover:text-ink disabled:opacity-40"
                                disabled={anyBusy}
                                onClick={() => setEditingProvider(p)}
                                title="修改名称、base_url 或重新输入 API Key"
                              >
                                编辑
                              </button>
                            )}
                            <button
                              className="text-[11.5px] text-muted hover:text-ink disabled:opacity-40"
                              disabled={anyBusy}
                              onClick={() => setPricing(p)}
                              title="填写模型单价，用于用量页估算费用"
                            >
                              价格
                            </button>
                            <button
                              className="text-[11.5px] text-muted hover:text-ink disabled:opacity-40"
                              disabled={anyBusy}
                              onClick={() => refreshProvider(p)}
                              title="重新拉取厂商的模型列表"
                            >
                              {busy === "refresh" ? (
                                <span className="inline-flex items-center gap-1">
                                  <Loader2 size={11} className="animate-spin" /> 刷新中
                                </span>
                              ) : (
                                "刷新模型"
                              )}
                            </button>
                            {p.kind !== "mock" && (
                              <button
                                className="text-[11.5px] text-muted hover:text-ink disabled:opacity-40"
                                disabled={anyBusy}
                                title="批量检测流式输出与工具调用能力"
                                onClick={() => probeProvider(p)}
                              >
                                {probing === p.id ? (
                                  <span className="inline-flex items-center gap-1">
                                    <Loader2 size={11} className="animate-spin" /> 检测中
                                  </span>
                                ) : (
                                  "检测能力"
                                )}
                              </button>
                            )}
                            <button
                              className="text-[11.5px] text-muted hover:text-ink disabled:opacity-40"
                              disabled={anyBusy}
                              onClick={() => testProvider(p)}
                              title="真实发一次请求，检测首个模型的连通性与能力"
                            >
                              {busy === "test" ? (
                                <span className="inline-flex items-center gap-1">
                                  <Loader2 size={11} className="animate-spin" /> 测试中
                                </span>
                              ) : (
                                "测试"
                              )}
                            </button>
                            <button
                              className="text-faint hover:text-danger disabled:opacity-40"
                              disabled={anyBusy}
                              onClick={() => askDeleteProvider(p)}
                              title="删除该厂商"
                            >
                              {busy === "delete" ? (
                                <Loader2 size={13} className="animate-spin" />
                              ) : (
                                <Trash2 size={13} />
                              )}
                            </button>
                          </>
                        );
                      })()}
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
          <Section title="默认限额" desc="设置单次任务的 token 预算、委派深度与审批有效期；群聊可单独覆盖 token 预算与委派深度。">
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
                  label="委派深度（1–5 层）"
                  value={settings.delegation_depth ?? 2}
                  onSave={(v) => saveSettings({ delegation_depth: v })}
                  min={1}
                  max={5}
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
            desc="配置后 Bot 才能勾选 web_search。未配置时该工具在 Bot 编辑页禁用，已在用的老 Bot 会回退到免密钥公共搜索（稳定性较差）。"
          >
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px]">
              {settings?.search_configured ? (
                <>
                  <Check size={12} className="text-ok" />
                  <span className="text-ok">已配置</span>
                  <span className="text-faint">· web_search 可在 Bot 里选用</span>
                </>
              ) : (
                <>
                  <AlertTriangle size={12} className="text-warn" />
                  <span className="text-warn">未配置</span>
                  <span className="text-faint">· Bot 编辑页的 web_search 暂不可选</span>
                </>
              )}
            </div>
            <input
              className="field"
              type="password"
              placeholder={
                settings?.search_configured ? "已配置，输入新 Key 可替换" : "搜索服务 API Key"
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const input = e.target as HTMLInputElement;
                  saveSettings({ search_api_key: input.value }, input.value ? "已保存" : "已清除");
                  input.value = "";
                }
              }}
            />
            <p className="mt-1.5 text-[10.5px] text-faint">
              回车保存；留空并回车可清除。目前支持 Tavily。
            </p>
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
                  setPushSt(null);
                  getPushState().then(setPushSt).catch(() => {});
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

            {/* NTF-06：Web Push */}
            <div className="mt-3 border-t border-line pt-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[12px] font-medium text-muted">关闭页面后仍提醒（Web Push）</span>
                {pushSt === "subscribed" ? (
                  <>
                    <button className="btn-ghost" disabled={pushBusy} onClick={disablePush}>
                      关闭推送
                    </button>
                    <button className="btn-ghost" onClick={testPush}>
                      发测试推送
                    </button>
                  </>
                ) : pushSt === "denied" ? (
                  <span className="text-[11px] text-warn">
                    通知权限被拒绝，请先在浏览器站点设置里允许通知
                  </span>
                ) : pushSt === "unsupported" ? (
                  <span className="text-[11px] text-faint">当前浏览器不支持 Web Push</span>
                ) : pushSt === "insecure" ? (
                  <span className="text-[11px] text-faint">
                    Web Push 需要 HTTPS（本机 localhost 可直接开启）
                  </span>
                ) : (
                  <button className="btn-ghost" disabled={pushBusy || pushSt === null} onClick={enablePush}>
                    {pushSt === null ? "检查中…" : "开启推送"}
                  </button>
                )}
              </div>
              <p className="mt-1.5 text-[10.5px] leading-relaxed text-faint">
                页面开着时不会重复弹窗，仍由页面内的通知负责；全部页面关闭后，系统通知由推送服务送达。
                {isIOS() && " iOS / iPadOS 需先把站点添加到主屏幕，并从主屏幕打开。"}
              </p>
            </div>
          </Section>

          {/* 账号 */}
          <Section title="账号" desc={me?.email}>
            {/* ACC-08：邮箱验证状态 */}
            <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-lg border border-line bg-panel2/40 px-3 py-2">
              {me?.email_verified ? (
                <MailCheck size={13} className="shrink-0 text-ok" />
              ) : (
                <Mail size={13} className="shrink-0 text-warn" />
              )}
              <span className="text-[11.5px] text-muted">{me?.email}</span>
              {me?.email_verified ? (
                <span className="text-[11px] text-ok">已验证</span>
              ) : (
                <>
                  <span className="text-[11px] text-warn">未验证</span>
                  <span className="text-[10.5px] text-faint">不影响使用，但没法用邮箱找回密码</span>
                  <button className="btn-ghost ml-auto" disabled={sendingVerify} onClick={sendVerify}>
                    {sendingVerify ? <Loader2 size={12} className="animate-spin" /> : "发送验证邮件"}
                  </button>
                </>
              )}
            </div>
            {/* ACC-09：第三方登录绑定 */}
            {oauth &&
              (oauth.available.google ||
                oauth.available.github ||
                Object.keys(oauth.linked).length > 0) && (
                <div className="mb-3 space-y-1.5">
                  {(["google", "github"] as const).map((p) => {
                    const bound = oauth.linked[p];
                    if (!bound && !oauth.available[p]) return null;
                    const label = p === "google" ? "Google" : "GitHub";
                    return (
                      <div
                        key={p}
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-line bg-panel2/40 px-3 py-2"
                      >
                        <span className="shrink-0 text-muted">
                          {p === "google" ? <GoogleMark size={13} /> : <GithubMark size={13} />}
                        </span>
                        <span className="text-[11.5px] text-muted">{label}</span>
                        {bound ? (
                          <>
                            <span className="text-[11px] text-ok">已绑定</span>
                            <span className="min-w-0 truncate text-[10.5px] text-faint">
                              {bound.email || bound.name || ""}
                            </span>
                            <button
                              className="ml-auto text-[11px] text-faint hover:text-danger"
                              onClick={() => unbindOauth(p)}
                            >
                              解除绑定
                            </button>
                          </>
                        ) : (
                          <>
                            <span className="text-[11px] text-faint">未绑定</span>
                            <button
                              className="btn-ghost ml-auto"
                              onClick={() =>
                                window.location.assign(`/api/auth/oauth/${p}/bind/start`)
                              }
                            >
                              绑定
                            </button>
                            <div className="w-full break-all font-mono text-[10px] leading-relaxed text-faint">
                              回调地址 {oauth.redirect_uri?.[p]}
                              <button
                                className="ml-1 text-accent hover:underline"
                                onClick={() => copyText(oauth.redirect_uri[p])}
                              >
                                复制
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
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

      {/* MOD-02：批量检测的逐模型结果，光看一个「已检测 N 个」不知道哪些不能用 */}
      <Modal
        open={!!probeResult}
        onClose={() => setProbeResult(null)}
        title={`「${probeResult?.provider || ""}」能力检测结果`}
        subtitle={probeResult?.message}
        width="max-w-lg"
        footer={
          <button className="btn-primary" onClick={() => setProbeResult(null)}>
            知道了
          </button>
        }
      >
        <div className="max-h-[46vh] space-y-1.5 overflow-y-auto">
          {(probeResult?.results || []).map((r) => (
            <div key={r.model} className="rounded-lg border border-line px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11.5px]">{r.model}</span>
                {r.ok ? (
                  <Check size={12} className="text-ok" />
                ) : (
                  <AlertTriangle size={12} className="text-danger" />
                )}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px]">
                <span
                  className={`chip ${
                    r.streaming === false
                      ? "border-danger/40 text-danger"
                      : r.streaming
                        ? "border-ok/40 text-ok"
                        : "text-faint"
                  }`}
                >
                  {capLabel(r.streaming, "流式")}
                </span>
                <span
                  className={`chip ${
                    r.tool_calling === false
                      ? "border-danger/40 text-danger"
                      : r.tool_calling
                        ? "border-ok/40 text-ok"
                        : "text-faint"
                  }`}
                >
                  {capLabel(r.tool_calling, "工具调用")}
                </span>
                {!r.ok && r.message && <span className="text-faint">{r.message}</span>}
              </div>
            </div>
          ))}
          {!(probeResult?.results || []).length && (
            <div className="rounded-lg border border-dashed border-line px-3 py-5 text-center text-[12px] text-faint">
              没有可展示的结果
            </div>
          )}
        </div>
        <p className="mt-2 text-[10.5px] leading-relaxed text-faint">
          只检测前 12 个模型；未检测的模型不拦截选择，但可能在执行时才失败。可以再次点「检测能力」继续。
        </p>
      </Modal>

      {/* MOD-04：删除厂商前列出受影响的 Bot，二选一 */}
      <Modal
        open={!!deleteTarget}
        onClose={() => !deleteBusy && setDeleteTarget(null)}
        title={`删除「${deleteTarget?.vendor_name || ""}」？`}
        subtitle={`有 ${deleteAffected.length} 个 Bot 正在使用该厂商`}
        footer={
          <>
            <button className="btn-ghost" disabled={deleteBusy} onClick={() => setDeleteTarget(null)}>
              取消
            </button>
            <button
              className="btn-ghost"
              disabled={deleteBusy}
              onClick={() => {
                window.location.href = "/bots";
              }}
            >
              去改绑
            </button>
            <button className="btn-danger" disabled={deleteBusy} onClick={forceDeleteProvider}>
              {deleteBusy ? "删除中…" : "置为不可用并删除"}
            </button>
          </>
        }
      >
        <div className="max-h-[40vh] space-y-1.5 overflow-y-auto">
          {deleteAffected.map((b) => (
            <div
              key={b.id}
              className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-[12px]"
            >
              <span className="min-w-0 truncate">{b.name}</span>
              <span className="ml-auto shrink-0 font-mono text-[10.5px] text-faint">
                {b.model || "未绑定模型"}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted">
          删除后这些 Bot 会失去模型，无法发起执行，直到你为它们重新选择模型。历史消息与执行记录保留。
        </p>
      </Modal>
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
                  modelCapability(r).bad
                    ? "text-warn"
                    : modelCapability(r).unknown
                      ? "text-faint"
                      : "text-ok"
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
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim()) return toast("名称不能为空", "error");
    if (!baseUrl.trim()) return toast("base_url 不能为空", "error");
    setSaving(true);
    setError(null);
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
      setError(e.message || "保存失败，请稍后重试");
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
        {error && (
          <div className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
            {error}
          </div>
        )}
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
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setLoading(true);
    setError(null);
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
      if (r.detected) {
        toast(r.message, "ok");
        onDone();
      } else {
        // 没探测到模型：保留弹窗，把原因内联展示，让用户能改 Key / base_url / 模型名
        setError(r.message || "未能获取模型，请检查 API Key 与 base_url");
        toast(r.message, "error");
      }
    } catch (e: any) {
      setError(e.message || "添加失败，请稍后重试");
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
        {error && (
          <div className="break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[11.5px] leading-relaxed text-danger">
            {error}
          </div>
        )}
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

        {loading && (
          <p className="flex items-center gap-1.5 rounded-lg border border-line bg-panel2/50 px-2.5 py-2 text-[11px] text-muted">
            <Loader2 size={12} className="animate-spin" />
            正在获取模型列表并探测首个模型，通常需要 5–20 秒，请稍候…
          </p>
        )}
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
  min,
  max,
}: {
  label: string;
  value: number;
  onSave: (v: number) => void | Promise<boolean | void>;
  allowUnlimited?: boolean;
  disabled?: boolean;
  min?: number;
  max?: number;
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
            min={allowUnlimited ? 1000 : min}
            max={max}
            disabled={disabled}
            value={v}
            onChange={(e) => setV(Number(e.target.value))}
          />
          <button className="btn-ghost h-10 w-10 shrink-0 p-0" aria-label={`保存${label}`} onClick={() => onSave(v)}
            disabled={
              disabled ||
              v === value ||
              (allowUnlimited && (!Number.isInteger(v) || v < 1000)) ||
              (min !== undefined && (!Number.isInteger(v) || v < min)) ||
              (max !== undefined && v > max)
            }>
            <Save size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
