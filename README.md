# Botly · 多智能体工作台 v1

按《多智能体工作台 需求文档 v0.3》实现的第一版：一个总指挥 Bot 拆解任务、把子任务派给群聊里的其他 Bot，
全过程在同一条消息流里可见，高风险动作暂停等审批，关闭页面后任务继续在后台跑。

2026-11-03 更新：审批提醒只走邮件，聊天内仍可批准/拒绝，L1/L2/L3 卡片均可点击「在本群聊始终允许」。

模型服务返回 429、408、5xx 或发生网络故障时，在尚未输出正文前最多自动重试 5 次，默认按 2、4、8、16、32 秒递增退避并加入少量随机延迟，支持服务端的 `Retry-After` 等待时间。开始输出后不自动重试，避免重复内容；服务仍不可用时显示中文原因与请求 ID，原始错误及各次重试保留在日志中。
独立账号的 50 用户 / 20 并发验收已通过，见 [多用户验收](docs/多用户验收.md)。
设置页可直接连接自己部署的 CubeSandbox 后端，填写地址、模板和可选密钥即可测试并保存；本机 Docker 仅用于测试，见 [沙箱接入](docs/沙箱接入.md)。

```
用户消息 → 创建执行(Run) → Redis 队列 → Worker
   ├─ 简单任务：总指挥直接回答
   └─ 复杂任务：计划卡片 → 用户确认 → delegate → 子 Bot 子线程执行 → 汇总
        任意时刻：审批 / 提问 / 逐步确认 → 暂停等用户 → 恢复
        任意时刻：停止 → 已停止，保留已完成步骤与产物
```

## 快速开始

### 前置

- Python 3.11+、Node 18+
- Redis（队列与事件广播）。本机 Docker 实例的连接串（含密码）写在 `server/.env` 的
  `BOTLY_REDIS_URL` 里，例如 `redis://:密码@127.0.0.1:6379/0`

### 首次准备

```bash
cd server
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env          # 按需修改 Redis 连接串

# 生成邀请码（第一版不开放自由注册）
./venv/bin/python -m scripts.make_invite --uses 10 --days 60 --note "内测"
```

### 启动

```bash
# 方式一：一键启动 API + Worker + Web
./start.sh

# 方式二：分开启动（推荐，日志清晰）
cd server && ./run-api.sh       # API      http://127.0.0.1:8100
cd server && ./run-worker.sh    # Worker   Agent 循环在这里跑
cd web    && ./dev.sh           # Web      http://127.0.0.1:3200
```

> Worker 必须启动。只起 API 时前端能打开、能发消息，但执行会停在「排队中」。
>
> `web/.next` 是 dev 与 build 共用的目录：**dev server 运行时不要执行 `next build`**，
> 否则 build 产物会覆盖 dev 正在引用的 chunk，页面报 `Cannot find module './xxx.js'`。
> 遇到时停掉 dev、重新 `./dev.sh` 即可（脚本会先清 `.next`）。

### 首次使用

1. 打开 http://localhost:3200，用邀请码注册。
2. 进入「设置 → 模型厂商」，从下拉里选一个厂商（内置 25 家：国内 13 家 + 海外 12 家，见下表），
   **只填 API Key** 即可：base_url 已预置，保存时会调用 `GET {base_url}/models` 自动获取可用模型；
   拿不到就退回预置模型列表，不影响使用。
3. 到「Bot」里编辑每个 Bot，选择模型厂商与具体模型（模型下拉里就是探测到的列表，也可以手填）。
4. 回到群聊，发一句任务。总指挥会先出计划卡片，确认后开始派任务。

点击会话列表的「+」可从市场调研、内容创作、软件开发、数据分析、产品策划五种模板一键建群。
模板自动选择总指挥和协作成员，复用已启用且模型可用的成员，缺少的角色会自动创建并沿用可用模型。
尚未接入模型时也可以先组建团队，再到「智能体」页面配置；「自定义团队」仍支持手动选择成员。

### 验证

```bash
curl -s http://127.0.0.1:8100/api/health
# {"ok":true,"redis":true,"queue_depth":0,"version":"1.0.0"}

cd server && ./venv/bin/python -m pytest tests -q     # 端到端验收与上下文回归测试
```

## 架构

### 进程

| 进程 | 职责 |
| --- | --- |
| API（FastAPI） | 鉴权、增删改查、SSE 推送、把执行入队。不跑 Agent 循环 |
| Worker | 从 Redis 队列取执行，跑总指挥图与子 Bot 图，写消息与事件 |
| Web（Next.js） | 只做界面渲染，`/api` 由 Next 反向代理到 FastAPI（同源，避免 Cookie 跨域与 SSE 缓冲） |

### 队列与事件

- **队列**：Redis List，`LPUSH` 入队、`BRPOP` 消费，消息体是 `run_id`。
- **事件**：落 `events` 表（自增 id 即 SSE 序号），同时 `PUBLISH` 一条唤醒信号。
  SSE 端收到唤醒后按 id 顺序从表里读，因此**不丢、不乱序**，`Last-Event-ID` 可直接补齐断线期间的消息。
- **降级**：Redis 不可用时自动退回「DB 轮询 + 直接扫 queued 执行」，功能不变、延迟略高。

### 执行与恢复

编排跑在 **LangGraph StateGraph** 上（`server/app/agent/graph.py`），总指挥与子 Bot 共用同一张图：

```
START → model ──有工具调用──→ tool ──还有未执行的调用──→ tool
          ↑                     │
          └─────────────────────┘
          │
          └──没有工具调用──→ after ──子 Bot 交回结果──→ model（切回总指挥）
                               │
                               └──总指挥结束──→ END
```

- **暂停**用 `interrupt()`：计划确认、提问、审批、逐步确认；payload 落 `runs.resume_payload`。
- **恢复**用 `Command(resume=payload)`：Worker 从 checkpoint 继续，节点重放时会读库判断
  「已经批准 / 已经回答 / 已经出过卡片」，因此不会重复建审批、重复发卡片。
- **派任务不是递归**：`delegate` 在 `tool` 节点里建子线程与任务，然后把图里的
  `role` 从 `supervisor` 切成 `worker` 接着跑；子 Bot 结束后 `after` 节点把结果回写成
  delegate 的工具结果，再切回总指挥。**多层委派（RUN-09）** 同一套机制：子 Bot 也能派任务，
  嵌套任务的线程挂在上级任务线程下，结束/失败时沿线程链切回上级，直到最外层交回总指挥；
  深度上限默认 2 层，可在设置页或群聊里调到 1–5。
- **Agent 自主调度（RUN-12）**：总指挥拆解时自行判断每步的 `depends_on`（前置步骤标题），
  独立任务填空数组；执行器据此自动生成并行与串行批次，无需用户指定执行方式。
  超过单批容量的独立任务拆成多个并行批次；后续步骤不能在前置批次完成前抢跑。
  同一批的步骤由总指挥派出一项后即可由系统按计划补齐，每个步骤是一条独立的子执行（`runs.parent_run_id`），
  有自己的检查点、审批与停止开关；总指挥转入 `waiting_children`，全部返回后写一条批次
  汇总再继续。不同批仍串行；逐步确认模式保持串行。
- **目标委派与自主执行**：总指挥负责目标、范围、用户约束、依赖调度和验收，子 Agent 自己选择方法、工具与技能。
  子任务目标不明确时用 `ask_supervisor` 向总指挥澄清，答复保存在任务执行过程里；总指挥也缺少用户信息时再向用户提问。
- **一条工具调用 = 图里的一步**，所以一轮多个调用可以逐个暂停、逐个恢复，不会重放已执行过的调用。
- **检查点**：SQLite 存 `data/langgraph_checkpoints.db`（独立文件，不干扰业务库）；
  Postgres 存同一个库的 `checkpoints` 系列表。Worker 重启后从检查点继续。

执行状态同时落在 `runs` / `tasks` / `messages` / `approvals` / `questions` 表里，图本身不持有业务数据：

| resume.kind | 触发场景 | 恢复方式 |
| --- | --- | --- |
| `plan` | 总指挥提交计划 | 计划状态已是 approved，节点直接继续执行 |
| `approval` | 调用需审批的工具 | 校验参数哈希一致后消费审批并执行该工具 |
| `question` | Bot 调 `ask_user` | 把用户回复作为工具结果回写 |
| `step` | 逐步确认模式下子任务完成 | `after` 节点的 interrupt 返回，切回总指挥继续 |

Worker 用 `lease_until` 做租约：进程崩溃后，租约过期的 `running` 执行会被回收成 `queued` 重跑，
图会从最近一次检查点继续，已完成的步骤和产物都在库里，不会丢。

## 与需求文档的对应

### 已实现（P0）

| 模块 | 覆盖 |
| --- | --- |
| 账号 ACC-01~06 | 邀请码注册（次数/有效期/使用记录）、邮箱密码登录、Google/GitHub 第三方登录、HttpOnly Cookie 7 天、登录失败限流字段、用户级数据隔离（越权 404）、未配模型接入时禁止发起执行 |
| 模型接入 MOD-01~04 | 预置 25 家厂商、只填 Key、自动探测模型、连通性/流式/工具调用能力探测、Key 对称加密只回显末 4 位、删除时校验占用 |
| Bot BOT-01~05 | 增删改查、同账号名称唯一（用于 @）、默认总指挥+研究员+架构师、软删除（管理列表不再显示、历史消息标注「已删除」、名字可复用）、删除保护、执行使用配置快照、6 个内置角色模板（总指挥/研究员/架构师/程序员/数据分析师/终审）一键套用 |
| 群聊 CHT-01~06 | 建群、随时增减成员、总指挥交接（先交接再移出旧总指挥，执行中的成员受保护）、@Bot 直连（支持中文标点紧邻）、单群单执行、流式输出、消息类型渲染、历史列表、重命名、解散（二次确认，连带清理消息/子任务/产物/通知/沙箱文件） |
| 消息搜索 CHT-08 | 群聊主线程按正文搜索：转义 `%`/`_`、按时间倒序分页、命中片段前后截取、结果点开跳到该消息上下文；Postgres 下自动建 pg_trgm 索引 |
| 调度 RUN-01~11 | 自动模式、计划卡片（可编辑步骤）、执行中调整以系统提示呈现、逐步确认、delegate 子线程、ask_user、停止、子 Bot 在群里回话、委派深度可配置（默认 2 层，支持子 Bot 再派任务）、子任务失败回写上级、改派上限按稳定的 `step` 键判断（RUN-10） |
| 审批 APV-01~05 | 三级权限判定、审批卡片（参数可展开）、拒绝原因回传、24h 过期、参数哈希校验不可绕过 |
| 记忆 MEM-01~03 | 共享/私有分离、按更新时间注入并受 4000 字上限约束、仅用户可编辑 |
| 工具 TOL-01~06 | 网页搜索、网页读取（SSRF 防护）、沙箱文件读写列删、JSON Schema 校验、三级权限；MCP 接入（TOL-05，HTTP/stdio 两种传输，服务级权限级别，审批联动）；代码执行 `code_exec`（TOL-06，跑在群聊的 Ubuntu 容器里，python/node/bash/sh） |
| 沙箱接入 | 设置页直接连接用户自己的 Cube 后端，按账号加密保存配置、按群聊持久化远端 ID，附件和产物自动同步；无 Cube 配置时默认未接入，`docker` 仅显式用于本机测试 |
| 技能 | 名称、用途和目录供 Bot 按需选择，完整说明由 skill_file_read 加载；支持 ZIP / SKILL.md / GitHub / URL / ClawHub 导入，说明、脚本及资源自动安装到会话沙箱 `/workspace/skills/`，卡片可复制真实路径，文件不会进入产物列表 |
| 通知 NTF-01~05 | 审批请求与过期提醒仅邮件发送（持久队列、失败重试、用户开关）；保留聊天审批与通知记录；浏览器/Web Push 用于任务和提问 |
| 限额 LIM-01~04 | 步数与运行时长均不设上限（只统计步数/记录起止时间）；token 预算可选无限制，有限预算保留 80% 提示；委派深度默认 2 层、可改 1–5（设置页默认 + 群聊覆盖）、单用户并发 2 |
| 用量与费用 LIM-05 / MOD-05 | 按 Bot / 模型 / 群聊 / 日期聚合 token 与调用次数；模型单价按每百万 token 配置，费用为估算值；未填单价的 token 单独标出 |
| 执行中插话 RUN-13 | 执行中发送的消息不新建执行，落库为「插话」，总指挥在下一轮模型调用前以系统提示标注优先级后读取 |
| 审批白名单 APV-06 | 审批卡片可点击「在本群聊始终允许」，支持 L1/L2/L3，按当前群聊 + 当前工具保存；群聊设置里可撤销，并可切换 L2 写入是否需要审批 |
| 文件 FIL-01~03 | 右侧「任务 / 产物 / 终端」面板，Cube 终端为持续 PTY，显示真实 shell 路径、直接输入及实时输出，Agent 命令记录另可查看；产物仅列 Agent 交付文件，技能与依赖留在沙箱；点击产物在侧栏预览、可下载，用户附件仍供 Agent 读取 |
| 接口与事件 | 第 11 节全部接口、第 9.3 节事件类型、`Last-Event-ID` 补发 |

### 已实现的 P1（2026-10）

- **ACC-07 删除账号**：设置页「危险操作」二次确认（密码 + 邮箱）；删除后账号立即停用、
  未完成的执行标记为已停止，数据保留 30 天，期间管理员可用
  `python -m scripts.purge_accounts --restore <邮箱>` 恢复；到期由 Worker 小时级任务彻底清除
  （业务表、沙箱文件、上传图片、登录记录、加密 Key），也可用 `--purge <邮箱> --yes` 立即清除。
- **15.2 导出群聊记录**：聊天页「更多操作 → 导出聊天记录」下载 Markdown；按日期分组，
  含用户消息、Bot 最终回复、计划卡片与系统提示，工具调用与子 Bot 过程镜像不重复导出。
- **MEM-05 长对话摘要**：主线程历史超过 token 阈值（默认约 8k，可配 `BOTLY_SUMMARY_THRESHOLD_TOKENS`）
  时，用总指挥模型把「最近 40 条之前」的旧消息压缩成摘要，存到线程并注入后续上下文；
  保留最近 40 条原文。摘要失败自动退回硬截断，不影响执行。
- **MEM-04 记忆候选**：执行成功后在后台用总指挥模型从「用户原话 + 执行汇总」里提取最多 3 条
  值得长期记住的信息（不采信网页/工具返回），写入 `memory_candidates`；记忆页顶部显示
  「待确认」，用户点保存才写入正式记忆，忽略则丢弃。
- **CHT-07 分支线程**：鼠标移到任意消息上，气泡右侧出现「分支」按钮，点击即拉出独立线程
  （复制锚点及之前的文本快照）；分支里的提问单独触发执行、回复落在分支，不打断主线程；
  分支是「深入讨论」，不派任务、不出计划。群聊菜单「分支讨论」可切换或删除分支（
  `DELETE /api/chats/{id}/branches/{thread_id}`，连同分支消息与执行一起清理，主线程不受影响）。
- **8.6 运行状况看板与告警**：侧栏「运行状况」展示完成率、每日执行（完成/失败/停止堆叠）、
  失败原因 Top、最近失败与系统告警；Worker 每分钟检查队列积压（≥10）、执行卡死（>20 分钟）、
  失败率（近 60 分钟 ≥30%），同类告警 30 分钟内不重复。
- **用量页每日趋势**：带 Y 轴刻度、网格、日均虚线与选中日期读数条（日期 / token / 调用次数 / 费用），
  支持近 7 / 30 / 90 天；接口的 `daily` 增加每日调用次数。
- **ACC-08 找回密码与邮箱验证**：登录页「忘记密码？」→ 填邮箱 → 收到一次性重置链接
  （60 分钟、只能用一次）→ 设置新密码，**旧会话立即失效**；注册后自动发一封验证邮件，
  设置页「账号」显示验证状态并可补发。邮件走 `BOTLY_SMTP_*` 配置；没配 SMTP 时默认开发模式，
  邮件写日志、接口回一个可点链接，本地能走完整流程（生产需配 SMTP 并把 `BOTLY_MAIL_DEV` 设为 0）。
- **ACC-09 第三方登录（Google / GitHub）**：登录页出现「使用 Google / GitHub 登录」，
  client id/secret 没配的一律不显示。回调走 `state + HttpOnly nonce` 校验；已绑定的直接登录，
  未绑定但**邮箱已验证且与现有账号一致**的自动绑定，其余按 `BOTLY_OAUTH_ALLOW_SIGNUP`
  决定是建号（默认允许）还是提示先用邀请码注册。已有账号可在设置页「账号」里点「绑定」，
  走 `/api/auth/oauth/{provider}/bind/start`，对方邮箱与本站不同也能绑上。

### 未实现（P2 / 后续规划，主动搁置）

Bot 版本回滚、短信等其他通知渠道（审批已决定只走邮件）、
团队空间与共享群聊、计费套餐、每个 Bot 持久云电脑、移动原生 App。

完整的缺口清单、P0 偏差、验收标准对照与建议排期见
[docs/功能缺口与后续规划.md](docs/功能缺口与后续规划.md)。

## 实现决策与偏差

### 1. 编排用 LangGraph，但「派任务」是同一张图里切角色

文档 9.1 要求「总指挥图 + 各 Bot 子图」。实现上是一张 `StateGraph`（`model / tool / after` 三个节点），
派任务时把图里的 `role` 从 `supervisor` 切成 `worker`，而不是嵌套一张子图：

- 子 Bot 的暂停（审批、提问）能直接用同一个 `interrupt()` 冒泡，父图不需要知道
  「恢复值该路由给哪个子图命名空间」——这是嵌套子图最容易出错的地方。
- 一棵检查点树、一条 thread（`run:<id>`），Worker 重启后恢复逻辑只有一条路径。
- 代价：子 Bot 与总指挥共用同一套状态字段，靠 `role` 区分；多层派任务不嵌套图，而是
  复用同一条图按线程链切换角色（RUN-09）：上级任务线程是下级任务线程的 `parent_thread_id`，
  子任务结束时沿链切回上级，因此仍是一棵检查点树、一条 thread，不需要真正的嵌套子图。

恢复时节点会重放，所以约束仍然成立：**审批点必须在有副作用的动作之前**、
**有副作用的工具按调用 ID 去重**（`approvals.consumed_at` 是审批的去重标记，
计划/提问卡片按 `meta.tool_call_id` 去重）、**模型轮按输出消息复用**（每轮输出落库时
在同一个事务里记 `runs.last_turn_message_id` 并计一步，重放时线程最后一条消息若正是它，
就直接复用这一轮结果，不再调一次模型，也不会重复计步/计费或产生重复消息）。

### 2. 检查点与业务表的分工

checkpoint 只存图的拓扑进度（下一步是哪个节点、当前角色是谁），**业务数据仍然以
`runs` / `tasks` / `messages` 为唯一事实来源**：

- 构造模型上下文（`_build_messages`）始终从库里重建，不读 LangGraph 的 state，
  所以「暂停期间群里插入了系统消息」这类情况不会漏。
- 暂停 payload 只是镜像到 `runs.resume_payload`（API 用它判断能不能批准/回答），
  恢复时 API 改的就是这个字段。

### 3. 存储默认 SQLite，可切 Postgres

单机演示零依赖。`DATABASE_URL` 换成 `postgresql+psycopg://...` 即可切库（表结构由 SQLAlchemy 统一生成）。
注意：SQLite 下的并发写依赖 WAL + busy_timeout，正式多人使用建议直接上 Postgres。

### 4. 事件用「表 + Pub/Sub 唤醒」而非纯 Pub/Sub

纯 Pub/Sub 断线即丢消息，无法满足 NTF-01「重新打开补齐断线期间消息」。
当前方案用 Redis 只做唤醒，数据以表为准，天然支持按序号补发。

### 5. 时间统一为「无时区 UTC」

SQLite 不保存时区，混用 aware/naive 会在做时间差时抛 `TypeError`。全项目约定库里存 UTC naive。

### 6. 模型接入收敛为「厂商 + Key」

文档要求用户填 `base_url`、`API Key` 和模型名。实际使用中这三项对普通用户是负担，且容易填错
（`base_url` 少写 `/v1`、模型名拼错都会导致第一次执行就失败）。第一版改为：

- **厂商目录内置**（`app/vendors.py`）：base_url 与推荐模型名随厂商预置，用户只填 Key。
- **保存时自动探测**：`GET {base_url}/models` 拉真实模型列表；失败则退回预置列表并提示原因，
  不阻断保存（部分自建网关没有该接口）。同时真实探测首个模型的流式输出与工具调用能力（MOD-02），
  结果按模型持久化；「检测能力」可批量检测（单次最多 12 个）。
- **模型选择下沉到 Bot**：同一厂商下不同 Bot 可以用不同模型（总指挥用强模型、子 Bot 用便宜的），
  这也符合文档「总指挥建议用能力最强的一档」的建议。

代价是厂商列表需要人工维护；`custom` 入口保留了任意 OpenAI 兼容网关的能力。

预置厂商（`app/vendors.py`）：

| 国内（13） | 海外（12） |
| --- | --- |
| DeepSeek、月之暗面 Kimi、智谱 GLM、阿里云百炼（通义）、火山方舟（豆包）、腾讯混元、百度千帆（文心）、MiniMax、阶跃星辰、零一万物、百川智能、讯飞星火、硅基流动 | OpenAI、Anthropic Claude、Google Gemini、xAI Grok、Mistral、Cohere、Groq、Together AI、Fireworks、Perplexity、OpenRouter、自定义 |

每个厂商带一个品牌色与真实图标：图标取自 [LobeHub Icons](https://icons.lobehub.com) 的静态 SVG
（`@lobehub/icons-static-svg`，走阿里云 npmmirror 镜像，国内访问快）。彩色品牌用 `-color.svg`，
OpenAI / xAI / Groq 这类本身是单色的品牌用 `.svg` 并在深色主题下自动反色；CDN 不可达时回退为
文字标识。也可以在添加厂商时上传自己的图片覆盖。

### 7. 派任务呈现为群聊 @ 消息，而不是任务卡片

文档 5.1 要求「总指挥派任务后，消息下方挂任务卡片」。实际读起来更像工单系统，不像群聊。

第一版改为：总指挥直接发一条 `@研究员 <指令>` 的消息；子 Agent 开始执行时先用自己的话说明处理思路
（系统提示要求，不同 Agent 措辞不同），这段文本以及后续输出都会实时镜像到主线程，以自己的头像和气泡
在群里说话，最终回复不会再重复一遍。工具调用和返回**不进入群聊**，只留在子线程——群聊只展示
bot 的聊天记录，想看过程（参数、结果、失败原因）就进右侧任务面板。
任务面板列「排队中 / 进行中 / 等待审批 / 等待回复」的任务，点击进入后能看到实时执行过程与工具调用；
总指挥自己的工具调用（`web_search`、`web_read`、文件读写等）在主线程里，面板顶部单列一条
「总指挥 · 工具调用与返回」入口，点进去同样能看参数、返回和失败原因。派任务 / 计划 / 提问这三种
控制类调用不重复列出——它们在群聊里各有卡片。
状态（执行中 / 已完成 / 耗时 / token）以一行小字挂在消息下方。
这样主线程读起来就是一段完整对话，同时保留了可追溯性——任务面板、子线程、工具调用记录都还在。
消息按群聊样式渲染：用户消息靠右、Bot 消息靠左带气泡，正文支持 GFM Markdown（表格、代码块、任务列表、
链接），Markdown 文件在文件面板里也是预览而不是源码。模型还没吐出文字时只显示三点跳动，
第一段内容到达后才出现气泡和光标；只调工具、没有文字的轮次不会留下空气泡。
右侧任务/文件面板支持拖拽调整宽度（双击恢复默认，宽度记在 localStorage），手机上仍是底部抽屉。
`task` / `task_done` 两种旧卡片类型仍保留渲染逻辑，用于兼容历史消息。

### 8. 恢复前先给悬空工具调用补结果

OpenAI 兼容接口要求 `assistant(tool_calls)` 后面必须紧跟每个 `tool_call_id` 对应的 `tool` 消息。
计划确认、逐步确认、子任务审批这些暂停点天然是「只有 tool_call、还没有 tool_result」的状态，
恢复时如果直接继续调用模型，就会得到
`An assistant message with 'tool_calls' must be followed by tool messages` 的 400。

处理方式：工具调用在图上是一步，恢复时**重放的就是这条调用本身**，结果写回后才允许回到
`model` 节点，所以正常情况下不会出现悬空调用；另有两处兜底——僵尸执行回收、子任务完成回写
delegate 结果（都用 `_answer_open_calls`）。构造模型历史时也会把 tool 结果紧贴对应调用、
丢弃结果不全的调用，即使暂停期间插入了系统消息（如成员变动）也不会破坏结构。

### 9. 推理模型的 reasoning_content 回传

deepseek-reasoner / Kimi K2 / GLM 等思维链模型要求：下一轮请求必须把上一轮的 `reasoning_content`
原样回传，否则返回 400（`The reasoning_content in the thinking mode must be passed back`）。
消息表新增 `reasoning_content` 列（只在服务端构造上下文使用，不下发前端），流式响应里捕获思考过程并落库，
构造历史时回填到对应的 assistant 消息上。

要点：一轮可能返回**多个 tool_call**，每条都会被拆成独立的 assistant 消息，思维链必须挂在**每一条**上
（尤其最后一条），否则下一轮接口同样报 400。另外 `approve` / `answer` 恢复执行时保留 `thread_id`，
否则主管级提问/审批的恢复会因找不到线程而失败。
工具轮没有输出思考内容时也回传空的 `reasoning_content` 字段，不能把空值当作缺失而省略。

还有一类容易漏的情况：崩溃、重启或停止时残留的**流式草稿**会被标成 `interrupted`（半句话）。
以前它仍会作为 assistant 消息进入下一次请求——既缺 `reasoning_content`，内容本身也是被截断的半句。
现在构造上下文时 `streaming` 与 `interrupted` 一律跳过，并且**所有** assistant 消息都显式带上
`reasoning_content`（没有思考内容就填空串）。实测该接口对空串返回 200、对缺字段返回 400，
所以「字段总是存在」比「字段通常有值」更重要。

派任务的 `@成员` 文本与子任务回复是群聊展示副本：本次执行通过真实的 `delegate` 调用和对应工具结果
回传，不把展示副本再当作 `assistant` 消息发送，避免没有思考内容的展示文本触发 400。
每个子任务只回答自己的调用，同轮多个派任务逐项执行并返回各自结果；子 Bot 失败后由总指挥读取失败结果再回复。

构造子线程上下文时，从任务表注入指令和期望产出。主线程明确标注本次用户请求，并只注入本次执行的
计划、错误、预算提示和插话优先级；旧执行的控制信息不进入新请求。其他 Bot 的历史回复保留署名，作为参考资料。
当前执行的用户请求与消息保留，40 条截断只用于此前文本历史。前端切换群聊时忽略旧群聊的异步响应与事件。

### 10. 执行中插话是「同一条执行里的补充消息」

RUN-13 没有新建执行：`POST /chats/{id}/messages` 带 `interject=true` 时，若群里已有进行中的执行，
消息以 `meta.kind = "interjection"` 落到主线程、挂在该 `run_id` 下，接口直接返回这条消息和当前执行。
总指挥下一次构造模型上下文时，会在这条用户消息**前面**插入一条系统提示，说明这是执行中的补充、
优先级高于原任务。这样既不会让用户原话被标注污染（模型复述派任务指令时会带出来），也不需要额外的队列。

子 Bot 在自己子线程里看不到主线程，所以执行器会在每个模型轮开始时检查主线程是否有**尚未被总指挥
读取**的新插话（用 `runs.last_interjection_seq` 记录总指挥已读到的位置）。有则提前结束当前子任务、
交回总指挥，由总指挥读插话并调整——不再等到整个子任务跑完。

### 11. 用量与费用按调用日志聚合，不改执行主流程

`usage_logs` 本来就是每次模型调用的流水（token、模型、耗时、错误），用量页只做聚合，不回写业务表：

- 费用 = `input_tokens / 1e6 * 输入单价 + output_tokens / 1e6 * 输出单价`，单价存在
  `providers.models` 里（每百万 token），没填单价的调用只计 token，并在页面顶部单独提示。
- 费用是**事后重算**的：补填单价后历史调用立刻有费用，不需要重新执行。
- 群聊维度先按 `usage_logs → runs → chats` 关联，再按 run 的费用汇总，避免跨模型时摊不准。

## 与文档仍未对齐的地方

以下偏差已经确认，代码里也有对应注释，后续迭代再按需求补齐：

1. **步数与运行时长上限已删除**（LIM-01 / 验收 8 的“步数”“运行时长”部分）。执行不再按步数或时长停止，
   只保留步数统计与起止时间；token 预算、委派深度和用户停止仍然生效。需求文档已同步修订。
2. **任务卡 / 完成卡 / 交接提示按兼容消息落库**（CHT-05 / RUN-08）。群聊显示 `@成员 指令`、概要与子 Bot 回复，
   `task` / `task_done` / `handoff` 带 `compat` 标记，不重复渲染，导出可消费这些类型。
3. **模型能力探测已硬校验连通性与流式输出**（MOD-02）。保存厂商时会探测首个模型的流式输出与
   工具调用能力并按模型持久化；Bot 选模型时提示、明确不可用的置灰；群聊创建或更换总指挥时，
   后端拒绝明确不支持工具调用/流式输出的模型。残留：没探测过的模型不拦截，仍可能在第一次执行时失败。
4. **审批过期已按“拒绝”处理**（APV-03，2026-11-02 修复）。过期时写系统消息、把执行重新入队，
   图重放工具节点时以「审批已过期，工具未执行」作为工具结果交给总指挥，由它决定改派或跳过；
   不再直接判失败。审批与执行状态不匹配时只标记过期，不会误唤醒。
5. **未配置搜索 Key 时 `web_search` 自动回退 DuckDuckGo**（TOL-01，2026-11-02 调整）。Bot 编辑页
   仍可勾选该工具，执行时优先用设置页配置的 Tavily Key，没配 Key 或 Tavily 调用失败就回退
   DuckDuckGo 免密钥搜索；结果末行注明来源，不静默假装是付费服务的结果。
6. **可观测性与备份已落地**（8.6 / M5）。提供 `/api/metrics`（Prometheus 文本格式）、
   `/api/stats/overview` 运行状况看板（完成率、失败原因分布、最近失败、告警、队列）、
   Worker 每分钟告警（队列积压 / 执行卡死 / 失败率）与 `scripts/backup_db.py`。
   Alembic 版本化迁移及 50 用户 / 20 并发 Postgres 验收已完成；备份自动调度与恢复演练待补。
7. **Postgres 下事件序号可能“后插入先提交”**（9.3）。events 的自增 id 在插入时分配、提交时可见，
   并发事务可能让较小编号晚于较大编号出现，单条事件可能被 SSE 跳过；前端重连/快照刷新可兜底。
   彻底修复需要按群聊单独分配序号或提交后排序，暂未改动。
8. **删除账号采用“先停用、后清除”**（ACC-07）。文档要求删除后 30 天内可恢复；实现上不是先删再备份，
   而是立即停用并保留数据 30 天（期间接口全部 401、无法登录），到期由 Worker 彻底清除。
   这样恢复不依赖额外备份，但保留期内数据仍占磁盘。
9. **邮箱验证不阻断使用**（ACC-08）。注册后会自动发验证邮件，但未验证也能正常登录、建群、执行；
   验证状态只在设置页提示。原因是第一版是邀请制，强制验证会把“邮件进垃圾箱”的用户直接挡在门外。
   重置密码本身不要求已验证——能收到邮件就说明邮箱可控。

## 目录

```
server/
  app/
    main.py              FastAPI 入口与路由挂载
    worker.py            独立 Worker：Redis 队列消费 + 僵尸执行回收
    core/                全局内核（谁都可以依赖，自己不依赖业务）
      config.py          环境变量与默认参数
      db.py              引擎、会话、轻量迁移
      models.py          数据模型（第 10 节）
      deps.py            依赖注入：当前用户、归属校验
      security.py        密码哈希、API Key 加密、会话与 OAuth state 签名
      logging_setup.py   日志格式、追踪 id、脱敏
      events.py          事件落库与订阅广播
      redis_bus.py       队列、事件广播、SSE 唤醒
      http_logging.py    请求日志中间件
      commit_middleware.py  响应发出前提交事务
    api/                 各模块路由（auth / bots / chats / runs / usage / metrics / stats …）
    agent/               执行引擎
      orchestrator.py    节点实现：模型调用、派任务、审批闸门、限额、收尾
      graph.py           LangGraph 编排：StateGraph(model ⇄ tool → after) + 检查点
      llm.py             OpenAI 兼容流式客户端 + mock provider
      memory_context.py  记忆注入
      capabilities.py    模型能力判定（工具调用 / 流式）
      execution_config.py 执行配置快照
      signals.py         PauseSignal（interrupt 的载体）
      serializers.py     对外 JSON 序列化
    sandbox/             执行环境
      files.py           沙箱路径校验与配额
      linux.py           每群一个 Ubuntu 容器（run_command / code_exec）
    tools/               工具与扩展
      registry.py        工具注册表（Schema / 权限级别 / 执行器）
      mcp.py             MCP 客户端（HTTP / stdio）
      skills.py          技能包解析与提示词注入
    services/            业务服务与外部集成
      accounts.py        账号停用、保留期与彻底清除（ACC-07）
      alerts.py          队列积压 / 执行卡死 / 失败率告警
      maintenance.py     定期清理与回收
      templates.py       默认 Bot 模板
      vendors.py         厂商与模型目录
      mail.py            邮件发送
      push.py            Web Push
      oauth.py           Google / GitHub 登录
  scripts/
    make_invite.py     生成邀请码
    purge_accounts.py  已删除账号的恢复与清理
    backup_db.py       数据库备份（Postgres pg_dump / SQLite .backup）
    migrate_sqlite_to_postgres.py  把业务表从 SQLite 复制到 Postgres
    smoke.py           对运行中的服务做端到端冒烟
    build_sandbox.sh   构建沙箱预装镜像 botly-sandbox:24.04
  tests/
    test_flow.py       20 项验收测试（进程内驱动，无需常驻 Worker）
    test_account_export.py  账号删除与群聊导出回归
    test_metrics.py    Prometheus 指标鉴权与输出
    test_backup.py     备份与保留策略
    test_mem04.py      记忆候选生成、确认与忽略
    test_branch.py     分支线程创建、隔离执行、删除与越权
    test_alerts.py     告警触发与冷却
    test_stats.py      运行状况看板统计
web/
  app/                 登录、注册、聊天、Bot、记忆、运行状况、通知、设置、用量、运行日志
  components/chat/     消息流、计划/任务/审批/提问卡片、群聊设置、侧栏、输入栏
  components/ui/       Select 下拉、Modal、Confirm 确认弹窗、Avatar、AvatarPicker
  lib/                 API 客户端、类型、格式化
```

## 配置

`server/.env`：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `BOTLY_SECRET_KEY` | 会话签名密钥，同时派生加密主密钥 | 开发用占位值 |
| `BOTLY_REDIS_URL` | Redis 连接串 | `redis://127.0.0.1:6379/0` |
| `BOTLY_REDIS_PREFIX` | 不同部署与测试库的队列/事件前缀 | `botly` |
| `BOTLY_ADMIN_EMAILS` | 可查看部署日志和系统告警的管理员邮箱，逗号分隔；普通用户只看自身日志/指标 | 未设置 |
| `BOTLY_WORKER_CONCURRENCY` / `BOTLY_MAX_CONCURRENT_RUNS` | 单 Worker 槽位 / 全局运行执行上限 | `20` / `20` |
| `BOTLY_DATA_DIR` | 数据目录（SQLite 与沙箱） | `server/data` |
| `DATABASE_URL` | Postgres 连接串；不设置时用 `server/data/botly.db` | 未设置 |
| `BOTLY_ENCRYPTION_KEY` | 独立的 Fernet 主密钥（可选） | 由 SECRET_KEY 派生 |
| `BOTLY_COOKIE_SECURE` | 设为 `1` 时强制会话 Cookie 带 Secure（HTTPS 部署） | 关闭 |
| `BOTLY_METRICS_TOKEN` | `/api/metrics` 的抓取令牌（`Authorization: Bearer <token>`）；未设置时仅回环地址可访问 | 未设置 |
| `BOTLY_SUMMARY_THRESHOLD_TOKENS` | 主线程历史超过该 token 估算值时触发长对话摘要 | 8000 |
| `BOTLY_SUMMARY_MAX_ITEMS` | 单次摘要最多处理的历史消息条数 | 80 |
| `BOTLY_ALERT_QUEUE_BACKLOG` | 队列积压告警阈值（排队执行数） | 10 |
| `BOTLY_ALERT_RUN_STUCK_MINUTES` | 执行运行超过多少分钟触发卡死告警 | 20 |
| `BOTLY_ALERT_FAIL_RATE` | 窗口内失败率告警阈值 | 0.3 |
| `BOTLY_ALERT_COOLDOWN_MINUTES` | 同类告警冷却时间 | 30 |
| `BOTLY_SMTP_HOST` | 发信 SMTP 主机；不配置时进入邮件开发模式 | 未设置 |
| `BOTLY_SMTP_PORT` | SMTP 端口 | 587 |
| `BOTLY_SMTP_SECURITY` | `starttls` / `ssl` / `none` | `starttls` |
| `BOTLY_SMTP_USER` / `BOTLY_SMTP_PASSWORD` | SMTP 登录凭据（内网中继可留空） | 未设置 |
| `BOTLY_MAIL_FROM` | 发件人地址 | 取 `SMTP_USER` |
| `BOTLY_MAIL_DEV` | `1` = 没配 SMTP 时把邮件写日志、接口回可点链接；生产应设 `0` | `1` |
| `BOTLY_BASE_URL` | 审批邮件中的站点对外地址，生产必配；认证邮件未配时按请求头推导 | 未设置 |
| `BOTLY_RESET_TOKEN_TTL_MINUTES` | 重置密码链接有效期 | 60 |
| `BOTLY_VERIFY_TOKEN_TTL_HOURS` | 邮箱验证链接有效期 | 24 |
| `BOTLY_LINUX_SANDBOX_ENABLED` | 是否启用常驻 Linux 环境（建群时预热容器） | `1` |
| `BOTLY_SANDBOX_PROVIDER` | `none` 未接入 / `docker` 本机测试 / `external` 云适配器 | `none` |
| `BOTLY_SANDBOX_ADAPTER` | 受信任服务端适配器，`包.模块:类名` | 未设置 |
| `BOTLY_LINUX_SANDBOX_IMAGE` | 沙箱镜像（预装 python3/node/git/jq 等） | `botly-sandbox:24.04` |
| `BOTLY_LINUX_SANDBOX_BASELINE_IMAGE` | 预装镜像缺失时的兜底镜像 | `ubuntu:24.04` |
| `BOTLY_LINUX_SANDBOX_TIMEOUT` | 单条命令超时秒数 | 60 |
| `BOTLY_LINUX_SANDBOX_MEMORY` / `_CPUS` | 环境资源限制 | `1g` / `1.0` |
| `BOTLY_LINUX_SANDBOX_AUTO_INSTALL` | 缺 python3/node 时自动 apt 安装 | `1` |
| `BOTLY_CODE_EXEC_LEVEL` | `code_exec` 的权限级别（`L2` 默认免审批 / `L3` 默认审批，支持群聊工具白名单） | `L2` |
| `BOTLY_GOOGLE_CLIENT_ID` / `_SECRET` | Google 登录（留空则不显示该按钮） | 未设置 |
| `BOTLY_GITHUB_CLIENT_ID` / `_SECRET` | GitHub 登录（留空则不显示该按钮） | 未设置 |
| `BOTLY_OAUTH_REDIRECT_BASE` | 回调地址前缀，必须与 OAuth 后台登记一致 | 按请求头推导 |
| `BOTLY_OAUTH_ALLOW_SIGNUP` | 陌生账号能否用第三方登录直接建号 | `1` |

限额、重试、超时等默认值集中在 `server/app/core/config.py`，对应文档附录 A。

## 数据迁移（Alembic）

从 2026-11 起，复杂 schema 变更走 Alembic。`server/alembic/versions/0001_baseline.py` 是
当前 schema 的标记点（不执行 DDL）；老库首次启动会自动 `stamp head` 建立版本表，
之后在 `server/` 目录下：

```bash
./venv/bin/alembic revision --autogenerate -m "加 xx 列"   # 生成迁移
./venv/bin/alembic upgrade head                            # 应用
./venv/bin/alembic downgrade -1                            # 回退一步
```

轻量的「加列」仍由启动时的 `_light_migrate()` 兜底（幂等），两者不冲突。
本次新增 `0002_approval_email`，API / Worker 启动会升级到 head；Postgres 迁移通过锁串行执行。

运行时长与步数已不作为停止条件，只保留统计。

**从 SQLite 迁移到 Postgres**：

```bash
psql -d postgres -c "CREATE ROLE botly LOGIN PASSWORD 'botly'"
psql -d postgres -c "CREATE DATABASE botly OWNER botly"   # 已存在则跳过
cd server
./venv/bin/python -m scripts.migrate_sqlite_to_postgres \
  --postgres "postgresql+psycopg://botly:botly@127.0.0.1:5432/botly"
```

迁移前确认没有排队/运行中/等待用户的执行；LangGraph 检查点不迁移（只对进行中的执行有意义）。
目标库已有数据时脚本会拒绝执行，确认覆盖再加 `--force`。

**监控指标**：`GET /api/metrics` 输出 Prometheus 文本格式（执行状态、队列深度、待处理审批/提问、
token/调用/失败计数）。未配置 `BOTLY_METRICS_TOKEN` 时仅回环地址可访问；远程抓取请设置该令牌，
Prometheus 用 `bearer_token` 或自定义 header 携带。

**备份**：`cd server && ./venv/bin/python -m scripts.backup_db` 备份到 `server/data/backups/`
（Postgres `pg_dump -Fc` / SQLite `.backup`），默认保留最近 7 份；`--list` 查看、`--restore <名字>`
打印恢复命令。建议配 cron 每日执行一次。

## 常见问题

**任务一直停在「排队中」**
Worker 没启动。执行是由 Worker 消费的，只起 API 不会推进。跑 `cd server && ./run-worker.sh`。

**`/api/health` 返回 `redis:false`**
`.env` 里的 `BOTLY_REDIS_URL` 与容器实际密码不一致。密码由本机容器的 `--requirepass`
决定，连接串形如 `redis://:密码@127.0.0.1:6379/0`。连不上时会降级为数据库轮询，功能仍可用。

**前端启动报 `EEXIST ... .next` 或 `CODEBUDDY_BROKER_DENY`**
上一次跑过 `next build`，残留的 `.next` 让 dev 启动时的缓存重建失败；在沙箱终端里还会被文件钩子拦截。
用 `web/dev.sh` 启动，它会先清缓存并解除这两个环境变量。

**解散群聊时提示「该群聊有执行正在进行」**
按 CHT-03 同一群聊只允许一个执行，先点「停止」再解散。解散会删除该群聊的消息、子任务、
运行记录、产物文件与沙箱目录，不可恢复。

**端口被占用**
Web 默认 3200（3000/3100 常被其他服务占）。改 `web/package.json` 的 `dev` 脚本或
`PORT=3300 ./web/dev.sh`。API 用 `PORT=8200 ./server/run-api.sh`，同时把 `web/next.config.ts`
里的 `BOTLY_API_URL` 指到新端口。

**登录后提示「尚未配置模型接入」**
这是 ACC-06 的预期行为。先到「设置 → 模型厂商」添加厂商，再到 Bot 里选择具体模型。

**添加厂商时提示 `HTTP 401` / `HTTP 404`**
`401` 是 Key 不对（大小写、空格、是否已删除）；`404` 多半是 base_url 少了 `/v1` 或路径不对。
两种情况都不会阻断保存，会退回预置模型列表，修好 Key 后点「刷新模型」即可。

## 已知限制

1. **演示模型（mock）是脚本化的**，只用于无 Key 时走通流程，不具备真实推理能力。
2. **网页搜索**优先使用 Tavily Key，未配置时仍可选并回退 DuckDuckGo，免密钥结果可能不稳定。
3. **token 估算**优先用模型返回的 usage，缺失时按 CJK 字数估算，与真实计费可能有偏差。
4. **子任务按计划批次并行**（RUN-12）：同一 batch 并发、不同 batch 串行，逐步确认模式保持串行；
   并行步骤写同一个文件仍可能互相覆盖，靠提示词约束「只做本步骤」。
5. `docker-compose.yml` 是部署参考，本次未在本机跑通整条容器链路。
6. 上传的图片存在 `data/uploads/{user_id}/`，通过带鉴权的 `/api/uploads/{id}` 读取（不同账号互相看不到）；
   尚未做图片压缩与 CDN，头像请控制在 2 MB 以内。
7. CubeSandbox 直连接口已实现，实际服务地址和模板需在设置页填写并测试；Docker 为本机测试实现。
