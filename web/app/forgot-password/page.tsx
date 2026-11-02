"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2, Mail } from "lucide-react";
import AuthShell from "@/components/AuthShell";
import { api } from "@/lib/api";

/** ACC-08：申请重置密码。不区分邮箱是否存在，统一给同样的提示。 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      const r = await api.forgotPassword(email.trim());
      setDevLink(r.dev_link || null);
      setSent(true);
    } catch (e: any) {
      setErr(e.message || "发送失败");
    } finally {
      setLoading(false);
    }
  };

  if (sent) {
    return (
      <AuthShell
        title="检查你的邮箱"
        subtitle="如果这个邮箱已注册，我们刚刚发送了一封带重置链接的邮件。"
      >
        <div className="rounded-xl border border-line bg-panel px-4 py-3.5 text-[12.5px] leading-relaxed text-muted">
          <div className="flex items-center gap-2 text-ink">
            <Mail size={14} className="text-accent" />
            <span className="font-medium">{email}</span>
          </div>
          <p className="mt-2">
            链接 60 分钟内有效，且只能使用一次。没收到的话，请检查垃圾邮件目录，
            或稍等一分钟后再试。
          </p>
        </div>

        {devLink && (
          <div className="mt-4 rounded-xl border border-warn/35 bg-warn/[0.08] px-4 py-3 text-[12px] leading-relaxed text-warn">
            本地开发模式（未配置 SMTP）：邮件没有真的发出。
            <a href={devLink} className="mt-1 block break-all underline">
              {devLink}
            </a>
          </div>
        )}

        <button className="btn-ghost mt-5 w-full py-2.5" onClick={() => setSent(false)}>
          换一个邮箱再试
        </button>
        <p className="mt-5 text-center text-[12.5px] text-muted">
          <Link href="/login" className="text-accent hover:underline">
            返回登录
          </Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="找回密码"
      subtitle="填写注册时用的邮箱，我们会发一封带重置链接的邮件。"
      footnote="只能通过邮箱重置：第一版没有绑定手机号，也不做安全问题。"
    >
      <form onSubmit={submit}>
        <label className="label">邮箱</label>
        <input
          className="field"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
        />

        {err && (
          <div className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12.5px] text-danger">
            {err}
          </div>
        )}

        <button className="btn-primary mt-6 w-full py-2.5" disabled={loading}>
          {loading ? <Loader2 size={15} className="animate-spin" /> : <>发送重置链接 <ArrowRight size={15} /></>}
        </button>
      </form>

      <p className="mt-5 text-center text-[12.5px] text-muted">
        想起来了？
        <Link href="/login" className="ml-1 text-accent hover:underline">
          直接登录
        </Link>
      </p>
    </AuthShell>
  );
}
