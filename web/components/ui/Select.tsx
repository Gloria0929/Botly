"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import Avatar from "./Avatar";

export type SelectOption = {
  value: string;
  label: string;
  description?: string;
  /** emoji / 图片地址 / 短标识 */
  avatar?: string | null;
  color?: string;
  disabled?: boolean;
};

/**
 * 统一的下拉选择组件，替代原生 select。
 * - 支持搜索、分组标题、头像/图标
 * - freeInput: 允许输入列表外的值（如火山方舟的接入点 ID）
 */
export default function Select({
  value,
  onChange,
  options,
  placeholder = "请选择",
  searchable,
  freeInput = false,
  disabled = false,
  className = "",
  size = "md",
  emptyText = "没有匹配项",
}: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  placeholder?: string;
  searchable?: boolean;
  freeInput?: boolean;
  disabled?: boolean;
  className?: string;
  size?: "sm" | "md";
  emptyText?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  const current = options.find((o) => o.value === value);

  const filtered = useMemo(() => {
    if (!query.trim()) return options;
    const q = query.trim().toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q) ||
        (o.description || "").toLowerCase().includes(q)
    );
  }, [options, query]);

  const canUseCustom =
    freeInput && query.trim() && !options.some((o) => o.value === query.trim());

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    if (searchable || freeInput) setTimeout(() => input.current?.focus(), 30);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, searchable, freeInput]);

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
    setQuery("");
  };

  const h = size === "sm" ? "h-8 text-[12px]" : "h-9 text-[13px]";

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex w-full items-center gap-2 rounded-lg border bg-panel2 px-2.5 ${h} text-left outline-none transition-colors ${
          disabled
            ? "cursor-not-allowed border-line opacity-50"
            : open
              ? "border-accent"
              : "border-line hover:border-faint/60"
        }`}
      >
        {current?.avatar !== undefined && current?.avatar !== null && (
          <Avatar
            value={current.avatar}
            color={current.color}
            size={size === "sm" ? 18 : 20}
            alt={current.label}
          />
        )}
        <span className={`min-w-0 flex-1 truncate ${current ? "text-ink" : "text-faint"}`}>
          {current?.label || value || placeholder}
        </span>
        <ChevronDown
          size={13}
          className={`shrink-0 text-faint transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full min-w-[200px] overflow-hidden rounded-xl border border-line bg-panel shadow-card">
          {(searchable || freeInput) && (
            <div className="flex items-center gap-1.5 border-b border-line px-2.5 py-1.5">
              <Search size={12} className="shrink-0 text-faint" />
              <input
                ref={input}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    if (canUseCustom) pick(query.trim());
                    else if (filtered.length) pick(filtered[0].value);
                  }
                }}
                placeholder="搜索…"
                className="w-full bg-transparent text-[12px] text-ink outline-none placeholder:text-faint"
              />
            </div>
          )}
          <div className="max-h-60 overflow-y-auto py-1" role="listbox">
            {filtered.map((o) => (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={o.value === value}
                disabled={o.disabled}
                onClick={() => pick(o.value)}
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors ${
                  o.disabled
                    ? "cursor-not-allowed opacity-40"
                    : o.value === value
                      ? "bg-accent/10"
                      : "hover:bg-panel2"
                }`}
              >
                {o.avatar !== undefined && o.avatar !== null && (
                  <Avatar value={o.avatar} color={o.color} size={20} alt={o.label} />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-ink">{o.label}</span>
                  {o.description && (
                    <span className="block truncate text-[10.5px] text-faint">{o.description}</span>
                  )}
                </span>
                {o.value === value && <Check size={13} className="shrink-0 text-accent" />}
              </button>
            ))}
            {canUseCustom && (
              <button
                type="button"
                onClick={() => pick(query.trim())}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-panel2"
              >
                <span className="flex h-5 w-5 items-center justify-center rounded-md border border-dashed border-line text-[11px] text-faint">
                  +
                </span>
                <span className="text-[12.5px] text-muted">
                  使用「<span className="text-ink">{query.trim()}</span>」
                </span>
              </button>
            )}
            {!filtered.length && !canUseCustom && (
              <div className="px-2.5 py-4 text-center text-[11.5px] text-faint">{emptyText}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
