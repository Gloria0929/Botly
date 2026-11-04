export type Bot = {
  id: string;
  name: string;
  avatar: string;
  role: string;
  system_prompt: string;
  provider_id: string | null;
  model: string | null;
  temperature: number;
  tools: string[];
  skills?: string[];
  enabled?: boolean;
  deleted: boolean;
  display_name: string;
  status?: "idle" | "working" | "waiting";
};

export type BotTemplate = {
  id: string;
  name: string;
  avatar: string;
  role: string;
  description: string;
  system_prompt: string;
  tools: string[];
  is_supervisor_template: boolean;
};

/** 技能：可复用的提示词 + 工具包，绑定到 Bot 后生效。 */
export type Skill = {
  id: string;
  name: string;
  description: string;
  prompt: string;
  tools: string[];
  enabled: boolean;
  bot_count?: number;
  source?: "manual" | "url" | "zip" | "github" | "clawhub" | string;
  source_url?: string | null;
  files?: string[];
  sandbox_path?: string;
  installation?: {
    provider: string;
    synced: number;
    pending: boolean;
    errors: { chat_id?: string; message: string }[];
  };
  created_at?: string;
};

/** TOL-05：用户接入的 MCP 服务。 */
export type McpServer = {
  id: string;
  name: string;
  slug: string;
  transport: "http" | "stdio" | string;
  url: string;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  level: "L1" | "L2" | "L3" | string;
  enabled: boolean;
  status: "unchecked" | "ok" | "error";
  last_error: string | null;
  last_checked_at: string | null;
  token_last4: string | null;
  tool_count: number;
  tools: { name: string; remote_name: string; description: string }[];
};

/** MOD-02：模型能力探测结果。三态——true 支持 / false 不支持 / null 未探测。 */
export type ModelInfo = {
  name: string;
  input_price?: number | null;
  output_price?: number | null;
  streaming?: boolean | null;
  tool_calling?: boolean | null;
  checked_at?: string | null;
};

export type Provider = {
  id: string;
  name: string;
  kind: "openai" | "mock";
  vendor_id: string | null;
  vendor_name: string;
  logo: string;
  color: string;
  base_url: string;
  api_key_last4: string | null;
  models: ModelInfo[];
  status: string;
  last_checked_at: string | null;
  capabilities?: {
    total: number;
    known: number;
    tool_calling: number;
    no_tool_calling: number;
    no_streaming: number;
  };
};


export type Vendor = {
  id: string;
  name: string;
  base_url: string;
  logo: string;
  color: string;
  region: "cn" | "intl";
  models: string[];
  note: string;
  key_hint: string;
  docs: string;
  requires_endpoint: boolean;
};

export type PlanStep = {
  title: string;
  bot: string;
  expected?: string;
  /** Agent 判断的前置任务，批次由服务端自动计算。 */
  depends_on?: string[];
  /** RUN-12：同一个 batch 的步骤并行执行；不填表示单独一批（串行） */
  batch?: number;
};

export type Message = {
  id: string;
  thread_id: string;
  run_id: string | null;
  seq: number;
  sender_type: "user" | "bot" | "system";
  sender_bot_id: string | null;
  /** 历史消息补全：发送者已删除或已移出群聊时仍能显示名字 */
  sender_bot_name?: string;
  sender_bot_avatar?: string;
  sender_bot_role?: string;
  sender_bot_deleted?: boolean;
  type:
    | "text"
    | "plan"
    | "task"
    | "task_done"
    | "handoff"
    | "approval"
    | "question"
    | "tool_call"
    | "tool_result"
    | "system"
    | "limit";
  content: string;
  meta: any;
  status: "streaming" | "done" | "stopped" | "interrupted";
  created_at: string;
};

/** CHT-08：群聊主线程搜索命中项 */
export type SearchHit = {
  id: string;
  seq: number;
  type: Message["type"];
  sender_type: "user" | "bot" | "system";
  sender_bot_id: string | null;
  sender_bot_name?: string;
  sender_bot_avatar?: string;
  snippet: string;
  created_at: string;
};

export type Task = {
  id: string;
  run_id: string;
  thread_id: string;
  assigned_bot_id: string;
  bot_name: string;
  bot_avatar: string;
  instruction: string;
  expected_output: string | null;
  status: string;
  result: string | null;
  attempt: number;
  step_key?: string | null;
  /** RUN-12：计划批次，>0 表示和其他步骤并行执行 */
  batch?: number;
  tokens_used: number;
  started_at: string | null;
  ended_at: string | null;
};

export type Run = {
  id: string;
  chat_id: string;
  status:
    | "queued"
    | "running"
    | "waiting"
    | "waiting_children"
    | "completed"
    | "failed"
    | "stopped";
  steps_used: number;
  tokens_used: number;
  started_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
  mode: string;
  target_bot_id: string | null;
  parent_run_id?: string | null;
  parent_task_id?: string | null;
  resume: {
    kind?: "plan" | "approval" | "question" | "step";
    phase?: string;
    task_id?: string;
    approval_id?: string;
    question_id?: string;
    plan?: PlanStep[];
  } | null;
};

export type ChatFile = {
  id: string; path: string; size: number; updated_at: string | null;
  run_id?: string | null; created_by_bot_id?: string | null;
};

export type TerminalEntry = {
  id: string; actor: string; bot_id: string | null; kind: "command" | "code"; language: string;
  command: string; output: string; status: "queued" | "running" | "completed" | "failed";
  created_at: string; started_at: string | null; ended_at: string | null;
};
export type TerminalHistory = {
  items: TerminalEntry[]; provider: string; available: boolean; reason: string;
  workspace: string; sandbox_id: string | null; agent_running: boolean; event_cursor: number;
};

export type SandboxStatus = {
  provider: string;
  available: boolean;
  local_test: boolean;
  reason?: string;
  api_url?: string;
  template_id?: string;
  proxy_node_ip?: string;
  proxy_port?: number;
  sandbox_domain?: string;
  api_key_configured?: boolean;
  last_checked_at?: string | null;
  last_check_error?: string | null;
  verification?: "untested" | "passed" | "failed";
  session_count?: number;
  sessions?: {
    chat_id: string;
    chat_name: string;
    sandbox_id: string;
    last_executed_at: string | null;
    last_exit_code: number | null;
  }[];
};

/** CHT-07：从主线程某条消息拉出的分支线程 */
export type Branch = {
  id: string;
  title: string;
  anchor_message_id: string | null;
  parent_thread_id?: string;
  message_count?: number;
  created_at: string | null;
};

export type Chat = {
  id: string;
  name: string;
  supervisor_bot_id: string | null;
  supervisor: Bot | null;
  members: Bot[];
  run_mode: "auto" | "step";
  limits_override: Record<string, number>;
  always_allow_tools: string[];
  require_approval_for_write: boolean;
  last_active_at: string;
  main_thread_id?: string | null;
  active_run: Run | null;
  last_run?: Run | null;
  pending_approvals?: { id: string; tool: string; bot_name: string }[];
  tasks: Task[];
  files: ChatFile[];
  limits: { max_steps: number; token_budget: number; delegation_depth?: number };
};

export type ChatSummary = {
  id: string;
  name: string;
  run_mode: string;
  last_active_at: string | null;
  run_status: string | null;
  preview: string;
  member_count: number;
};

export type ChatTemplate = {
  id: string;
  name: string;
  avatar: string;
  description: string;
  members: { name: string; role: string; avatar: string }[];
};

export type Notification = {
  id: string;
  type: string;
  title: string;
  body: string;
  chat_id: string | null;
  target_type: string | null;
  target_id: string | null;
  read: boolean;
  created_at: string;
  email_status?: "pending" | "sending" | "sent" | "dev" | "failed" | "skipped" | null;
};

export type Memory = {
  id: string;
  scope: "shared" | "bot";
  bot_id: string | null;
  title: string;
  content: string;
  /** 主题分类（偏好/约定/项目背景/事实/其他），空表示未分类 */
  category?: string;
  /** user = 用户手动维护；bot = Bot 执行中通过 memory_save 自主保存 */
  source?: "user" | "bot";
  updated_at: string | null;
};

/** MEM-04：执行结束后自动提取、等待用户确认的记忆候选 */
export type MemoryCandidate = {
  id: string;
  run_id: string | null;
  chat_id: string | null;
  scope: "shared" | "bot";
  bot_id: string | null;
  title: string;
  content: string;
  category?: string;
  status: "pending" | "accepted" | "rejected";
  created_at: string | null;
};

export type Me = {
  id: string;
  email: string;
  name: string;
  has_provider: boolean;
  /** ACC-08：邮箱是否已验证（未验证不影响使用，但不能用邮箱找回密码） */
  email_verified: boolean;
  email_verified_at: string | null;
  settings: {
    max_steps: number;
    token_budget: number;
    delegation_depth?: number;
    notifications_enabled: boolean;
    approval_email_enabled?: boolean;
    approval_ttl_hours: number;
    search_configured: boolean;
  };
};

/** 8.6 运行状况看板 */
export type StatsOverview = {
  days: number;
  totals: {
    runs: number;
    completed: number;
    failed: number;
    stopped: number;
    running: number;
    queued: number;
    success_rate: number;
    avg_duration_sec: number;
    avg_steps: number;
    avg_tokens: number;
  };
  daily: { date: string; completed: number; failed: number; stopped: number; other: number }[];
  failures: { reason: string; count: number }[];
  recent_failures: {
    run_id: string;
    chat_id: string;
    chat_name: string;
    reason: string;
    ended_at: string | null;
  }[];
  queue: { depth: number; queued: number };
  alerts: {
    id: string;
    kind: string;
    level: "warning" | "critical";
    title: string;
    body: string;
    created_at: string | null;
    acknowledged: boolean;
  }[];
};

/* ---------------- 用量与费用（LIM-05 / MOD-05） ---------------- */
export type UsageSummary = {
  days: number;
  since: string;
  totals: {
    input_tokens: number;
    output_tokens: number;
    tokens: number;
    cost: number;
    calls: number;
    errors: number;
    error_rate: number;
    runs: number;
    completed: number;
    failed: number;
    stopped: number;
    avg_steps: number;
    avg_tokens: number;
    unpriced_tokens: number;
  };
  by_bot: {
    bot_id: string | null;
    name: string;
    avatar: string;
    input_tokens: number;
    output_tokens: number;
    tokens: number;
    calls: number;
    cost: number;
  }[];
  by_model: {
    provider_id: string | null;
    provider: string;
    model: string;
    tokens: number;
    calls: number;
    errors: number;
    cost: number;
    priced: boolean;
  }[];
  by_chat: {
    chat_id: string;
    name: string;
    runs: number;
    tokens: number;
    cost: number;
  }[];
  daily: { date: string; tokens: number; cost: number; calls?: number }[];
  recent_runs: {
    run_id: string;
    chat_id: string;
    chat_name: string;
    status: string;
    steps_used: number;
    tokens: number;
    cost: number;
    trigger: string;
    created_at: string | null;
    ended_at: string | null;
  }[];
};

export type LogEntry = {
  time: string;
  level: string;
  module: string;
  /** 后端进程号，和后端 app.log 里的一致 */
  pid?: string;
  request_id: string;
  run_id: string;
  task_id: string;
  message: string;
};

export type LogPage = {
  items: LogEntry[];
  has_more: boolean;
  offset: number;
  limit: number;
  files: string[];
  log_dir: string;
  max_file_mb: number;
  /** 被后端折叠掉的数据库语句条数（历史日志里残留的 SQL 回显） */
  hidden_sql?: number;
};

export type LogModule = { module: string; count: number };
