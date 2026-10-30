"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  Coins,
  Gauge,
  Loader2,
  RefreshCw,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import { api } from "@/lib/api";
import type { UsageSummary } from "@/lib/types";
import { timeAgo } from "@/lib/fmt";
import { toast } from "@/lib/ui";

const RANGES = [
  { days: 7, label: "近 7 天" },
  { days: 30, label: "近 30 天" },
  { days: 90, label: "近 90 天" },
];

const RUN_STATUS: Record<string, { label: string; cls: string }> = {
  completed: { label: "已完成", cls: "text-ok" },
  failed: { label: "失败", cls: "text-danger" },
  stopped: { label: "已停止", cls: "text-faint" },
  waiting: { label: "等待用户", cls: "text-warn" },
  running: { label: "运行中", cls: "text-accent" },
  queued: { label: "排队中", cls: "text-faint" },
};

function money(n: number): string {
  if (!n) return "$0.00";
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export default function UsagePage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const load = (d = days) => {
    setLoading(true);
    api
      .usage(d)
      .then(setData)
      .catch((e: any) => toast(e.message, "error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load(days);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const totals = data?.totals;
  const empty = !loading && data && totals?.calls === 0;
  const activeDays = (data?.daily || []).filter((d) => d.tokens > 0).length;

  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <h1 className="text-[20px] font-semibold tracking-tight">用量与费用</h1>
              <p className="mt-1 text-[12.5px] text-muted">
                按 Bot、模型与群聊统计 token；单价默认按厂商参考价预置，可在「设置 → 已添加的厂商 → 价格」修改。
              </p>
            </div>
            <div className="ml-auto flex items-center gap-1">
              <div className="flex rounded-lg border border-line p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r.days}
                    onClick={() => setDays(r.days)}
                    className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${
                      days === r.days
                        ? "bg-accent/15 text-accent"
                        : "text-muted hover:text-ink"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              <button
                onClick={() => load()}
                title="刷新"
                className="p-1.5 text-faint hover:text-ink"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              </button>
            </div>
          </div>

          {empty ? (
            <div className="mt-10 rounded-xl border border-dashed border-line px-6 py-14 text-center">
              <Gauge size={22} className="mx-auto text-faint" />
              <div className="mt-2.5 text-[13px] font-medium">这段时间还没有调用</div>
              <p className="mt-1 text-[12px] text-muted">
                发起一次执行后，这里会显示 token 用量、失败率与估算费用。
              </p>
            </div>
          ) : (
            <>
              {/* 概览 */}
              <div className="mt-6 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                <Stat
                  icon={<Coins size={14} />}
                  label="估算费用"
                  value={money(totals?.cost || 0)}
                  hint="按模型单价计算"
                />
                <Stat
                  icon={<Activity size={14} />}
                  label="总 token"
                  value={tokens(totals?.tokens || 0)}
                  hint={`输入 ${tokens(totals?.input_tokens || 0)} · 输出 ${tokens(
                    totals?.output_tokens || 0
                  )}`}
                />
                <Stat
                  icon={<Gauge size={14} />}
                  label="模型调用"
                  value={String(totals?.calls || 0)}
                  hint={`${totals?.runs || 0} 次执行 · 平均 ${totals?.avg_steps || 0} 步`}
                />
                <Stat
                  icon={
                    totals?.errors ? (
                      <AlertTriangle size={14} className="text-warn" />
                    ) : (
                      <ArrowDownRight size={14} />
                    )
                  }
                  label="错误率"
                  value={`${((totals?.error_rate || 0) * 100).toFixed(1)}%`}
                  hint={`${totals?.errors || 0} 次调用失败`}
                />
              </div>

              {!!totals?.unpriced_tokens && (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-warn/25 bg-warn/[0.06] px-3 py-2 text-[11.5px] text-warn/90">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span>
                    有 {tokens(totals.unpriced_tokens)} token 所在模型未填单价，未计入费用估算。
                  </span>
                </div>
              )}

              {/* 每日趋势 */}
              <section className="mt-7">
                <div className="mb-2 flex items-end justify-between">
                  <h2 className="text-[13.5px] font-medium">每日 token</h2>
                  <span className="text-[10.5px] text-faint">
                    {data?.daily.length || 0} 天 · 有调用 {activeDays} 天
                  </span>
                </div>
                <DailyChart daily={data?.daily || []} />
              </section>

              {/* 按 Bot */}
              <Section title="按 Bot" hint={`${data?.by_bot.length || 0} 个 Bot 有调用`}>
                {(data?.by_bot || []).map((b) => (
                  <Row
                    key={b.bot_id || b.name}
                    left={<Avatar value={b.avatar} size={24} alt={b.name} />}
                    title={b.name}
                    sub={`${b.calls} 次调用 · 输入 ${tokens(b.input_tokens)} / 输出 ${tokens(
                      b.output_tokens
                    )}`}
                    value={tokens(b.tokens)}
                    extra={money(b.cost)}
                  />
                ))}
                {!data?.by_bot.length && <EmptyRow />}
              </Section>

              {/* 按模型 */}
              <Section title="按模型" hint="错误率来自模型调用失败记录">
                {(data?.by_model || []).map((m) => (
                  <Row
                    key={`${m.provider_id}-${m.model}`}
                    left={
                      <span className="flex h-6 w-6 items-center justify-center rounded-md bg-panel2 text-[10px] text-muted">
                        AI
                      </span>
                    }
                    title={m.model || "（未指定模型）"}
                    sub={`${m.provider || "未知厂商"} · ${m.calls} 次调用${
                      m.errors ? ` · ${m.errors} 次失败` : ""
                    }${m.priced ? "" : " · 未填单价"}`}
                    value={tokens(m.tokens)}
                    extra={money(m.cost)}
                  />
                ))}
                {!data?.by_model.length && <EmptyRow />}
              </Section>

              {/* 按群聊 */}
              <Section title="按群聊" hint="token 与执行次数的分布">
                {(data?.by_chat || []).map((c) => (
                  <Row
                    key={c.chat_id}
                    left={
                      <span className="flex h-6 w-6 items-center justify-center rounded-md bg-panel2 text-[11px]">
                        💬
                      </span>
                    }
                    title={c.name}
                    sub={`${c.runs} 次执行`}
                    value={tokens(c.tokens)}
                    extra={money(c.cost)}
                  />
                ))}
                {!data?.by_chat.length && <EmptyRow />}
              </Section>

              {/* 最近执行 */}
              <Section title="最近执行" hint="只显示当前时间范围内的最近 12 次">
                {(data?.recent_runs || []).map((r) => {
                  const st = RUN_STATUS[r.status] || { label: r.status, cls: "text-faint" };
                  return (
                    <div
                      key={r.run_id}
                      className="flex items-center gap-3 border-b border-line px-3.5 py-2.5 last:border-0"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-[12.5px]">{r.trigger || "（无触发消息）"}</span>
                          <span className={`shrink-0 text-[10.5px] ${st.cls}`}>{st.label}</span>
                        </div>
                        <div className="mt-0.5 text-[10.5px] text-faint">
                          {r.chat_name} · {r.steps_used} 步 · {timeAgo(r.created_at)}
                        </div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="text-[12px] tabular-nums">{tokens(r.tokens)}</div>
                        <div className="text-[10.5px] text-faint">{money(r.cost)}</div>
                      </div>
                    </div>
                  );
                })}
                {!data?.recent_runs.length && <EmptyRow />}
              </Section>

              <p className="mt-8 text-[10.5px] leading-relaxed text-faint">
                费用为估算值：单价按每百万 token 计，取自模型接入配置；实际账单以厂商为准。
              </p>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

type DailyPoint = { date: string; tokens: number; cost: number; calls?: number };

function weekday(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? "" : WEEKDAYS[d.getDay()];
}

/** 选一个能整除 4 的刻度步长，Y 轴四等分后都是整数。 */
function niceScale(peak: number): { top: number; step: number } {
  if (peak <= 0) return { top: 4, step: 1 };
  const raw = peak / 4;
  const base = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / base;
  const step = (
    n <= 1 ? 1 : n <= 1.2 ? 1.2 : n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 2.5 ? 2.5 :
    n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 6 ? 6 : n <= 8 ? 8 : 10
  ) * base;
  return { top: step * 4, step };
}

/**
 * 每日 token 柱状图：Y 轴刻度 + 网格 + 日均虚线 + 选中日期的明细。
 * 选中用顶部读数条而不是浮动 tooltip，窄屏和最后几天也不会被裁掉。
 */
function DailyChart({ daily }: { daily: DailyPoint[] }) {
  const [picked, setPicked] = useState<number | null>(null);
  const n = daily.length;
  const peak = Math.max(0, ...daily.map((d) => d.tokens));
  const { top, step } = niceScale(peak);
  const total = daily.reduce((s, d) => s + d.tokens, 0);
  const avg = n ? total / n : 0;
  const H = 132;
  const TICKS = [0, 1, 2, 3, 4];
  const active = picked !== null && daily[picked] ? picked : n - 1;
  const item = daily[active];

  const labels = useMemo(() => {
    if (n <= 1) return n === 1 ? [0] : [];
    const count = Math.min(6, n);
    const step = (n - 1) / (count - 1);
    return Array.from(new Set(Array.from({ length: count }, (_, i) => Math.round(i * step))));
  }, [n]);

  if (!n) return null;

  return (
    <div className="card px-4 pb-3 pt-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-faint">
        <span>
          合计 <span className="tabular-nums text-muted">{tokens(total)}</span>
        </span>
        <span>
          日均 <span className="tabular-nums text-muted">{tokens(Math.round(avg))}</span>
        </span>
        <span>
          峰值 <span className="tabular-nums text-muted">{tokens(peak)}</span>
        </span>
        {avg > 0 && <span className="ml-auto hidden sm:inline">虚线为日均</span>}
      </div>

      <div className="relative mt-3" style={{ height: H }}>
        {/* Y 轴刻度 */}
        <div className="absolute inset-y-0 left-0 w-10">
          {TICKS.map((i) => (
            <span
              key={i}
              className="absolute right-0 -translate-y-1/2 text-[9.5px] tabular-nums text-faint"
              style={{ top: `${(1 - i / 4) * 100}%` }}
            >
              {i === 0 ? "0" : tokens(Math.round(step * i))}
            </span>
          ))}
        </div>

        <div className="relative ml-11 h-full">
          {TICKS.map((i) => (
            <div
              key={i}
              className={`absolute inset-x-0 border-t ${i === 0 ? "border-line" : "border-dashed border-line/60"}`}
              style={{ top: `${(1 - i / 4) * 100}%` }}
            />
          ))}
          {avg > 0 && (
            <div
              className="pointer-events-none absolute inset-x-0 border-t border-dotted border-accent/40"
              style={{ top: `${(1 - Math.min(1, avg / top)) * 100}%` }}
            />
          )}
          <div className="absolute inset-0 flex items-end gap-[2px]">
            {daily.map((d, i) => {
              const h = d.tokens ? Math.max(3, Math.round((d.tokens / top) * H)) : 2;
              const on = i === active;
              return (
                <button
                  key={d.date}
                  type="button"
                  aria-label={`${d.date} ${weekday(d.date)}，${d.tokens} tokens，${d.calls ?? 0} 次调用`}
                  onMouseEnter={() => setPicked(i)}
                  onMouseLeave={() => setPicked(null)}
                  onFocus={() => setPicked(i)}
                  onBlur={() => setPicked(null)}
                  onClick={() => setPicked(i)}
                  className="group relative flex h-full min-w-0 flex-1 items-end outline-none"
                >
                  <span
                    className="w-full rounded-t-[2px] transition-[height,opacity] duration-150"
                    style={{
                      height: h,
                      background: d.tokens ? "var(--accent)" : "var(--line)",
                      opacity: d.tokens ? (on ? 1 : 0.5) : on ? 1 : 0.7,
                    }}
                  />
                  <span className="pointer-events-none absolute inset-x-[-1px] inset-y-0 rounded-[2px] ring-1 ring-inset ring-accent/0 transition-[box-shadow] group-focus-visible:ring-accent/40" />
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="relative ml-11 mt-1.5 h-3.5">
        {labels.map((i) => (
          <span
            key={i}
            className="absolute whitespace-nowrap text-[9.5px] tabular-nums text-faint"
            style={{
              left: `${n <= 1 ? 0 : (i / (n - 1)) * 100}%`,
              transform: i === 0 ? "none" : i === n - 1 ? "translateX(-100%)" : "translateX(-50%)",
            }}
          >
            {daily[i].date.slice(5)}
          </span>
        ))}
      </div>

      {item && (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-panel2 px-2.5 py-1.5 text-[11px]">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.tokens ? "bg-accent" : "bg-faint/50"}`} />
          <span className="tabular-nums text-ink">
            {item.date.slice(5)} {weekday(item.date)}
          </span>
          <span className="tabular-nums text-muted">{tokens(item.tokens)} tokens</span>
          <span className="text-faint">·</span>
          <span className="tabular-nums text-muted">{item.calls ?? 0} 次调用</span>
          <span className="ml-auto tabular-nums text-faint">{money(item.cost)}</span>
        </div>
      )}
    </div>
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

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6">
      <div className="mb-2 flex items-end justify-between">
        <h2 className="text-[13.5px] font-medium">{title}</h2>
        {hint && <span className="text-[10.5px] text-faint">{hint}</span>}
      </div>
      <div className="card overflow-hidden">{children}</div>
    </section>
  );
}

function Row({
  left,
  title,
  sub,
  value,
  extra,
}: {
  left: React.ReactNode;
  title: string;
  sub: string;
  value: string;
  extra: string;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-line px-3.5 py-2.5 last:border-0">
      <div className="shrink-0">{left}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px]">{title}</div>
        <div className="mt-0.5 text-[10.5px] text-faint">{sub}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className="text-[12px] tabular-nums">{value}</div>
        <div className="text-[10.5px] text-faint">{extra}</div>
      </div>
    </div>
  );
}

function EmptyRow() {
  return <div className="px-3.5 py-6 text-center text-[11.5px] text-faint">暂无数据</div>;
}
