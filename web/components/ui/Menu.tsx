"use client";

import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";

export type MenuItem = {
  label: string;
  icon?: React.ComponentType<{ size?: number | string; className?: string }>;
  danger?: boolean;
  onClick: () => void;
};

/** 轻量操作菜单：点击外部或 Esc 关闭。 */
export default function Menu({
  items,
  align = "right",
  trigger,
  title = "更多操作",
}: {
  items: MenuItem[];
  align?: "left" | "right";
  trigger?: React.ReactNode;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

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
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={`flex h-7 w-7 items-center justify-center rounded-lg transition-colors ${
          open ? "bg-panel2 text-ink" : "text-faint hover:bg-panel2 hover:text-ink"
        }`}
      >
        {trigger || <MoreHorizontal size={16} />}
      </button>

      {open && (
        <div
          role="menu"
          className={`absolute top-full z-50 mt-1 w-40 overflow-hidden rounded-xl border border-line bg-panel py-1 shadow-card ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {items.map((it) => {
            const Icon = it.icon;
            return (
              <button
                key={it.label}
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  it.onClick();
                }}
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] transition-colors ${
                  it.danger
                    ? "text-danger hover:bg-danger/10"
                    : "text-muted hover:bg-panel2 hover:text-ink"
                }`}
              >
                {Icon && <Icon size={13} />}
                {it.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
