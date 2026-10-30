"use client";

import { useEffect } from "react";
import { X } from "lucide-react";

export default function Modal({
  open,
  onClose,
  title,
  subtitle,
  footer,
  width = "max-w-md",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  subtitle?: string;
  footer?: React.ReactNode;
  width?: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        className={`relative flex max-h-[88vh] w-full ${width} animate-rise flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-card`}
      >
        {(title || subtitle) && (
          <div className="flex items-start gap-2 border-b border-line px-4 py-3">
            <div className="min-w-0 flex-1">
              {title && <div className="text-[13px] font-medium">{title}</div>}
              {subtitle && (
                <div className="mt-0.5 text-[11px] leading-relaxed text-faint">{subtitle}</div>
              )}
            </div>
            <button
              onClick={onClose}
              className="-mr-1 -mt-0.5 shrink-0 rounded-md p-1 text-faint hover:bg-panel2 hover:text-ink"
              aria-label="关闭"
            >
              <X size={15} />
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</div>
        )}
      </div>
    </div>
  );
}
