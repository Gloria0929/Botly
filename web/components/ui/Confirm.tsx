"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";
import Modal from "./Modal";

type Options = {
  title: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
};

const ConfirmCtx = createContext<(o: Options) => Promise<boolean>>(
  () => Promise.resolve(false)
);

/** 全局确认弹窗：替代原生 confirm()，风格与站点一致。 */
export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{
    opts: Options;
    resolve: (v: boolean) => void;
  } | null>(null);

  const confirm = useCallback(
    (opts: Options) =>
      new Promise<boolean>((resolve) => {
        setState({ opts, resolve });
      }),
    []
  );

  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };

  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmCtx.Provider value={value}>
      {children}
      <Modal
        open={!!state}
        onClose={() => close(false)}
        width="max-w-sm"
        footer={
          <>
            <button className="btn-ghost" onClick={() => close(false)}>
              {state?.opts.cancelText || "取消"}
            </button>
            <button
              className={state?.opts.danger ? "btn-danger" : "btn-primary"}
              onClick={() => close(true)}
              autoFocus
            >
              {state?.opts.confirmText || "确认"}
            </button>
          </>
        }
      >
        <div className="flex gap-3">
          <div
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              state?.opts.danger ? "bg-danger/12 text-danger" : "bg-accent/12 text-accent"
            }`}
          >
            <AlertTriangle size={15} />
          </div>
          <div className="min-w-0">
            <div className="text-[13.5px] font-medium">{state?.opts.title}</div>
            {state?.opts.description && (
              <p className="mt-1 text-[12px] leading-relaxed text-muted">
                {state?.opts.description}
              </p>
            )}
          </div>
        </div>
      </Modal>
    </ConfirmCtx.Provider>
  );
}

export function useConfirm() {
  return useContext(ConfirmCtx);
}
