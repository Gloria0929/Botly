import type { NextConfig } from "next";

const API = process.env.BOTLY_API_URL || "http://127.0.0.1:8100";

const nextConfig: NextConfig = {
  // 关闭开发模式右下角的 Next 浮标（Route / Turbopack / Preferences 面板）
  devIndicators: false,
  // 前端与 API 同源：浏览器只访问 3100，由 Next 转发到 FastAPI，
  // 避免 Cookie 跨域与 SSE 被缓冲（对应需求 9.1 落地约束）
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
