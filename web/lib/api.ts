export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Botly-Client": "web",
      ...(init.headers || {}),
    },
    credentials: "same-origin",
  });
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
  tools: () => req<{ name: string; description: string; level: string }[]>("/bots/tools"),
  templates: () => req<import("./types").BotTemplate[]>("/bots/templates"),

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
  send: (chatId: string, content: string, mention_bot_id?: string) =>
    req<{ message: import("./types").Message; run: import("./types").Run }>(
      `/chats/${chatId}/messages`,
      { method: "POST", body: JSON.stringify({ content, mention_bot_id }) }
    ),
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

  // 通知
  notifications: () => req<{ items: import("./types").Notification[]; unread_count: number }>("/notifications"),
  readNotifications: (body: { ids?: string[]; all?: boolean }) =>
    req<{ ok: boolean; count: number }>("/notifications/read", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 设置
  settings: () => req<any>("/settings"),
  patchSettings: (body: any) => req<any>("/settings", { method: "PATCH", body: JSON.stringify(body) }),

  // 用量与费用
  usage: (days = 30) =>
    req<import("./types").UsageSummary>(`/usage/summary?days=${days}`),
};
