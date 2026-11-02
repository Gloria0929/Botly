"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import AuthShell from "@/components/AuthShell";
import { api } from "@/lib/api";

type State = { kind: "loading" } | { kind: "ok"; email: string } | { kind: "error"; message: string };

/** ACC-08：邮件里的验证链接落在这里，进页面就消费令牌。 */
export default function VerifyEmailPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setState({ kind: "error", message: "链接里没有验证令牌，可能是链接被截断了。" });
      return;
    }
    let alive = true;
    api
      .verifyEmail(token)
      .then((r) => alive && setState({ kind: "ok", email: r.email }))
      .catch((e: any) => alive && setState({ kind: "error", message: e.message || "验证失败" }));
    return () => {
      alive = false;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <AuthShell title="正在验证邮箱">
        <div className="flex items-center gap-2 text-[12.5px] text-muted">
          <Loader2 size={15} className="animate-spin text-faint" /> 正在确认这个链接…
        </div>
      </AuthShell>
    );
  }

  if (state.kind === "error") {
    return (
      <AuthShell
        title="验证失败"
        subtitle="链接可能已经用过或超过 24 小时。登录后可以在设置页重新发送一封。"
      >
        <div className="flex items-start gap-2 rounded-xl border border-danger/35 bg-danger/10 px-4 py-3 text-[12.5px] leading-relaxed text-danger">
          <XCircle size={14} className="mt-0.5 shrink-0" />
          {state.message}
        </div>
        <Link href="/login" className="btn-primary mt-6 w-full py-2.5">
          去登录
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="邮箱已验证" subtitle={`${state.email} 已确认，可以用来找回密码了。`}>
      <div className="flex items-center gap-2 rounded-xl border border-ok/35 bg-ok/10 px-4 py-3 text-[12.5px] text-ok">
        <CheckCircle2 size={15} className="shrink-0" />
        验证完成，回到设置页就能看到状态已更新。
      </div>
      <Link href="/chat" className="btn-primary mt-6 w-full py-2.5">
        进入工作台
      </Link>
    </AuthShell>
  );
}
