"use client";

import Link from "next/link";
import { LogoMark } from "@/components/ui/Logo";

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
    <div className="relative flex min-h-screen items-center justify-center px-5 py-12">
      <div className="w-full max-w-[400px] animate-rise">
        <Link href="/login" className="mb-6 flex items-center gap-2.5">
          <LogoMark size={30} />
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.08em]">Botly</span>
        </Link>
        <div className="card p-6 md:p-7">
          <div className="page-eyebrow">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            ACCOUNT
          </div>
          <h1 className="text-[24px] font-bold tracking-[-0.03em]">{title}</h1>
          {subtitle && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">{subtitle}</p>
          )}
          <div className="mt-6">{children}</div>
        </div>
        {footnote && (
          <div className="mt-5 px-1 text-[11.5px] leading-relaxed text-faint">{footnote}</div>
        )}
      </div>
    </div>
  );
}
