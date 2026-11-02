"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2 } from "lucide-react";
import { api, errText } from "@/lib/api";
import AuthMarketing from "@/components/AuthMarketing";
import { LogoMark } from "@/components/ui/Logo";

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
      // ACC-06：新账号还没有模型接入，先带去设置页完成配置，再回群聊
      router.push("/settings?welcome=1");
    } catch (e: any) {
      setErr(errText(e, "注册失败"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthMarketing
        badge="邀请制内测"
        title={
          <>
            第一版
            <br />
            邀请制内测
          </>
        }
        description="注册后会自动为你创建总指挥、研究员、架构师三个 Bot，以及一个群聊；填好模型接入的 API Key 就能开始。"
        note="说明：你的消息内容会发送给你自己配置的模型服务商。平台不提供统一的模型额度，需要自备 OpenAI 兼容接口的 base_url 与 API Key。"
      />

      <div className="relative flex items-center justify-center px-5 py-12">
        <form onSubmit={submit} className="w-full max-w-[372px] animate-rise">
          <div className="mb-7 flex items-center gap-2.5 lg:hidden">
            <LogoMark size={30} />
            <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.08em]">Botly</span>
          </div>

          <div className="card p-6 md:p-7">
            <div className="page-eyebrow">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              AUTH / REGISTER
            </div>
            <h2 className="text-[26px] font-bold tracking-[-0.03em]">创建账号</h2>
            <p className="mt-1.5 text-[12.5px] text-muted">需要邀请码，暂不开放自由注册</p>

            <div className="mt-6 space-y-4">
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
              <div className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12px] text-danger">
                {err}
              </div>
            )}

            <button className="btn-primary mt-6 w-full py-2.5" disabled={loading}>
              {loading ? <Loader2 size={15} className="animate-spin" /> : <>注册并进入 <ArrowRight size={15} /></>}
            </button>

            <p className="mt-5 text-center text-[11.5px] leading-relaxed text-faint">
              使用时，消息及提供给 Bot 的文件内容会发送至你自行配置的模型服务商。
            </p>
          </div>

          <p className="mt-5 text-center text-[12.5px] text-muted">
            已有账号？
            <Link href="/login" className="ml-1 font-medium text-accent hover:underline">
              直接登录
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
