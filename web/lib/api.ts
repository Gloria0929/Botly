import { toast } from "./ui";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData 由浏览器自己带 boundary，不能手动指定 Content-Type
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: {
        ...(isForm ? {} : { "Content-Type": "application/json" }),
        "X-Botly-Client": "web",
        ...(init.headers || {}),
      },
      credentials: "same-origin",
    });
  } catch (e) {
    const message = "网络请求失败，请检查连接或稍后重试";
    toast(message, "error");
    throw new ApiError(0, message);
  }
  if (res.status === 401) {
    if (typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    throw new ApiError(401, "未登录");
  }
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) {
        detail = data.detail
          .map((d: any) => (typeof d === "string" ? d : d?.msg || ""))
          .filter(Boolean)
          .join("；") || detail;
      }
    } catch {
      /* ignore */
    }
    toast(detail, "error");
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  // 账号
  me: () => req<import("./types").Me>("/auth/me"),
  login: (email: string, password: string) =>
    req<{ id: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  register: (body: { email: string; password: string; invite_code: string; name?: string }) =>
    req<{ id: string; chat_id: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    req("/auth/password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }),
  /** ACC-08：申请重置密码。不区分邮箱是否存在，避免被用来枚举账号 */
  forgotPassword: (email: string) =>
    req<{ ok: boolean; dev_link?: string }>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  /** ACC-08：用邮件里的令牌设置新密码（成功后所有旧会话失效） */
  resetPassword: (token: string, password: string) =>
    req<{ ok: boolean; email: string }>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),
  /** ACC-08：给当前账号发验证邮件 */
  sendVerification: () =>
    req<{ ok: boolean; already: boolean; email: string; dev_link?: string }>(
      "/auth/send-verification",
      { method: "POST" }
    ),
  /** ACC-08：消费验证邮件里的令牌 */
  verifyEmail: (token: string) =>
    req<{ ok: boolean; email: string }>("/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  /** 第三方登录：哪些 provider 配好了（没配的不显示按钮） */
  oauthProviders: () =>
    req<{ google: boolean; github: boolean; signup: boolean }>("/auth/oauth/providers"),
  /** 当前账号绑定了哪些第三方账号 */
  oauthAccounts: () =>
    req<{
      available: { google: boolean; github: boolean };
      redirect_uri: Record<string, string>;
      linked: Record<
        string,
        { name?: string | null; email?: string | null; avatar?: string | null; last_login_at?: string | null }
      >;
    }>("/auth/oauth/accounts"),
  /** 解绑第三方账号 */
  unbindOauth: (provider: string) =>
    req<{ ok: boolean; remaining: number }>(`/auth/oauth/${provider}`, { method: "DELETE" }),
  /** ACC-07：删除账号（立即停用，30 天后彻底清除） */
  deleteAccount: (password: string, confirm_email: string) =>
    req<{ ok: boolean; purge_after: string | null; retention_days: number }>("/auth/account", {
      method: "DELETE",
      body: JSON.stringify({ password, confirm_email }),
    }),

  // 图片上传（头像 / 厂商标识）
  uploadImage: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/uploads", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "X-Botly-Client": "web" },
    });
    if (!res.ok) {
      let msg = "上传失败";
      try {
        const d = await res.json();
        if (typeof d.detail === "string") msg = d.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, msg);
    }
    return (await res.json()) as { id: string; url: string };
  },

  // 模型接入
  providers: () => req<import("./types").Provider[]>("/providers"),
  vendors: () => req<import("./types").Vendor[]>("/providers/vendors"),
  createProvider: (body: any) =>
    req<{ provider: import("./types").Provider; detected: boolean; message: string }>("/providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  refreshProvider: (id: string) =>
    req<{ provider: import("./types").Provider; detected: boolean; message: string }>(
      `/providers/${id}/refresh`,
      { method: "POST" }
    ),
  patchProvider: (id: string, body: any) =>
    req<import("./types").Provider>(`/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProvider: (id: string, force = false) =>
    req(`/providers/${id}?force=${force}`, { method: "DELETE" }),
  testProvider: (id: string) =>
    req<{
      ok: boolean;
      message: string;
      model: string;
      streaming: boolean | null;
      tool_calling: boolean | null;
      provider: import("./types").Provider;
    }>(`/providers/${id}/test`, {
      method: "POST",
    }),
  /** MOD-02：批量检测模型能力（不传 models 时检测前 12 个） */
  probeProvider: (id: string, models?: string[]) =>
    req<{
      results: {
        model: string;
        ok: boolean;
        message: string;
        streaming: boolean | null;
        tool_calling: boolean | null;
      }[];
      provider: import("./types").Provider;
      message: string;
    }>(`/providers/${id}/probe`, {
      method: "POST",
      body: JSON.stringify(models ? { models } : {}),
    }),

  // Bot
  bots: () => req<import("./types").Bot[]>("/bots"),
  createBot: (body: any) =>
    req<import("./types").Bot>("/bots", { method: "POST", body: JSON.stringify(body) }),
  patchBot: (id: string, body: any) =>
    req<import("./types").Bot>(`/bots/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBot: (id: string) => req(`/bots/${id}`, { method: "DELETE" }),
  tools: () =>
    req<
      {
        name: string;
        description: string;
        level: string;
        available?: boolean;
        reason?: string | null;
        source?: "builtin" | "mcp";
      }[]
    >("/bots/tools"),
  templates: () => req<import("./types").BotTemplate[]>("/bots/templates"),

  // 技能
  skills: () => req<import("./types").Skill[]>("/skills"),
  createSkill: (body: any) =>
    req<import("./types").Skill>("/skills", { method: "POST", body: JSON.stringify(body) }),
  patchSkill: (id: string, body: any) =>
    req<import("./types").Skill>(`/skills/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSkill: (id: string) => req(`/skills/${id}`, { method: "DELETE" }),
  importSkillFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<import("./types").Skill>("/skills/import", { method: "POST", body: form });
  },
  importSkillUrl: (url: string) =>
    req<import("./types").Skill>("/skills/import-url", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  importSkillFromClawhub: (slug: string) =>
    req<import("./types").Skill>("/skills/import-clawhub", {
      method: "POST",
      body: JSON.stringify({ slug }),
    }),
  importSkillFromGithub: (repo: string) =>
    req<import("./types").Skill>("/skills/import-github", {
      method: "POST",
      body: JSON.stringify({ repo }),
    }),

  // TOL-05：MCP 服务
  mcpServers: () => req<import("./types").McpServer[]>("/mcp/servers"),
  createMcpServer: (body: any) =>
    req<import("./types").McpServer>("/mcp/servers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchMcpServer: (id: string, body: any) =>
    req<import("./types").McpServer>(`/mcp/servers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  testMcpServer: (id: string) =>
    req<{ ok: boolean; status: string; error: string | null; tools: any[] }>(
      `/mcp/servers/${id}/test`,
      { method: "POST" }
    ),
  deleteMcpServer: (id: string) => req(`/mcp/servers/${id}`, { method: "DELETE" }),
  importMcpServers: (body: any) =>
    req<{ created: any[]; skipped: { name: string; reason: string }[]; errors: { name: string; reason: string }[] }>(
      "/mcp/import",
      { method: "POST", body: JSON.stringify(body) }
    ),

  // 群聊
  chats: () => req<import("./types").ChatSummary[]>("/chats"),
  chat: (id: string) => req<import("./types").Chat>(`/chats/${id}`),
  task: (id: string) => req<import("./types").Task>(`/tasks/${id}`),
  createChat: (body: any) =>
    req<import("./types").Chat>("/chats", { method: "POST", body: JSON.stringify(body) }),
  patchChat: (id: string, body: any) =>
    req<import("./types").Chat>(`/chats/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  addMembers: (chatId: string, botIds: string[]) =>
    req<import("./types").Chat>(`/chats/${chatId}/members`, {
      method: "POST",
      body: JSON.stringify({ bot_ids: botIds }),
    }),
  removeMember: (chatId: string, botId: string) =>
    req<import("./types").Chat>(`/chats/${chatId}/members/${botId}`, { method: "DELETE" }),
  deleteChat: (id: string) => req(`/chats/${id}`, { method: "DELETE" }),
  uploadChatFile: async (chatId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`/api/chats/${chatId}/files`, {
      method: "POST", credentials: "same-origin", headers: { "X-Botly-Client": "web" }, body,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(response.status, typeof data.detail === "string" ? data.detail : "文件上传失败");
    }
    return await response.json() as import("./types").ChatFile;
  },
  messages: (chatId: string, params: { before?: number; limit?: number; thread_id?: string; around?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.before) q.set("before", String(params.before));
    if (params.limit) q.set("limit", String(params.limit));
    if (params.thread_id) q.set("thread_id", params.thread_id);
    if (params.around) q.set("around", params.around);
    return req<{ messages: import("./types").Message[]; has_more: boolean; event_cursor: number }>(
      `/chats/${chatId}/messages?${q.toString()}`
    );
  },
  messageTarget: (chatId: string, kind: string, targetId: string) =>
    req<{ message_id: string; task_id: string | null }>(`/chats/${chatId}/message-target?${new URLSearchParams({ kind, target_id: targetId })}`),
  /** CHT-08：在当前群聊主线程里按正文搜索 */
  searchMessages: (chatId: string, q: string, params: { before?: number; limit?: number } = {}) => {
    const s = new URLSearchParams({ q });
    if (params.before) s.set("before", String(params.before));
    if (params.limit) s.set("limit", String(params.limit));
    return req<{
      results: import("./types").SearchHit[];
      total: number;
      has_more: boolean;
      query: string;
    }>(`/chats/${chatId}/messages/search?${s.toString()}`);
  },
  send: (chatId: string, content: string, mention_bot_id?: string, thread_id?: string) =>
    req<{ message: import("./types").Message; run: import("./types").Run }>(
      `/chats/${chatId}/messages`,
      { method: "POST", body: JSON.stringify({ content, mention_bot_id, thread_id }) }
    ),
  /** CHT-07：从主线程的某条消息拉出分支 */
  branches: (chatId: string) =>
    req<import("./types").Branch[]>(`/chats/${chatId}/branches`),
  createBranch: (chatId: string, message_id: string, title?: string) =>
    req<import("./types").Branch>(`/chats/${chatId}/branches`, {
      method: "POST",
      body: JSON.stringify({ message_id, title }),
    }),
  deleteBranch: (chatId: string, threadId: string) =>
    req(`/chats/${chatId}/branches/${threadId}`, { method: "DELETE" }),
  /** RUN-13：执行中补充，不新建执行 */
  interject: (chatId: string, content: string) =>
    req<{ message: import("./types").Message; run: import("./types").Run; interjection: boolean }>(
      `/chats/${chatId}/messages`,
      { method: "POST", body: JSON.stringify({ content, interject: true }) }
    ),
  chatFiles: (chatId: string) => req<import("./types").ChatFile[]>(`/chats/${chatId}/files`),
  /** 15.2：导出主线程为 Markdown，返回 Blob 供前端触发下载 */
  exportChat: async (chatId: string) => {
    const res = await fetch(`/api/chats/${chatId}/export`, {
      headers: { "X-Botly-Client": "web" },
      credentials: "same-origin",
    });
    if (!res.ok) {
      let msg = "导出失败";
      try {
        const data = await res.json();
        if (typeof data.detail === "string") msg = data.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, msg);
    }
    return await res.blob();
  },
  fileContent: async (fileId: string) => {
    const res = await fetch(`/api/files/${fileId}/content`, {
      headers: { "X-Botly-Client": "web" },
      credentials: "same-origin",
    });
    if (!res.ok) throw new ApiError(res.status, "无法读取文件");
    return res.text();
  },

  // 日志（只读）
  logs: (params: Record<string, string | number | undefined> = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const suffix = qs.toString() ? `?${qs}` : "";
    return req<import("./types").LogPage>(`/logs${suffix}`);
  },
  logModules: () => req<import("./types").LogModule[]>("/logs/modules"),

  // 运行状况看板（8.6）
  statsOverview: (days = 7) =>
    req<import("./types").StatsOverview>(`/stats/overview?days=${days}`),
  acknowledgeAlert: (id: string) =>
    req<{ ok: boolean }>(`/stats/alerts/${id}/acknowledge`, { method: "POST" }),

  // 执行
  run: (id: string) => req<import("./types").Run>(`/runs/${id}`),
  stop: (id: string) => req<import("./types").Run>(`/runs/${id}/stop`, { method: "POST" }),
  cont: (id: string, note?: string) =>
    req<import("./types").Run>(`/runs/${id}/continue`, {
      method: "POST",
      body: JSON.stringify({ note: note || "" }),
    }),
  approvePlan: (id: string, steps?: any[]) =>
    req<import("./types").Run>(`/runs/${id}/plan/approve`, {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),
  cancelPlan: (id: string) => req<import("./types").Run>(`/runs/${id}/plan/cancel`, { method: "POST" }),
  approvals: () => req<any[]>("/approvals"),
  approve: (id: string, alwaysAllow = false) =>
    req<import("./types").Run>(`/approvals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ always_allow: alwaysAllow }),
    }),
  reject: (id: string, reason?: string) =>
    req<import("./types").Run>(`/approvals/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  answer: (id: string, answer: string) =>
    req<import("./types").Run>(`/questions/${id}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),

  // 记忆
  memories: (params: { scope?: string; bot_id?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.scope) q.set("scope", params.scope);
    if (params.bot_id) q.set("bot_id", params.bot_id);
    return req<{ memories: import("./types").Memory[]; injected_length: number; total_length: number; truncated: boolean; limit: number }>(
      `/memories?${q.toString()}`
    );
  },
  memoryStats: () =>
    req<{ limit: number; bots: any[] }>("/memories/stats"),
  createMemory: (body: any) =>
    req<import("./types").Memory>("/memories", { method: "POST", body: JSON.stringify(body) }),
  patchMemory: (id: string, body: any) =>
    req<import("./types").Memory>(`/memories/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMemory: (id: string) => req(`/memories/${id}`, { method: "DELETE" }),
  /** MEM-04：执行结束后自动生成的记忆候选 */
  memoryCandidates: () =>
    req<{ candidates: import("./types").MemoryCandidate[] }>("/memories/candidates"),
  acceptMemoryCandidate: (id: string) =>
    req<import("./types").Memory>(`/memories/candidates/${id}/accept`, { method: "POST" }),
  rejectMemoryCandidate: (id: string) =>
    req(`/memories/candidates/${id}/reject`, { method: "POST" }),

  // 通知
  notifications: () => req<{ items: import("./types").Notification[]; unread_count: number }>("/notifications"),
  readNotifications: (body: { ids?: string[]; all?: boolean }) =>
    req<{ ok: boolean; count: number }>("/notifications/read", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  clearNotifications: () =>
    req<{ ok: boolean; count: number }>("/notifications", { method: "DELETE" }),
  /** NTF-06：Web Push 配置与订阅管理 */
  pushConfig: () => req<{ enabled: boolean; public_key: string }>("/push/config"),
  pushSubscriptions: () =>
    req<{ items: { id: string; endpoint: string; user_agent: string; created_at: string | null }[] }>(
      "/push/subscriptions"
    ),
  pushSubscribe: (body: { endpoint: string; keys: { p256dh: string; auth: string }; user_agent?: string }) =>
    req<{ ok: boolean }>("/push/subscriptions", { method: "POST", body: JSON.stringify(body) }),
  pushUnsubscribe: (endpoint: string) =>
    req<{ ok: boolean }>("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
  pushTest: () => req<{ ok: boolean; id: string }>("/push/test", { method: "POST" }),

  // 设置
  settings: () => req<any>("/settings"),
  patchSettings: (body: any) => req<any>("/settings", { method: "PATCH", body: JSON.stringify(body) }),

  // 用量与费用
  usage: (days = 30) =>
    req<import("./types").UsageSummary>(`/usage/summary?days=${days}`),
};
