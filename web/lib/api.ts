import { toast } from "./ui";

export const NOTIFICATIONS_CHANGED_EVENT = "botly:notifications-changed";
export type NotificationCountChange = { unreadCount?: number; readCount?: number };

function notifyNotificationChange(detail: NotificationCountChange) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent<NotificationCountChange>(NOTIFICATIONS_CHANGED_EVENT, { detail }));
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI 的错误体：detail 可能是字符串，也可能是校验错误数组。 */
export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail.trim();
  if (Array.isArray(detail)) {
    return detail
      .map(detailText)
      .filter(Boolean)
      .join("；");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    for (const key of ["detail", "msg", "message"]) {
      const text = detailText(obj[key]);
      if (text) return text;
    }
  }
  return "";
}

/** 统一取错误文案：优先后端 detail，其次 Error.message，最后兜底。 */
export function errText(e: unknown, fallback = "操作失败，请稍后重试"): string {
  if (typeof e === "string" && e.trim()) return e.trim();
  const error = e as { detail?: unknown; message?: unknown } | null | undefined;
  const detail = detailText(error?.detail);
  if (detail) return detail;
  const message = error?.message;
  if (typeof message === "string" && message.trim()) {
    if (e instanceof ApiError) return message.trim();
    // 浏览器把网络层的失败暴露成英文 TypeError，对用户没意义
    if (/^(Failed to fetch|Load failed|NetworkError)/i.test(message)) {
      return "网络请求失败，请检查连接或稍后重试";
    }
    return message.trim();
  }
  return fallback;
}

/** 错误提示统一出口：任何用户触发的失败都要能说清原因，不能只留一个空 toast。 */
export function toastError(e: unknown, fallback?: string) {
  toast(errText(e, fallback), "error");
}

/** 从响应里读 detail；不是 JSON 或没有 detail 时用兜底文案。 */
async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return detailText(data?.detail) || fallback;
  } catch {
    return fallback;
  }
}

/** JSON、上传和下载共用同一套错误解析与登录失效处理。 */
async function request(path: string, init: RequestInit = {}, fallback?: string, silent = false): Promise<Response> {
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
  } catch {
    const message = "网络请求失败，请检查连接或稍后重试";
    if (!silent) toast(message, "error");
    throw new ApiError(0, message);
  }
  if (!res.ok) {
    // 所有状态码（含 401）都先读取 detail，不能把账号密码错误覆盖成“未登录”。
    const message = await readError(res, fallback || (
      res.status === 401
        ? "未登录，请重新登录"
        : res.status >= 500
        ? `服务暂时不可用（${res.status}），请确认后端已启动后重试`
        : `请求失败（${res.status}）`
    ));
    if (!silent) toast(message, "error");
    if (res.status === 401 && path !== "/auth/login" && typeof window !== "undefined") {
      const publicPages = ["/login", "/register", "/forgot-password", "/reset-password", "/verify-email"];
      if (!publicPages.some((page) => location.pathname === page || location.pathname.startsWith(`${page}/`))) {
        location.href = "/login";
      }
    }
    throw new ApiError(res.status, message);
  }
  return res;
}

async function req<T>(path: string, init: RequestInit = {}, silent = false): Promise<T> {
  const res = await request(path, init, undefined, silent);
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
    const res = await request("/uploads", {
      method: "POST",
      body: fd,
    }, "上传失败");
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
  deleteSkill: (id: string) => req<{ ok: boolean; installation?: import("./types").Skill["installation"] }>(`/skills/${id}`, { method: "DELETE" }),
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
  chatTemplates: () => req<import("./types").ChatTemplate[]>("/chats/templates"),
  createChatFromTemplate: (template_id: string, mode: "task" | "free" = "task") =>
    req<import("./types").Chat & { created_bot_ids: string[]; model_setup_required: boolean }>(
      "/chats/from-template", { method: "POST", body: JSON.stringify({ template_id, mode }) }
    ),
  chat: (id: string) => req<import("./types").Chat>(`/chats/${id}`),
  task: (id: string) => req<import("./types").Task>(`/tasks/${id}`),
  retryTask: (chatId: string, taskId: string, note = "") =>
    req<{ message: import("./types").Message; run: import("./types").Run; task: import("./types").Task }>(
      `/chats/${chatId}/tasks/${taskId}/retry`, { method: "POST", body: JSON.stringify({ note }) }
    ),
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
    const response = await request(`/chats/${chatId}/files`, {
      method: "POST", body,
    }, "文件上传失败");
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
  send: (chatId: string, content: string, mention_bot_id?: string, thread_id?: string, reply_to?: string) =>
    req<{ message: import("./types").Message; run: import("./types").Run | null }>(
      `/chats/${chatId}/messages`,
      { method: "POST", body: JSON.stringify({ content, mention_bot_id, thread_id, reply_to }) }
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
    const res = await request(`/chats/${chatId}/export`, {}, "导出失败");
    return await res.blob();
  },
  fileContent: async (fileId: string) => {
    const res = await request(`/files/${fileId}/content`, {}, "无法读取文件");
    return res.text();
  },
  fileSite: (fileId: string) => req<{ url: string; expires_in: number; backend: { port: number; manifest: string } | null }>(`/files/${fileId}/site`),
  fileBackend: (fileId: string, action: "start" | "stop" | "status" = "start", signal?: AbortSignal) =>
    req<import("./types").PreviewBackendStatus>(`/files/${fileId}/backend`, {
      method: "POST", body: JSON.stringify({ action }), signal,
    }, !!signal),
  deleteFile: (fileId: string) => req<{ ok: boolean }>(`/files/${fileId}`, { method: "DELETE" }),

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

  // 状态看板（8.6）
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
  memories: (params: { scope?: string; bot_id?: string; q?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.scope) q.set("scope", params.scope);
    if (params.bot_id) q.set("bot_id", params.bot_id);
    if (params.q) q.set("q", params.q);
    return req<{ memories: import("./types").Memory[]; injected_length: number; total_length: number; truncated: boolean; limit: number }>(
      `/memories?${q.toString()}`
    );
  },
  memoryStats: () =>
    req<{ limit: number; bots: any[] }>("/memories/stats"),
  /** 疑似重复记忆对，供整理合并 */
  memoryDuplicates: (params: { scope?: string; bot_id?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.scope) q.set("scope", params.scope);
    if (params.bot_id) q.set("bot_id", params.bot_id);
    return req<{ duplicates: { a: import("./types").Memory; b: import("./types").Memory; score: number }[]; count: number }>(
      `/memories/duplicates?${q.toString()}`
    );
  },
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
  readNotifications: async (body: { ids?: string[]; all?: boolean }) => {
    const result = await req<{ ok: boolean; count: number }>("/notifications/read", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (result.ok) notifyNotificationChange(body.all ? { unreadCount: 0 } : { readCount: result.count });
    return result;
  },
  clearNotifications: async () => {
    const result = await req<{ ok: boolean; count: number }>("/notifications", { method: "DELETE" });
    if (result.ok) notifyNotificationChange({ unreadCount: 0 });
    return result;
  },
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
  sandbox: () => req<import("./types").SandboxStatus>("/sandbox"),
  cubeTemplates: (body: { api_url: string; api_key?: string }, signal?: AbortSignal) =>
    req<{ templates: import("./types").CubeTemplate[] }>("/sandbox/cube/templates", { method: "POST", body: JSON.stringify(body), signal }, true),
  terminal: (chatId: string) => req<import("./types").TerminalHistory>(`/chats/${chatId}/terminal`),
  terminalCommand: (chatId: string, command: string) => req<import("./types").TerminalEntry>(`/chats/${chatId}/terminal`, { method: "POST", body: JSON.stringify({ command }) }),
  saveCube: (body: any) => req<import("./types").SandboxStatus>("/sandbox/cube", { method: "PUT", body: JSON.stringify(body) }),
  testCube: (body: any) => req<{ ok: boolean; message: string; sandbox?: import("./types").SandboxStatus }>("/sandbox/cube/test", { method: "POST", body: JSON.stringify(body) }),
  disconnectCube: () => req<{ ok: boolean }>("/sandbox/cube", { method: "DELETE" }),

  // 用量与费用
  usage: (days = 30) =>
    req<import("./types").UsageSummary>(`/usage/summary?days=${days}`),
};
