"use client";

import Link from "next/link";

/**
 * 登录之外的认证页面（找回密码、重置密码、验证邮箱）共用的外壳。
 * 这些是一次性流程，不需要登录/注册页那块营销左栏，保持克制的一列布局。
 */
export default function AuthShell({
  title,
  subtitle,
  children,
  footnote,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footnote?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6 py-12">
      <div className="w-full max-w-[380px]">
        <Link href="/login" className="mb-10 flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-[14px] font-bold text-accentink">
            B
          </div>
          <span className="text-[15px] font-semibold tracking-tight">Botly</span>
        </Link>
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{subtitle}</p>}
        <div className="mt-7">{children}</div>
        {footnote && <div className="mt-6 text-[12px] leading-relaxed text-faint">{footnote}</div>}
      </div>
    </div>
  );
}
