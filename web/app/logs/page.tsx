"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, Search, ScrollText, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import Select from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { LogEntry, LogModule, LogPage } from "@/lib/types";
import { toast } from "@/lib/ui";

const LEVELS = [
  { value: "", label: "全部" },
  { value: "DEBUG", label: "DEBUG 及以上" },
  { value: "INFO", label: "INFO 及以上" },
  { value: "WARNING", label: "WARNING 及以上" },
  { value: "ERROR", label: "ERROR" },
];

const LEVEL_CLS: Record<string, string> = {
  DEBUG: "text-faint",
  INFO: "text-muted",
  WARNING: "text-warn",
  ERROR: "text-danger",
  CRITICAL: "text-danger",
};

const PAGE = 300;

export default function LogsPage() {
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

  const load = useCallback(
    async (nextLimit = limit, silent = false) => {
      if (!silent) setLoading(true);
      try {
        const page = await api.logs({ level, module, q, request_id: requestId, run_id: runId, limit: nextLimit });
        setData(page);
      } catch (e: any) {
        if (!silent) toast(e.message, "error");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [level, module, q, requestId, runId, limit]
  );

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

  const items = data?.items || [];
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
        <div className="mx-auto max-w-5xl px-6 py-8">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">运行日志</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                API 请求、Agent 执行链路与安全事件。每行带 <code className="font-mono">req=</code> /{" "}
                <code className="font-mono">run=</code> 编号，点编号可只保留同一请求或同一次执行。
              </p>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <label className="flex cursor-pointer select-none items-center gap-1.5 text-[11.5px] text-muted">
                <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
                自动刷新
              </label>
              <button onClick={() => load(limit)} title="刷新" className="p-1.5 text-faint hover:text-ink">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              </button>
            </div>
          </div>

          {data && (
            <div className="mt-2 text-[11px] text-faint">
              日志目录 <code className="font-mono">{data.log_dir}</code>
              {data.files.length > 0 && ` · ${data.files.length} 个文件（单文件上限 ${data.max_file_mb} MB）`}
            </div>
          )}

          {/* 筛选 */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-2">
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
              {["ERROR", "WARNING", "INFO", "DEBUG"].map((lv) =>
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
                <span className="flex items-center gap-1 rounded-md bg-accent/10 px-2 py-1 text-accent">
                  请求 <code className="font-mono">{requestId}</code>
                  <button onClick={() => setRequestId("")} className="hover:text-ink">
                    <X size={11} />
                  </button>
                </span>
              )}
              {runId && (
                <span className="flex items-center gap-1 rounded-md bg-accent/10 px-2 py-1 text-accent">
                  执行 <code className="font-mono">{runId}</code>
                  <button onClick={() => setRunId("")} className="hover:text-ink">
                    <X size={11} />
                  </button>
                </span>
              )}
            </div>
          )}

          {/* 列表 */}
          <div className="mt-3 overflow-hidden rounded-xl border border-line bg-panel">
            {loading && !items.length ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={18} className="animate-spin text-faint" />
              </div>
            ) : !items.length ? (
              <div className="px-6 py-16 text-center">
                <ScrollText size={22} className="mx-auto text-faint" />
                <div className="mt-2.5 text-[13px] font-medium">
                  {hasFilter ? "没有匹配的日志" : "还没有日志"}
                </div>
                <p className="mt-1 text-[12px] text-muted">
                  {hasFilter
                    ? "换个关键字或级别再试，或清除筛选查看全部。"
                    : "后端处理请求或执行任务后，日志会写入 data/logs/app.log。"}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-line/60">
                {items.map((row, i) => (
                  <LogRow key={`${row.time}-${i}`} row={row} onRequest={(id) => setRequestId(id)} onRun={(id) => setRunId(id)} />
                ))}
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

function LogRow({ row, onRequest, onRun }: { row: LogEntry; onRequest: (id: string) => void; onRun: (id: string) => void }) {
  const isError = row.level === "ERROR" || row.level === "CRITICAL";
  const isWarn = row.level === "WARNING";
  return (
    <div
      className={`flex gap-2 px-3 py-1.5 font-mono text-[11.5px] leading-relaxed ${
        isError ? "bg-danger/[0.04]" : isWarn ? "bg-warn/[0.04]" : ""
      }`}
    >
      <span className="shrink-0 text-faint">{row.time.slice(11)}</span>
      <span className={`w-[62px] shrink-0 ${LEVEL_CLS[row.level] || "text-muted"}`}>{row.level}</span>
      <span className="w-[76px] shrink-0 truncate text-accent/80" title={row.module}>
        {row.module}
      </span>
      <div className="min-w-0 flex-1 break-words text-ink/90">
        <span>{row.message}</span>
        <div className="mt-0.5 flex flex-wrap gap-2 text-[10.5px] text-faint">
          {row.request_id !== "-" && (
            <button onClick={() => onRequest(row.request_id)} className="hover:text-accent" title="只看这个请求">
              req={row.request_id}
            </button>
          )}
          {row.run_id !== "-" && (
            <button onClick={() => onRun(row.run_id)} className="hover:text-accent" title="只看这次执行">
              run={row.run_id}
            </button>
          )}
          {row.task_id !== "-" && <span>task={row.task_id}</span>}
        </div>
      </div>
      {(isError || isWarn) && (
        <AlertTriangle size={12} className={`mt-1 shrink-0 ${isError ? "text-danger" : "text-warn"}`} />
      )}
    </div>
  );
}
