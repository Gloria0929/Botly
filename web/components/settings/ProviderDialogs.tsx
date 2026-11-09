"use client";

import { useState } from "react";
import { ChevronDown, Loader2, Plus, Save } from "lucide-react";
import AvatarPicker from "@/components/ui/AvatarPicker";
import Modal from "@/components/ui/Modal";
import { api, toastError, errText } from "@/lib/api";
import { modelCapability } from "@/lib/fmt";
import type { Provider, Vendor } from "@/lib/types";
import { toast } from "@/lib/ui";

const toPrice = (v: string): number | null => {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : null;
};

export function PriceDialog({
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
      input:
        m.input_price === null || m.input_price === undefined
          ? ""
          : String(m.input_price),
      output:
        m.output_price === null || m.output_price === undefined
          ? ""
          : String(m.output_price),
      streaming: m.streaming ?? null,
      tool_calling: m.tool_calling ?? null,
    })),
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
      toastError(e);
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
            {saving ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Save size={13} />
            )}{" "}
            保存
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
              <span
                className="block truncate font-mono text-[11.5px] text-muted"
                title={r.name}
              >
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
          <div className="py-8 text-center text-[11.5px] text-faint">
            没有匹配的模型
          </div>
        )}
      </div>
    </Modal>
  );
}

export function ProviderEditDialog({
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
    if (!name.trim()) { setError("请填写服务名称。"); return toast("名称不能为空", "error"); }
    if (!baseUrl.trim()) { setError("请填写 base_url 地址。"); return toast("base_url 不能为空", "error"); }
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
          .catch((e) => toastError(e));
      }
    } catch (e: any) {
      setError(errText(e, "保存失败，请稍后重试"));
      toastError(e);
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
            {saving ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Save size={13} />
            )}{" "}
            保存
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
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
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

export function VendorDialog({
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
    if (!custom && !key.trim()) {
      setError("请填写 API Key 后再添加此模型厂商。");
      return toast("请填写 API Key", "error");
    }
    if (custom && advanced && !baseUrl.trim()) {
      setError("请填写兼容 OpenAI 的 base_url。");
      return toast("请填写 base_url", "error");
    }
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
      setError(errText(e, "添加失败，请稍后重试"));
      toastError(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`添加 ${vendor.name}`}
      subtitle={
        custom
          ? "填写兼容 OpenAI 的 base_url 与模型名"
          : "只需填写 API Key，其余自动完成"
      }
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={submit} disabled={loading}>
            {loading ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Plus size={13} />
            )}{" "}
            添加
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
            {custom && (
              <span className="ml-1 font-normal text-faint">（可选）</span>
            )}
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
            <p className="mt-1.5 text-[10.5px] leading-relaxed text-warn">
              {vendor.note}
            </p>
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
          保存时会调用{" "}
          <code className="text-muted">GET {baseUrl || "base_url"}/models</code>{" "}
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
