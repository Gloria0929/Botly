"use client";

import { useEffect, useState } from "react";
import { Loader2, Save, X } from "lucide-react";

export function SearchKeyField({
  configured,
  disabled = false,
  onSave,
}: {
  configured: boolean;
  disabled?: boolean;
  onSave: (key: string) => Promise<boolean>;
}) {
  const [key, setKey] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const locked = disabled || saving;
  const showConfigured = configured && !editing;

  const save = async () => {
    const value = key.trim();
    if (locked || showConfigured || !value) return;
    setSaving(true);
    try {
      if (await onSave(value)) {
        setKey("");
        setEditing(false);
      }
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    if (locked) return;
    setKey("");
    setEditing(false);
  };

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        <div className={`field flex min-w-0 flex-1 basis-full items-center sm:basis-0 ${locked ? "opacity-50" : ""}`}>
          {showConfigured ? (
            <span className="chip gap-1.5 border-ok/30 text-ok">
              已配置
              <button type="button" className="rounded p-0.5 hover:bg-panel2 disabled:opacity-40"
                aria-label="重新配置搜索 Key" title="重新配置搜索 Key" disabled={locked}
                onClick={() => { setKey(""); setEditing(true); }}>
                <X size={12} />
              </button>
            </span>
          ) : (
            <input
              className="min-w-0 w-full bg-transparent text-ink outline-none placeholder:text-faint"
              type="password"
              aria-label="搜索服务 API Key"
              autoComplete="off"
              autoFocus={configured}
              spellCheck={false}
              value={key}
              disabled={locked}
              placeholder={configured ? "输入新 Key，保存后替换原配置" : "搜索服务 API Key"}
              onChange={(event) => setKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  void save();
                } else if (event.key === "Escape" && configured) {
                  event.preventDefault();
                  cancel();
                }
              }}
            />
          )}
        </div>
        {!showConfigured && (
          <>
            <button type="button" className="btn-primary shrink-0" disabled={locked || !key.trim()} onClick={save}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              {saving ? "正在保存…" : "保存 Key"}
            </button>
            {configured && (
              <button type="button" className="btn-ghost shrink-0" aria-label="取消重新配置搜索 Key" disabled={locked} onClick={cancel}>
                取消
              </button>
            )}
          </>
        )}
      </div>
      <p className="mt-1.5 text-[10.5px] text-faint">
        {showConfigured
          ? "支持 Tavily。点击 × 可重新配置。"
          : configured
            ? "新 Key 保存成功后才替换原配置；留空或取消保留原 Key。"
            : "支持 Tavily。输入 Key 后点击「保存 Key」或按回车。"}
      </p>
    </div>
  );
}

export function Section({
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
    <section
      id={`setting-${title}`}
      className="settings-section mb-6 overflow-visible rounded-xl border border-line bg-panel"
    >
      <div className="flex items-start gap-4 border-b border-line px-5 py-5 sm:px-6">
        <div>
          <h2 className="section-title">{title}</h2>
          {desc && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
              {desc}
            </p>
          )}
        </div>
        <div className="ml-auto">{action}</div>
      </div>
      <div className="p-5 sm:p-6">{children}</div>
    </section>
  );
}

export function NumField({
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
            <input
              type="checkbox"
              checked={unlimited}
              disabled={disabled}
              className="h-3.5 w-3.5 accent-[var(--accent)]"
              onChange={async (e) => {
                const next = e.target.checked;
                setUnlimited(next);
                const saved = await onSave(next ? 0 : v >= 1000 ? v : 300000);
                if (saved === false) setUnlimited(!next);
              }}
            />
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
          <button
            className="btn-ghost h-10 w-10 shrink-0 p-0"
            aria-label={`保存${label}`}
            onClick={() => onSave(v)}
            disabled={
              disabled ||
              v === value ||
              (allowUnlimited && (!Number.isInteger(v) || v < 1000)) ||
              (min !== undefined && (!Number.isInteger(v) || v < min)) ||
              (max !== undefined && v > max)
            }
          >
            <Save size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
