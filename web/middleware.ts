import { NextRequest, NextResponse } from "next/server";

/**
 * 登录门禁：在服务端渲染之前就检查会话 cookie，
 * 未登录访问受保护页面（含 `/`）直接 302 到 /login，
 * 修复"先闪现 chat 页、等 401 再跳登录"的问题。
 *
 * 注意：这里只判断 cookie 是否存在（无法校验 epoch/签名），
 * cookie 过期或失效时仍由 lib/api.ts 的 401 处理兜底跳转。
 */
const SESSION_COOKIE = "botly_session";

// 无需登录的路径：认证流程页 + 静态资源 + API（API 由后端自己返回 401）
const PUBLIC_PREFIXES = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/verify-email",
  "/_next",
  "/favicon",
  "/api",
];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }
  const hasSession = Boolean(req.cookies.get(SESSION_COOKIE)?.value);
  if (hasSession) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = "/login";
  // 登录成功后回到原目标；只保留路径本身，避免把未知 query 带进登录页
  const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
  url.search = next;
  return NextResponse.redirect(url);
}

export const config = {
  // 排除静态文件
  matcher: ["/((?!.*\\.).*)"],
};
