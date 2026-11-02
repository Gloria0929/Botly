"use client";

import { useEffect } from "react";
import { toast } from "@/lib/ui";

/**
 * 全局错误兜底：任何没被业务代码 catch 的 Promise 拒绝或运行时异常，
 * 都以错误提示的形式弹出来，避免「点了一下、什么都没发生」。
 */
export default function GlobalError() {
  useEffect(() => {
    const onRejection = (e: PromiseRejectionEvent) => {
      const reason = e.reason;
      const message =
        (reason && typeof reason === "object" && "message" in reason
          ? String((reason as any).message)
          : "") ||
        (typeof reason === "string" ? reason : "") ||
        "请求失败，请稍后重试";
      toast(message, "error");
    };
    const onError = (e: ErrorEvent) => {
      toast(e.message || "发生未知错误", "error");
    };
    window.addEventListener("unhandledrejection", onRejection);
    window.addEventListener("error", onError);
    return () => {
      window.removeEventListener("unhandledrejection", onRejection);
      window.removeEventListener("error", onError);
    };
  }, []);
  return null;
}
