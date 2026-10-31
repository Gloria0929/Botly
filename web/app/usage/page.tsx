"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  Coins,
  Gauge,
  Loader2,
  MessageSquare,
  RefreshCw,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import Avatar from "@/components/ui/Avatar";
import DailyChart from "@/components/ui/DailyChart";
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
                <DailyChart
                  days={(data?.daily || []).map((d) => ({
                    date: d.date,
                    segments: [{ label: "token", value: d.tokens, className: "bg-accent" }],
                    detail: `${tokens(d.tokens)} tokens · ${d.calls ?? 0} 次调用`,
                    detailExtra: money(d.cost),
                  }))}
                  format={tokens}
                  unit="tokens"
                />
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
                      <span className="flex h-6 w-6 items-center justify-center rounded-md bg-panel2">
                        <MessageSquare size={13} />
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
