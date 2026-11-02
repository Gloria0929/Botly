"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2 } from "lucide-react";
import { GithubMark, GoogleMark } from "@/components/ui/BrandIcons";
import AuthMarketing from "@/components/AuthMarketing";
import { LogoMark } from "@/components/ui/Logo";
import { api, errText } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  // ACC-08：从重置密码页跳回来时给一条成功提示（用 location 读，避免整页套 Suspense）
  const [resetDone, setResetDone] = useState(false);
  // 第三方登录：没配 client id/secret 的 provider 后端会返回 false，这里就不显示按钮
  const [providers, setProviders] = useState<{ google: boolean; github: boolean; signup: boolean } | null>(null);
  const [oauthError, setOauthError] = useState("");
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setResetDone(q.get("reset") === "1");
    const oauthErr = q.get("oauth_error");
    if (oauthErr) setOauthError(oauthErr);
    api
      .oauthProviders()
      .then(setProviders)
      .catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      await api.login(email, password);
      // 中间件把未登录的目标页带在 ?next= 上，登录后原路回去
      const next = new URLSearchParams(window.location.search).get("next");
      if (next && next.startsWith("/") && !next.startsWith("//")) {
        router.push(next);
        return;
      }
      // ACC-06：一个模型接入都没有时先去设置页引导，别让用户到群聊才撞到 409
      const providers = await api.providers().catch(() => null);
      router.push(providers && providers.length === 0 ? "/settings?welcome=1" : "/chat");
    } catch (e: any) {
      setErr(errText(e, "登录失败"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      <AuthMarketing
        title={
          <>
            把多步骤工作
            <br />
            交给一支 AI 团队
          </>
        }
        description="总指挥拆解任务，子 Bot 在各自线程里执行，过程在同一个群聊里可见。高风险动作会停下来等你审批。"
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
              AUTH / SIGN IN
            </div>
            <h2 className="text-[26px] font-bold tracking-[-0.03em]">登录</h2>
            <p className="mt-1.5 text-[12.5px] text-muted">使用邮箱与密码进入工作台</p>

            <div className="mt-6 space-y-4">
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
                <Link href="/forgot-password" className="mt-1.5 inline-block text-[11.5px] text-accent hover:underline">
                  忘记密码？
                </Link>
              </div>
            </div>

            {resetDone && !err && (
              <div className="mt-4 rounded-lg border border-ok/35 bg-ok/10 px-3 py-2 text-[12px] text-ok">
                密码已重置，请用新密码登录。
              </div>
            )}

            {(oauthError || err) && (
              <div className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12px] text-danger">
                {oauthError || err}
              </div>
            )}

            <button className="btn-primary mt-6 w-full py-2.5" disabled={loading}>
              {loading ? <Loader2 size={15} className="animate-spin" /> : <>登录 <ArrowRight size={15} /></>}
            </button>

            {(providers?.google || providers?.github) && (
              <>
                <div className="my-5 flex items-center gap-3 text-[11px] text-faint">
                  <span className="h-px flex-1 bg-line" />
                  或
                  <span className="h-px flex-1 bg-line" />
                </div>
                <div className="space-y-2">
                  {providers?.google && (
                    <button
                      type="button"
                      className="btn-ghost w-full py-2.5"
                      onClick={() => window.location.assign("/api/auth/oauth/google/start")}
                    >
                      <GoogleMark />
                      使用 Google 登录
                    </button>
                  )}
                  {providers?.github && (
                    <button
                      type="button"
                      className="btn-ghost w-full py-2.5"
                      onClick={() => window.location.assign("/api/auth/oauth/github/start")}
                    >
                      <GithubMark />
                      使用 GitHub 登录
                    </button>
                  )}
                </div>
              </>
            )}
          </div>

          <p className="mt-5 text-center text-[12.5px] text-muted">
            还没有账号？
            <Link href="/register" className="ml-1 font-medium text-accent hover:underline">
              用邀请码注册
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
