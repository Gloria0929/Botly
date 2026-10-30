"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      await api.login(email, password);
      router.push("/chat");
    } catch (e: any) {
      setErr(e.message || "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-white/10 bg-[#0b0e13] p-12 text-white lg:flex">
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(680px 420px at 10% 6%, rgba(124,140,255,.28), transparent 62%), radial-gradient(520px 380px at 90% 94%, rgba(52,211,153,.16), transparent 60%)",
          }}
        />
        <div className="relative">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-[14px] font-bold text-accentink">
              B
            </div>
            <span className="text-[15px] font-semibold tracking-tight text-white">Botly</span>
          </div>
          <h1 className="mt-16 max-w-md text-[34px] font-semibold leading-[1.25] tracking-tight text-white">
            把多步骤工作
            <br />
            交给一支 AI 团队
          </h1>
          <p className="mt-4 max-w-sm text-[12.5px] leading-relaxed text-white/55">
            总指挥拆解任务，子 Bot 在各自线程里执行，过程在同一个群聊里可见。
            高风险动作会停下来等你审批。
          </p>
        </div>
        <div className="relative grid grid-cols-3 gap-3">
          {[
            { k: "派活", v: "总指挥拆解并调度" },
            { k: "可见", v: "每个子任务独立线程" },
            { k: "可控", v: "审批、限额与停止" },
          ].map((i) => (
            <div key={i.k} className="rounded-xl border border-white/10 bg-white/[0.04] p-3.5">
              <div className="text-[12px] font-medium text-white/90">{i.k}</div>
              <div className="mt-1 text-[11.5px] leading-relaxed text-white/40">{i.v}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <form onSubmit={submit} className="w-full max-w-[340px]">
          <h2 className="text-[22px] font-semibold tracking-tight">登录</h2>
          <p className="mt-1.5 text-[13px] text-muted">使用邮箱与密码进入工作台</p>

          <div className="mt-7 space-y-4">
            <div>
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
            </div>
            <div>
              <label className="label">密码</label>
              <input
                className="field"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="至少 8 位"
                autoComplete="current-password"
              />
            </div>
          </div>

          {err && (
            <div className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12.5px] text-danger">
              {err}
            </div>
          )}

          <button className="btn-primary mt-6 w-full py-2.5" disabled={loading}>
            {loading ? <Loader2 size={15} className="animate-spin" /> : <>登录 <ArrowRight size={15} /></>}
          </button>

          <p className="mt-5 text-center text-[12.5px] text-muted">
            还没有账号？
            <Link href="/register" className="ml-1 text-accent hover:underline">
              用邀请码注册
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
