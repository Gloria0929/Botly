"use client";

import { useEffect } from "react";
import { toast } from "@/lib/ui";
import { errText } from "@/lib/api";

/**
 * 全局错误兜底：任何没被业务代码 catch 的 Promise 拒绝或运行时异常，
 * 都以错误提示的形式弹出来，避免「点了一下、什么都没发生」。
 */
export default function GlobalError() {
  useEffect(() => {
    const onRejection = (e: PromiseRejectionEvent) => {
      toast(errText(e.reason), "error");
    };
    const onError = (e: ErrorEvent) => {
      toast(errText(e.error, e.message || "发生未知错误"), "error");
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
