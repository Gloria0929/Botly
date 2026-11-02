"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import AuthShell from "@/components/AuthShell";
import { api, errText } from "@/lib/api";

/** ACC-08：用邮件里的令牌设置新密码。 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [form, setForm] = useState({ next: "", confirm: "" });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  // 用 window.location 读参数：不必给整页套 Suspense
  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") || "");
    setReady(true);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (!token) return;
    if (form.next.length < 8) {
      setErr("密码至少 8 位");
      return;
    }
    if (form.next !== form.confirm) {
      setErr("两次输入的密码不一致");
      return;
    }
    setLoading(true);
    try {
      await api.resetPassword(token, form.next);
      router.push("/login?reset=1");
    } catch (e: any) {
      setErr(errText(e, "重置失败"));
    } finally {
      setLoading(false);
    }
  };

  if (!ready) {
    return (
      <AuthShell title="设置新密码">
        <Loader2 size={16} className="animate-spin text-faint" />
      </AuthShell>
    );
  }

  if (!token) {
    return (
      <AuthShell title="链接无效" subtitle="缺少重置令牌，可能是链接被截断了，请重新申请一封邮件。">
        <Link href="/forgot-password" className="btn-primary w-full py-2.5">
          重新申请重置链接
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="设置新密码"
      subtitle="设置后，其他设备上的登录状态会立即失效，需要用新密码重新登录。"
      footnote="想不起来为什么会收到这封邮件？直接关闭页面即可，密码不会被修改。"
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label className="label">新密码</label>
          <input
            className="field"
            type="password"
            required
            minLength={8}
            value={form.next}
            onChange={(e) => setForm({ ...form, next: e.target.value })}
            placeholder="至少 8 位"
            autoComplete="new-password"
            autoFocus
          />
        </div>
        <div>
          <label className="label">再输一次</label>
          <input
            className="field"
            type="password"
            required
            minLength={8}
            value={form.confirm}
            onChange={(e) => setForm({ ...form, confirm: e.target.value })}
            placeholder="确认新密码"
            autoComplete="new-password"
          />
        </div>

        {err && (
          <div className="rounded-lg border border-danger/35 bg-danger/10 px-3 py-2 text-[12.5px] text-danger">
            {err}
          </div>
        )}

        <button className="btn-primary w-full py-2.5" disabled={loading}>
          {loading ? <Loader2 size={15} className="animate-spin" /> : <>设置新密码 <ArrowRight size={15} /></>}
        </button>
      </form>

      <p className="mt-5 text-center text-[12.5px] text-muted">
        链接过期了？
        <Link href="/forgot-password" className="ml-1 text-accent hover:underline">
          重新申请
        </Link>
      </p>
    </AuthShell>
  );
}
