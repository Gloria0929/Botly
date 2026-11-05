import type { NextConfig } from "next";

const API = process.env.BOTLY_API_URL || "http://127.0.0.1:8100";

const nextConfig: NextConfig = {
  // 开发服务和生产构建使用独立目录，构建校验不会覆盖正在提供的 CSS/JS。
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  async headers() {
    const development = process.env.NODE_ENV === "development";
    const policy = [
      "default-src 'self'", `script-src 'self' 'unsafe-inline'${development ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
      `connect-src 'self'${development ? " ws: wss:" : ""}`,
      "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'",
    ].join("; ");
    // 静态预览由 API 设置专用 CSP，允许嵌入但禁止访问宿主会话。
    return [{ source: "/((?!api/preview/).*)", headers: [
      { key: "Content-Security-Policy", value: policy },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "no-referrer" },
    ] }];
  },
  // 关闭开发模式右下角的 Next 浮标（Route / Turbopack / Preferences 面板）
  devIndicators: false,
  // 前端与 API 同源：浏览器只访问 3100，由 Next 转发到 FastAPI，
  // 避免 Cookie 跨域与 SSE 被缓冲（对应需求 9.1 落地约束）
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
