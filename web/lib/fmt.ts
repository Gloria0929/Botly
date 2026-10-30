export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function num(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function countdown(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const ms = d.getTime() - Date.now();
  if (ms <= 0) return "已过期";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h >= 1) return `${h} 小时后过期`;
  return `${m} 分钟后过期`;
}

/** MOD-02：单项能力的三态文案。 */
export function capLabel(v: boolean | null | undefined, name: string): string {
  return v === true ? `支持${name}` : v === false ? `不支持${name}` : `${name}未知`;
}

/**
 * MOD-02：模型能力的合并文案。
 * `bad` = 探测到明确不可用（会拦总指挥 / 执行必然失败）；
 * `unknown` = 还没检测过，不做拦截。
 */
export function modelCapability(
  m: { streaming?: boolean | null; tool_calling?: boolean | null } | null | undefined
): { text: string; bad: boolean; unknown: boolean } {
  if (m) {
    if (m.streaming === false) return { text: "不支持流式输出", bad: true, unknown: false };
    if (m.tool_calling === false) return { text: "不支持工具调用", bad: true, unknown: false };
    if (m.streaming === true && m.tool_calling === true) {
      return { text: "支持流式与工具调用", bad: false, unknown: false };
    }
    if (m.tool_calling === true || m.streaming === true) {
      return { text: capLabel(m.tool_calling, "工具调用"), bad: false, unknown: false };
    }
  }
  return { text: "未检测能力", bad: false, unknown: true };
}

