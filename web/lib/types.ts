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

export type PlanStep = { title: string; bot: string; expected?: string };

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
  tokens_used: number;
  started_at: string | null;
  ended_at: string | null;
};

export type Run = {
  id: string;
  chat_id: string;
  status: "queued" | "running" | "waiting" | "completed" | "failed" | "stopped";
  steps_used: number;
  tokens_used: number;
  started_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
  mode: string;
  target_bot_id: string | null;
  resume: {
    kind?: "plan" | "approval" | "question" | "step";
    phase?: string;
    task_id?: string;
    approval_id?: string;
    question_id?: string;
    plan?: PlanStep[];
  } | null;
};

export type ChatFile = { id: string; path: string; size: number; updated_at: string | null };

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
  limits: { max_steps: number; token_budget: number };
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
};

export type Memory = {
  id: string;
  scope: "shared" | "bot";
  bot_id: string | null;
  title: string;
  content: string;
  updated_at: string | null;
};

export type Me = {
  id: string;
  email: string;
  name: string;
  has_provider: boolean;
  settings: {
    max_steps: number;
    token_budget: number;
    notifications_enabled: boolean;
    approval_ttl_hours: number;
    search_configured: boolean;
  };
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
};

export type LogModule = { module: string; count: number };
