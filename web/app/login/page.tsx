"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Eye, EyeOff, Loader2, LockKeyhole } from "lucide-react";
import { GithubMark, GoogleMark } from "@/components/ui/BrandIcons";
import AuthMarketing from "@/components/AuthMarketing";
import { LogoMark } from "@/components/ui/Logo";
import { api, errText } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
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
    if (loading) return;
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
    <div className="auth-entry grid min-h-dvh lg:grid-cols-[minmax(0,1.06fr)_minmax(0,1fr)]">
      <AuthMarketing
        title={
          <>
            一个目标，
            <br />
            一支 AI 团队。
          </>
        }
        description="把繁复的工作交给团队。从拆解任务到汇总结果，每一步都有分工，每一步都清晰可见。"
      />

      <main className="flex min-w-0 flex-col px-6 py-7 sm:px-10 lg:px-8 lg:py-5 xl:px-16">
        <header className="flex min-h-10 items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 lg:invisible"><LogoMark size={32} /><span className="text-[22px] font-semibold tracking-[-0.05em]">Botly</span></div>
          {providers?.signup !== false && <Link href="/register" className="flex items-center gap-1.5 text-[12px] text-muted transition-colors hover:text-ink">创建账号<ArrowRight size={13} /></Link>}
        </header>
        <div className="flex flex-1 items-center justify-center py-12 sm:py-16 lg:py-10">
        <form onSubmit={submit} className="w-full max-w-[380px] animate-rise" aria-labelledby="login-heading">
          <div>
            <p className="mb-3 text-[11px] font-medium tracking-[0.12em] text-faint">YOUR WORKSPACE, AWAITS.</p>
            <h2 id="login-heading" className="text-[32px] font-semibold tracking-[-0.04em] sm:text-[36px]">欢迎回来</h2>
            <p className="mt-3 text-[14px] leading-relaxed text-muted">登录 Botly，继续和你的团队一起工作。</p>

            <div className="mt-9 space-y-5">
              <div>
                <label htmlFor="login-email" className="label mb-2 text-[13px] text-ink">邮箱地址</label>
                <input
                  id="login-email"
                  className="field h-12 bg-panel2/50"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                />
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3"><label htmlFor="login-password" className="label mb-0 text-[13px] text-ink">密码</label><Link href="/forgot-password" className="text-[12px] text-muted hover:text-ink hover:underline">忘记密码？</Link></div>
                <div className="relative">
                  <input id="login-password" className="field h-12 bg-panel2/50 pr-12" type={showPassword ? "text" : "password"} required value={password} onChange={e => setPassword(e.target.value)} placeholder="输入你的密码" autoComplete="current-password" />
                  <button type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)} className="icon-btn absolute inset-y-0 right-2 my-auto">{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                </div>
              </div>
            </div>

            {resetDone && !err && (
              <div role="status" className="mt-4 rounded-lg border border-ok/35 bg-ok/10 px-3 py-2 text-[12px] text-ok">
                密码已重置，请用新密码登录。
              </div>
            )}

            {(oauthError || err) && (
              <div role="alert" className="mt-4 rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12px] text-danger">
                {oauthError || err}
              </div>
            )}

            <button type="submit" className="btn-primary mt-7 h-12 w-full text-[14px]" disabled={loading}>
              {loading ? <><Loader2 size={16} className="animate-spin" />正在登录…</> : <>进入工作台 <ArrowRight size={16} className="ml-1" /></>}
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

          {providers?.signup !== false && <p className="mt-7 text-center text-[12px] text-muted">
            还没有账号？
            <Link href="/register" className="ml-1 font-medium text-accent hover:underline">
              用邀请码注册
            </Link>
          </p>}
        </form>
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-3 text-[10px] text-faint"><span>© {new Date().getFullYear()} Botly</span><span className="flex items-center gap-1.5"><LockKeyhole size={11} />你的工作空间，由你掌控</span></footer>
      </main>
    </div>
  );
}
