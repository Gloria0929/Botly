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
  X,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import PageHeader from "@/components/ui/PageHeader";
import DailyChart from "@/components/ui/DailyChart";
import { api, toastError } from "@/lib/api";
import type { StatsOverview } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";

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
  // 已关闭的告警：先本地隐藏再回写，点 X 立刻消失，不用等下一次刷新
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const load = (d = days) => {
    setLoading(true);
    api
      .statsOverview(d)
      .then(setData)
      .catch((e) => toastError(e))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load(days);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const alerts = useMemo(
    () => (data?.alerts || []).filter((a) => !a.acknowledged && !dismissed.has(a.id)),
    [data, dismissed]
  );

  const markDismissed = (ids: string[], on: boolean) =>
    setDismissed((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
      return next;
    });

  /** 关闭一条告警：先乐观隐藏，接口失败再放回来（失败原因由 api 统一提示） */
  const dismiss = async (id: string) => {
    markDismissed([id], true);
    try {
      await api.acknowledgeAlert(id);
    } catch {
      markDismissed([id], false);
    }
  };

  const dismissAll = async () => {
    const ids = alerts.map((a) => a.id);
    if (!ids.length) return;
    markDismissed(ids, true);
    const results = await Promise.allSettled(ids.map((id) => api.acknowledgeAlert(id)));
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    if (failed.length) markDismissed(failed, false);
  };
  const maxFail = Math.max(1, ...(data?.failures || []).map((f) => f.count));
  const activeDays = (data?.daily || []).filter(
    (d) => d.completed + d.failed + d.stopped + d.other > 0
  ).length;
  const t = data?.totals;
  const empty = !loading && data && (t?.runs || 0) === 0;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <PageHeader
            eyebrow="OPS / HEALTH"
            title="运行状况"
            description="执行完成率、失败原因分布与系统告警；统计只包含你自己的执行。"
            actions={
              <>
                <div className="segmented">
                  {RANGES.map((r) => (
                    <button
                      key={r.days}
                      onClick={() => setDays(r.days)}
                      className={`segmented-item ${
                        days === r.days ? "segmented-item-active" : ""
                      }`}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
                <button onClick={() => load()} title="刷新" className="icon-btn">
                  {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                </button>
              </>
            }
          />

          {alerts.length > 0 && (
            <div className="mt-4">
              {alerts.length > 1 && (
                <div className="mb-2 flex items-center justify-end">
                  <button
                    onClick={dismissAll}
                    className="text-[11px] text-faint transition-colors hover:text-ink"
                  >
                    全部忽略（{alerts.length}）
                  </button>
                </div>
              )}
              <div className="space-y-2">
                {alerts.map((a) => (
                  <div
                    key={a.id}
                    className={`flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 ${
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
                    <div className="flex shrink-0 items-center gap-1">
                      <span className="text-[10.5px] text-faint">
                        {a.created_at ? timeAgo(a.created_at) : ""}
                      </span>
                      <button
                        onClick={() => dismiss(a.id)}
                        title="关闭这条告警"
                        aria-label="关闭这条告警"
                        className={`rounded-md p-1 text-faint transition-colors hover:bg-panel hover:text-ink ${
                          a.level === "critical" ? "hover:text-danger" : ""
                        }`}
                      >
                        <X size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {empty ? (
            <div className="empty-state mt-10">
              <div className="empty-icon">
                <Activity size={20} />
              </div>
              <div className="mt-3 text-[13px] font-medium">这段时间还没有执行</div>
              <p className="mx-auto mt-1 max-w-xs text-[12px] leading-relaxed text-muted">
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
                  <h2 className="section-title">每日执行</h2>
                  <span className="text-[10.5px] text-faint">
                    {data?.daily.length || 0} 天 · 有执行 {activeDays} 天
                  </span>
                </div>
                <DailyChart
                  days={(data?.daily || []).map((d) => {
                    const stopped = d.stopped + d.other;
                    return {
                      date: d.date,
                      segments: [
                        { label: "完成", value: d.completed, className: "bg-ok" },
                        { label: "失败", value: d.failed, className: "bg-danger" },
                        { label: "停止", value: stopped, className: "bg-faint" },
                      ],
                      detail: `完成 ${d.completed} · 失败 ${d.failed} · 停止 ${stopped}`,
                    };
                  })}
                  format={(n) => String(n)}
                  unit="次"
                  legend={[
                    { label: "完成", className: "bg-ok" },
                    { label: "失败", className: "bg-danger" },
                    { label: "停止", className: "bg-faint" },
                  ]}
                />
              </section>

              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                {/* 失败原因 */}
                <section>
                  <h2 className="section-title mb-2">失败原因</h2>
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
                  <h2 className="section-title mb-2">最近失败</h2>
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
    <div className="card card-hover px-4 py-3.5">
      <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.16em] text-faint">
        <span className="text-accent">{icon}</span>
        {label}
      </div>
      <div className="mt-2.5 text-[24px] font-bold leading-none tabular-nums tracking-[-0.03em]">
        {value}
      </div>
      {hint && <div className="mt-1.5 text-[10.5px] leading-relaxed text-faint">{hint}</div>}
    </div>
  );
}
