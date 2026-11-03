"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Copy, Loader2, RefreshCw, Search, ScrollText, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import PageHeader from "@/components/ui/PageHeader";
import Select from "@/components/ui/Select";
import { api, toastError } from "@/lib/api";
import { toast } from "@/lib/ui";
import type { LogEntry, LogModule, LogPage } from "@/lib/types";

const LEVELS = [
  { value: "", label: "全部" },
  { value: "DEBUG", label: "DEBUG" },
  { value: "INFO", label: "INFO" },
  { value: "WARNING", label: "WARNING" },
  { value: "ERROR", label: "ERROR" },
  { value: "CRITICAL", label: "CRITICAL" },
];

const LEVEL_CLS: Record<string, string> = {
  DEBUG: "text-faint",
  INFO: "text-muted",
  WARNING: "text-warn",
  ERROR: "text-danger",
  CRITICAL: "text-danger",
};

const PAGE = 300;

/** SQLAlchemy 的 SQL 回显属于实现细节，日志页不展示（后端读取时也会跳过） */
const isDbLog = (module: string) => module.startsWith("sqlalchemy");
/** 超过这个长度的消息先折成两行，避免一条超长指令把整页撑开 */
const CLAMP_AT = 180;

export default function LogsPage() {
  // 默认不筛选：和直接看 data/logs/app.log 看到的内容一致，
  // 要排查问题再切到 WARNING / ERROR。
  const [level, setLevel] = useState("");
  const [module, setModule] = useState("");
  const [q, setQ] = useState("");
  const [requestId, setRequestId] = useState("");
  const [runId, setRunId] = useState("");
  const [data, setData] = useState<LogPage | null>(null);
  const [modules, setModules] = useState<LogModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [limit, setLimit] = useState(PAGE);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(
    async (nextLimit = limit, silent = false) => {
      const sequence = ++requestSequence.current;
      if (!silent) setLoading(true);
      try {
        const page = await api.logs({ level, module, q, request_id: requestId, run_id: runId, limit: nextLimit });
        if (sequence === requestSequence.current) setData(page);
      } catch (e: any) {
        if (sequence === requestSequence.current && !silent) toastError(e);
      } finally {
        if (sequence === requestSequence.current) setLoading(false);
      }
    },
    [level, module, q, requestId, runId, limit]
  );

  useEffect(() => () => { requestSequence.current += 1; }, []);

  useEffect(() => {
    setLimit(PAGE);
    load(PAGE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [level, module, requestId, runId]);

  // 关键字搜索加防抖，避免每敲一个字就读一遍日志文件
  useEffect(() => {
    const t = setTimeout(() => load(limit), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  useEffect(() => {
    api.logModules().then(setModules).catch(() => {});
  }, []);

  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(() => load(limit, true), 5000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, limit, level, module, q, requestId, runId]);

  const items = useMemo(
    () => (data?.items || []).filter((r) => !isDbLog(r.module) && (!level || r.level === level)),
    [data, level]
  );
  // 后端已折叠数据库语句；前端再兜一层，保证接口没重启时也不会看到 SQL 刷屏
  const hiddenSql = Math.max(
    data?.hidden_sql || 0,
    (data?.items || []).filter((r) => isDbLog(r.module)).length
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((it) => (c[it.level] = (c[it.level] || 0) + 1));
    return c;
  }, [items]);

  const hasFilter = !!(level || module || q || requestId || runId);
  const clearFilters = () => {
    setLevel("");
    setModule("");
    setQ("");
    setRequestId("");
    setRunId("");
  };

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <PageHeader
            eyebrow="OPS / LOGS"
            title="运行日志"
            description={
              <>
                和 <code className="font-mono text-[11.5px] text-muted">data/logs/app.log</code>{" "}
                同源：时间、级别、模块、pid 与 req / run 编号都保留。API 请求会拆成方法、路径、状态与耗时；
                堆栈等多行内容可展开查看。
              </>
            }
            actions={
              <>
                <button
                  type="button"
                  role="switch"
                  aria-checked={auto}
                  aria-label="自动刷新"
                  onClick={() => setAuto((value) => !value)}
                  className="flex min-h-10 select-none items-center gap-2.5 rounded-lg px-2 text-[12px] text-muted transition-colors hover:bg-panel2 hover:text-ink"
                >
                  <span
                    aria-hidden="true"
                    className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-200 ${auto ? "border-accent bg-accent" : "border-line2 bg-panel3"}`}
                  >
                    <span className={`absolute left-0.5 h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${auto ? "translate-x-4" : "translate-x-0"}`} />
                  </span>
                  自动刷新
                </button>
                <button onClick={() => load(limit)} title="刷新" className="icon-btn">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                </button>
              </>
            }
          />

          {/*{data && (*/}
          {/*  <div className="mt-2 text-[11px] text-faint">*/}
          {/*    日志目录 <code className="font-mono">{data.log_dir}</code>*/}
          {/*    {data.files.length > 0 && ` · ${data.files.length} 个文件（单文件上限 ${data.max_file_mb} MB）`}*/}
          {/*    {hiddenSql > 0 && " · 数据库语句已折叠"}*/}
          {/*  </div>*/}
          {/*)}*/}

          {/* 筛选 */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-[9px] border border-line bg-panel px-2.5 transition-colors">
              <Search size={13} className="text-faint" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索内容…"
                className="w-52 bg-transparent py-1.5 text-[12px] outline-none placeholder:text-faint"
              />
              {q && (
                <button onClick={() => setQ("")} className="text-faint hover:text-ink">
                  <X size={12} />
                </button>
              )}
            </div>
            <Select
              value={level}
              onChange={setLevel}
              options={LEVELS}
              size="sm"
              className="w-[148px]"
              placeholder="全部"
            />
            <Select
              value={module}
              onChange={setModule}
              options={[
                { value: "", label: "全部模块" },
                ...modules.map((m) => ({
                  value: m.module,
                  label: m.module,
                  description: `${m.count} 条`,
                })),
              ]}
              size="sm"
              searchable
              className="w-[176px]"
              placeholder="全部模块"
            />
            {hasFilter && (
              <button onClick={clearFilters} className="text-[11.5px] text-accent hover:underline">
                清除筛选
              </button>
            )}
            <div className="ml-auto flex items-center gap-2 text-[10.5px] text-faint">
              {["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"].map((lv) =>
                counts[lv] ? (
                  <span key={lv} className={LEVEL_CLS[lv]}>
                    {lv} {counts[lv]}
                  </span>
                ) : null
              )}
            </div>
          </div>

          {(requestId || runId) && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11.5px]">
              {requestId && (
                <span className="flex items-center gap-1 rounded-md bg-info/10 px-2 py-1 text-info">
                  请求 <code className="font-mono">{requestId}</code>
                  <button onClick={() => setRequestId("")} className="hover:text-ink">
                    <X size={11} />
                  </button>
                </span>
              )}
              {runId && (
                <span className="flex items-center gap-1 rounded-md bg-info/10 px-2 py-1 text-info">
                  执行 <code className="font-mono">{runId}</code>
                  <button onClick={() => setRunId("")} className="hover:text-ink">
                    <X size={11} />
                  </button>
                </span>
              )}
            </div>
          )}

          {/* 列表 */}
          <div className="mt-3 overflow-hidden rounded-[14px] border border-line bg-panel shadow-card">
            {loading && !items.length ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={18} className="animate-spin text-faint" />
              </div>
            ) : !items.length ? (
              <div className="px-6 py-16 text-center">
                <div className="empty-icon">
                  <ScrollText size={20} />
                </div>
                <div className="mt-3 text-[13px] font-medium">
                  {hasFilter ? "没有匹配的日志" : "还没有日志"}
                </div>
                <p className="mx-auto mt-1 max-w-sm text-[12px] leading-relaxed text-muted">
                  {hasFilter
                    ? "换个关键字或级别再试，或清除筛选查看全部。"
                    : "后端处理请求或执行任务后，日志会写入 data/logs/app.log。"}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-line/60">
                {items.map((row, i) => {
                  const date = row.time.slice(0, 10);
                  const newDay = i === 0 || items[i - 1].time.slice(0, 10) !== date;
                  return (
                    <Fragment key={`${row.time}-${i}`}>
                      {newDay && (
                        <div className="bg-panel2/70 px-3 py-1 text-[10.5px] font-medium text-faint">
                          {date}
                        </div>
                      )}
                      <LogRow
                        row={row}
                        onRequest={(id) => setRequestId(id)}
                        onRun={(id) => setRunId(id)}
                      />
                    </Fragment>
                  );
                })}
              </div>
            )}
          </div>

          {data?.has_more && (
            <button
              className="btn-ghost mx-auto mt-3 block text-[11.5px]"
              disabled={loading}
              onClick={() => {
                const next = limit + PAGE;
                setLimit(next);
                load(next);
              }}
            >
              {loading ? "加载中…" : "加载更多"}
            </button>
          )}
        </div>
      </div>
    </AppShell>
  );
}

type AccessInfo = {
  method: string;
  path: string;
  status: number;
  ms: number;
  size: string;
  ip: string;
  uid: string;
  ua: string;
  query: string;
  body: string;
};

/** 后端 access 日志的固定格式，拆开后才不会糊成一长串。
 *  METHOD path -> 状态 耗时ms size=... ip=... uid=... [ua='...'][ query=...][ body=...]
 */
function parseAccess(message: string): AccessInfo | null {
  const m = message.match(
    /^([A-Z]+) (\S+) -> (\d{3}) (\d+)ms size=(\S+) ip=(\S+) uid=(\S+)([\s\S]*)$/
  );
  if (!m) return null;
  const rest = m[8] || "";
  // body 里可能有空格和引号，用第一个 " body=" 切开，后续内容整体当 body
  const bodyAt = rest.indexOf(" body=");
  const head = bodyAt >= 0 ? rest.slice(0, bodyAt) : rest;
  return {
    method: m[1],
    path: m[2],
    status: Number(m[3]),
    ms: Number(m[4]),
    size: m[5],
    ip: m[6],
    uid: m[7],
    ua: head.match(/\bua='([^']*)'/)?.[1] || "",
    query: head.match(/\bquery=(\S+)/)?.[1] || "",
    body: bodyAt >= 0 ? rest.slice(bodyAt + 6) : "",
  };
}

function prettyBody(body: string) {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

/** 还原成和 data/logs/app.log 一模一样的一行，方便贴到别处或对比 */
function rawLine(row: LogEntry) {
  return `${row.time} ${row.level.padEnd(7)} pid=${row.pid || "-"} [${row.module}] req=${row.request_id} run=${row.run_id} task=${row.task_id} ${row.message}`;
}

function statusClass(status: number) {
  if (status >= 500) return "text-danger";
  if (status >= 400) return "text-warn";
  return "text-ok";
}

function LogRow({ row, onRequest, onRun }: { row: LogEntry; onRequest: (id: string) => void; onRun: (id: string) => void }) {
  const isError = row.level === "ERROR" || row.level === "CRITICAL";
  const isWarn = row.level === "WARNING";
  const [open, setOpen] = useState(false);
  const access = row.module === "access" ? parseAccess(row.message) : null;
  const multiline = row.message.includes("\n");
  const long = row.message.length > CLAMP_AT || multiline;
  const detail = !!access && !!(access.query || access.body || access.ua);

  const copy = () => {
    const text = rawLine(row);
    const fail = () => toast("复制失败，请手动选择文本", "error");
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(() => toast("已复制原始日志行", "ok"), fail);
    else fail();
  };

  return (
    <div
      className={`group flex gap-2 px-3 py-1.5 font-mono text-[11.5px] leading-relaxed ${
        isError ? "bg-danger/[0.04]" : isWarn ? "bg-warn/[0.04]" : ""
      }`}
    >
      <span className="w-[56px] shrink-0 text-faint" title={row.time}>
        {row.time.slice(11)}
      </span>
      <span className={`w-[58px] shrink-0 ${LEVEL_CLS[row.level] || "text-muted"}`}>{row.level}</span>
      <span className="w-[76px] shrink-0 truncate text-info/80" title={row.module}>
        {row.module}
      </span>
      <div className="min-w-0 flex-1 text-ink/90">
        {access ? (
          <>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="rounded bg-info/10 px-1 text-[10px] font-semibold text-info">
                {access.method}
              </span>
              <span className="break-all">{access.path}</span>
              <span className={`font-semibold ${statusClass(access.status)}`}>{access.status}</span>
              <span className={access.ms >= 1000 ? "text-warn" : "text-faint"}>{access.ms}ms</span>
              <span className="text-faint">size={access.size}</span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10.5px] text-faint">
              <span>ip={access.ip}</span>
              <span>uid={access.uid}</span>
              {detail && (
                <button onClick={() => setOpen((v) => !v)} className="text-info/80 hover:text-info">
                  {open ? "收起详情" : "请求详情"}
                </button>
              )}
            </div>
            {open && detail && (
              <div className="mt-1 space-y-1 rounded-md border border-line bg-panel2/60 p-2 text-[10.5px] text-muted">
                {access.query && (
                  <div>
                    <span className="text-faint">query </span>
                    <span className="break-all">{access.query}</span>
                  </div>
                )}
                {access.body && (
                  <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all">
                    {prettyBody(access.body)}
                  </pre>
                )}
                {access.ua && <div className="break-all text-faint">{access.ua}</div>}
              </div>
            )}
          </>
        ) : (
          <>
            <div className={long && !open ? "line-clamp-3 break-words" : "whitespace-pre-wrap break-words"}>
              {row.message}
            </div>
            {long && (
              <button
                onClick={() => setOpen((v) => !v)}
                className="mt-0.5 text-[10.5px] text-info/80 hover:text-info"
              >
                {open ? "收起" : multiline ? "展开完整内容（含堆栈）" : `展开（${row.message.length} 字）`}
              </button>
            )}
          </>
        )}
        <div className="mt-0.5 flex flex-wrap gap-2 text-[10.5px] text-faint">
          {row.pid && <span title="后端进程号">pid={row.pid}</span>}
          {row.request_id !== "-" && (
            <button onClick={() => onRequest(row.request_id)} className="hover:text-info" title="只看这个请求">
              req={row.request_id}
            </button>
          )}
          {row.run_id !== "-" && (
            <button onClick={() => onRun(row.run_id)} className="hover:text-info" title="只看这次执行">
              run={row.run_id}
            </button>
          )}
          {row.task_id !== "-" && <span>task={row.task_id}</span>}
        </div>
      </div>
      <button
        onClick={copy}
        title="复制与 data/logs/app.log 一致的原始行"
        className="mt-0.5 shrink-0 text-faint opacity-50 hover:text-ink hover:opacity-100"
      >
        <Copy size={12} />
      </button>
      {(isError || isWarn) && (
        <AlertTriangle size={12} className={`mt-1 shrink-0 ${isError ? "text-danger" : "text-warn"}`} />
      )}
    </div>
  );
}
