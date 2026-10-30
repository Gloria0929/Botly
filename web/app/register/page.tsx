"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({ email: "", password: "", invite: "", name: "" });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (form.password.length < 8) {
      setErr("密码至少 8 位");
      return;
    }
    setLoading(true);
    try {
      await api.register({
        email: form.email,
        password: form.password,
        invite_code: form.invite.trim(),
        name: form.name || undefined,
      });
      router.push("/chat");
    } catch (e: any) {
      setErr(e.message || "注册失败");
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
            第一版
            <br />
            邀请制内测
          </h1>
          <p className="mt-4 max-w-sm text-[12.5px] leading-relaxed text-white/55">
            注册后会自动为你创建总指挥、研究员、写手三个 Bot，以及一个可直接使用的群聊。
          </p>
        </div>
        <p className="relative max-w-sm text-[11.5px] leading-relaxed text-white/35">
          说明：你的消息内容会发送给你自己配置的模型服务商。平台不提供统一的模型额度，
          需要自备 OpenAI 兼容接口的 base_url 与 API Key。
        </p>
      </div>

      <div className="flex items-center justify-center px-6 py-12">
        <form onSubmit={submit} className="w-full max-w-[340px]">
          <h2 className="text-[22px] font-semibold tracking-tight">创建账号</h2>
          <p className="mt-1.5 text-[13px] text-muted">需要邀请码，暂不开放自由注册</p>

          <div className="mt-7 space-y-4">
            <div>
              <label className="label">邀请码</label>
              <input
                className="field font-mono tracking-wide"
                required
                value={form.invite}
                onChange={set("invite")}
                placeholder="BOTLY-XXXX-XXXX"
              />
            </div>
            <div>
              <label className="label">邮箱</label>
              <input
                className="field"
                type="email"
                required
                value={form.email}
                onChange={set("email")}
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className="label">昵称（可选）</label>
              <input className="field" value={form.name} onChange={set("name")} placeholder="怎么称呼你" />
            </div>
            <div>
              <label className="label">密码</label>
              <input
                className="field"
                type="password"
                required
                value={form.password}
                onChange={set("password")}
                placeholder="至少 8 位"
              />
            </div>
          </div>

          {err && (
            <div className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12.5px] text-danger">
              {err}
            </div>
          )}

          <button className="btn-primary mt-6 w-full py-2.5" disabled={loading}>
            {loading ? <Loader2 size={15} className="animate-spin" /> : <>注册并进入 <ArrowRight size={15} /></>}
          </button>

          <p className="mt-5 text-center text-[12.5px] text-muted">
            使用时，消息及提供给 Bot 的文件内容会发送至你自行配置的模型服务商。
          </p>
          <p className="mt-5 text-center text-[12.5px] text-muted">
            已有账号？
            <Link href="/login" className="ml-1 text-accent hover:underline">
              直接登录
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
