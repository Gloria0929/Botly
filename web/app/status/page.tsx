"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Loader2,
  RefreshCw,
  ServerCrash,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import type { StatsOverview } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

const RANGES = [
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 90, label: "近 90 天" },
];

function duration(sec: number): string {
  if (!sec) return "—";
  if (sec < 60) return `${Math.round(sec)} 秒`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return s ? `${m} 分 ${s} 秒` : `${m} 分`;
}

export default function StatusPage() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<StatsOverview | null>(null);
  const [loading, setLoading] = useState(true);

  const load = (d = days) => {
    setLoading(true);
    api
      .statsOverview(d)
      .then(setData)
      .catch((e: any) => toast(e.message, "error"))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load(days);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const alerts = useMemo(
    () => (data?.alerts || []).filter((a) => !a.acknowledged),
    [data]
  );
  const maxDaily = Math.max(
    1,
    ...(data?.daily || []).map((d) => d.completed + d.failed + d.stopped + d.other)
  );
  const maxFail = Math.max(1, ...(data?.failures || []).map((f) => f.count));
  const t = data?.totals;
  const empty = !loading && data && (t?.runs || 0) === 0;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-8">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">运行状况</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                执行完成率、失败原因分布与系统告警；统计只包含你自己的执行。
              </p>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <div className="flex rounded-lg border border-line p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r.days}
                    onClick={() => setDays(r.days)}
                    className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${
                      days === r.days ? "bg-accent/15 text-accent" : "text-muted hover:text-ink"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              <button onClick={() => load()} title="刷新" className="p-1.5 text-faint hover:text-ink">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              </button>
            </div>
          </div>

          {alerts.length > 0 && (
            <div className="mt-4 space-y-2">
              {alerts.map((a) => (
                <div
                  key={a.id}
                  className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 ${
                    a.level === "critical"
                      ? "border-danger/35 bg-danger/[0.07]"
                      : "border-warn/35 bg-warn/[0.07]"
                  }`}
                >
                  <AlertTriangle
                    size={15}
                    className={`mt-0.5 shrink-0 ${
                      a.level === "critical" ? "text-danger" : "text-warn"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div
                      className={`text-[12.5px] font-medium ${
                        a.level === "critical" ? "text-danger" : "text-warn"
                      }`}
                    >
                      {a.title}
                    </div>
                    <div className="mt-0.5 text-[11.5px] leading-relaxed text-muted">{a.body}</div>
                  </div>
                  <span className="shrink-0 text-[10.5px] text-faint">
                    {a.created_at ? timeAgo(a.created_at) : ""}
                  </span>
                </div>
              ))}
            </div>
          )}

          {empty ? (
            <div className="mt-10 rounded-xl border border-dashed border-line px-6 py-14 text-center">
              <Activity size={22} className="mx-auto text-faint" />
              <div className="mt-2.5 text-[13px] font-medium">这段时间还没有执行</div>
              <p className="mt-1 text-[12px] text-muted">
                发起一次任务后，这里会显示完成率、失败原因与队列状况。
              </p>
            </div>
          ) : (
            <>
              <div className="mt-5 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                <Stat
                  icon={<CheckCircle2 size={14} />}
                  label="完成率"
                  value={`${((t?.success_rate || 0) * 100).toFixed(0)}%`}
                  hint={`${t?.completed || 0} 完成 · ${t?.failed || 0} 失败 · ${t?.stopped || 0} 停止`}
                />
                <Stat
                  icon={<ServerCrash size={14} />}
                  label="失败执行"
                  value={String(t?.failed || 0)}
                  hint="按结束原因归类"
                />
                <Stat
                  icon={<Clock3 size={14} />}
                  label="平均耗时"
                  value={duration(t?.avg_duration_sec || 0)}
                  hint={`平均 ${t?.avg_steps || 0} 步 · ${Math.round(t?.avg_tokens || 0)} tokens`}
                />
                <Stat
                  icon={<Activity size={14} />}
                  label="当前队列"
                  value={String(t?.queued || 0)}
                  hint={`Redis ${data?.queue.depth || 0} · 运行中 ${t?.running || 0}`}
                />
              </div>

              {/* 每日执行趋势 */}
              <section className="mt-6">
                <div className="mb-2 flex items-end justify-between">
                  <h2 className="text-[13.5px] font-medium">每日执行</h2>
                  <span className="text-[10.5px] text-faint">峰值 {maxDaily} 次/天</span>
                </div>
                <div className="card p-4">
                  <div className="flex items-center gap-3 text-[10.5px] text-faint">
                    <span className="flex items-center gap-1">
                      <span className="h-2 w-2 rounded-sm bg-ok" />
                      完成
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="h-2 w-2 rounded-sm bg-danger" />
                      失败
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="h-2 w-2 rounded-sm bg-faint" />
                      停止
                    </span>
                  </div>
                  <div
                    className="mx-auto mt-3 grid h-[120px] items-end gap-[3px]"
                    style={{
                      gridTemplateColumns: `repeat(${Math.max(1, data?.daily.length || 1)}, minmax(0, 1fr))`,
                      maxWidth: Math.max(1, data?.daily.length || 1) * 47,
                    }}
                  >
                    {(data?.daily || []).map((d) => {
                      const total = d.completed + d.failed + d.stopped + d.other;
                      const h = total ? Math.max(2, Math.round((total / maxDaily) * 120)) : 2;
                      return (
                        <div
                          key={d.date}
                          title={`${d.date} · 完成 ${d.completed} / 失败 ${d.failed} / 停止 ${d.stopped}`}
                          className="flex flex-col-reverse overflow-hidden rounded-t-[3px]"
                          style={{ height: h }}
                        >
                          {d.completed > 0 && (
                            <div className="bg-ok" style={{ height: `${(d.completed / total) * 100}%` }} />
                          )}
                          {d.failed > 0 && (
                            <div className="bg-danger" style={{ height: `${(d.failed / total) * 100}%` }} />
                          )}
                          {d.stopped + d.other > 0 && (
                            <div
                              className="bg-faint"
                              style={{ height: `${((d.stopped + d.other) / total) * 100}%` }}
                            />
                          )}
                          {total === 0 && <div className="h-full bg-panel2" />}
                        </div>
                      );
                    })}
                  </div>
                  <div
                    className="mx-auto mt-2 flex justify-between text-[10px] text-faint"
                    style={{ maxWidth: Math.max(1, data?.daily.length || 1) * 47 }}
                  >
                    <span>{data?.daily[0]?.date.slice(5)}</span>
                    <span>{data?.daily[data.daily.length - 1]?.date.slice(5)}</span>
                  </div>
                </div>
              </section>

              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                {/* 失败原因 */}
                <section>
                  <h2 className="mb-2 text-[13.5px] font-medium">失败原因</h2>
                  <div className="card p-4">
                    {(data?.failures || []).map((f) => (
                      <div key={f.reason} className="mt-2.5 first:mt-0">
                        <div className="flex items-center justify-between gap-2 text-[11.5px]">
                          <span className="min-w-0 truncate text-muted" title={f.reason}>
                            {f.reason}
                          </span>
                          <span className="shrink-0 tabular-nums text-faint">{f.count}</span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-panel2">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${(f.count / maxFail) * 100}%`,
                              background: "var(--danger)",
                              opacity: 0.7,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                    {!data?.failures.length && (
                      <div className="py-6 text-center text-[11.5px] text-faint">
                        这段时间没有失败执行
                      </div>
                    )}
                  </div>
                </section>

                {/* 最近失败 */}
                <section>
                  <h2 className="mb-2 text-[13.5px] font-medium">最近失败</h2>
                  <div className="card overflow-hidden">
                    {(data?.recent_failures || []).map((r) => (
                      <div
                        key={r.run_id}
                        className="flex items-start gap-3 border-b border-line px-3.5 py-2.5 last:border-0"
                      >
                        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-danger" />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12px]">{r.chat_name}</div>
                          <div className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-faint">
                            {r.reason || "（没有记录原因）"}
                          </div>
                        </div>
                        <span className="shrink-0 text-[10.5px] text-faint">
                          {r.ended_at ? timeAgo(r.ended_at) : ""}
                        </span>
                      </div>
                    ))}
                    {!data?.recent_failures.length && (
                      <div className="py-6 text-center text-[11.5px] text-faint">没有失败记录</div>
                    )}
                  </div>
                </section>
              </div>

              <p className="mt-8 text-[10.5px] leading-relaxed text-faint">
                告警由 Worker 每分钟检查：队列积压 ≥ 10、执行运行超过 20 分钟、近 60 分钟失败率 ≥ 30%；
                同类告警 30 分钟内不重复。阈值可通过 `BOTLY_ALERT_*` 环境变量调整。
              </p>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function Stat({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="card px-3.5 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-faint">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-[19px] font-semibold tabular-nums tracking-tight">{value}</div>
      {hint && <div className="mt-0.5 text-[10.5px] text-faint">{hint}</div>}
    </div>
  );
}
