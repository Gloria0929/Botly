"use client";

import { useEffect, useId, useRef } from "react";
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
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => {
      const first = panel.current?.querySelector<HTMLElement>("input:not([disabled]), textarea:not([disabled]), button:not([disabled])");
      (first || panel.current)?.focus({ preventScroll: true });
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); }
      if (e.key !== "Tab") return;
      const elements = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex="0"]') || []).filter(el => el.getClientRects().length);
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (!first) { e.preventDefault(); panel.current?.focus(); return; }
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { e.preventDefault(); last.focus(); }
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : "对话框"}
        className={`relative flex max-h-[88vh] w-full ${width} animate-rise flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-pop`}
      >
        {(title || subtitle) && (
          <div className="flex items-start gap-3 border-b border-line px-6 py-5">
            <div className="min-w-0 flex-1">
              {title && <div id={titleId} className="text-[16px] font-semibold">{title}</div>}
              {subtitle && (
                <div className="mt-2 text-[12px] leading-relaxed text-muted">{subtitle}</div>
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
        <div className="min-h-0 flex-1 overflow-y-auto p-6">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line/80 px-5 py-3.5">{footer}</div>
        )}
      </div>
    </div>
  );
}
