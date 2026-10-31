"use client";

import { useMemo, useState } from "react";

export type DailyChartSegment = {
  label: string;
  value: number;
  /** 柱段填充色，如 bg-accent / bg-ok / bg-danger / bg-faint */
  className: string;
};

export type DailyChartDay = {
  date: string; // YYYY-MM-DD
  segments: DailyChartSegment[];
  /** 明细行中间，如 "12.5k tokens · 4 次调用" / "完成 3 · 失败 1 · 停止 0" */
  detail: string;
  /** 明细行右侧，如 "$0.02"，没有就不传 */
  detailExtra?: string;
};

type DailyChartProps = {
  days: DailyChartDay[];
  /** Y 轴与合计/日均/峰值的数字格式，如 tokens 缩写或原样 */
  format: (n: number) => string;
  /** 读数单位，如 "tokens" / "次"，不传就不显示 */
  unit?: string;
  /** 堆叠图的图例，如完成/失败/停止；单系列不传 */
  legend?: { label: string; className: string }[];
  emptyText?: string;
};

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function weekday(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? "" : WEEKDAYS[d.getDay()];
}

/** 选一个能整除 4 的刻度步长，Y 轴四等分后都是整数。 */
function niceScale(peak: number): { top: number; step: number } {
  if (peak <= 0) return { top: 4, step: 1 };
  // 小数值（比如每天几次执行）不用 0.8、1.5 这类小数步长，否则四舍五入后
  // Y 轴会出现重复刻度：直接取整数步长。
  if (peak <= 40) {
    const step = Math.max(1, Math.ceil(peak / 4));
    return { top: step * 4, step };
  }
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
 * 每日柱状图（用量页与状态页共用，样式与交互保持一致）：
 * Y 轴刻度 + 网格 + 日均虚线 + 选中日期的明细。
 * 选中用底部明细条而不是浮动 tooltip，窄屏、触屏和最后几天也不会被裁掉；
 * 单系列是一段的柱，堆叠图是多段的柱，结构同一套。
 */
export default function DailyChart({ days, format, unit, legend, emptyText }: DailyChartProps) {
  const [picked, setPicked] = useState<number | null>(null);
  const n = days.length;
  const totals = days.map((d) => d.segments.reduce((s, g) => s + g.value, 0));
  const peak = Math.max(0, ...totals);
  const { top, step } = niceScale(peak);
  const total = totals.reduce((s, v) => s + v, 0);
  const avg = n ? total / n : 0;
  const H = 132;
  const TICKS = [0, 1, 2, 3, 4];
  const active = picked !== null && days[picked] ? picked : n - 1;
  const item = days[active];

  const labels = useMemo(() => {
    if (n <= 1) return n === 1 ? [0] : [];
    const count = Math.min(6, n);
    const s = (n - 1) / (count - 1);
    return Array.from(new Set(Array.from({ length: count }, (_, i) => Math.round(i * s))));
  }, [n]);

  if (!n) {
    return (
      <div className="card px-4 py-8 text-center text-[11.5px] text-faint">
        {emptyText || "这段时间没有每日数据"}
      </div>
    );
  }

  const dotClass = item?.segments.find((g) => g.value > 0)?.className || "bg-faint/50";

  return (
    <div className="card px-4 pb-3 pt-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-faint">
        <span>
          合计 <span className="tabular-nums text-muted">{format(total)}</span>
          {unit && <span> {unit}</span>}
        </span>
        <span>
          日均 <span className="tabular-nums text-muted">{format(Math.round(avg))}</span>
          {unit && <span> {unit}</span>}
        </span>
        <span>
          峰值 <span className="tabular-nums text-muted">{format(peak)}</span>
          {unit && <span> {unit}</span>}
        </span>
        {legend && (
          <span className="flex items-center gap-3">
            {legend.map((g) => (
              <span key={g.label} className="flex items-center gap-1">
                <span className={`h-2 w-2 rounded-sm ${g.className}`} />
                {g.label}
              </span>
            ))}
          </span>
        )}
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
              {i === 0 ? "0" : format(Math.round(step * i))}
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
            {days.map((d, i) => {
              const t = totals[i];
              const h = t ? Math.max(3, Math.round((t / top) * H)) : 2;
              const on = i === active;
              return (
                <button
                  key={d.date}
                  type="button"
                  aria-label={`${d.date} ${weekday(d.date)}，${d.detail}`}
                  onMouseEnter={() => setPicked(i)}
                  onMouseLeave={() => setPicked(null)}
                  onFocus={() => setPicked(i)}
                  onBlur={() => setPicked(null)}
                  onClick={() => setPicked(i)}
                  className="group relative flex h-full min-w-0 flex-1 items-end outline-none"
                >
                  <span
                    className="flex w-full flex-col-reverse overflow-hidden rounded-t-[2px] transition-[height,opacity] duration-150"
                    style={{
                      height: h,
                      opacity: t ? (on ? 1 : 0.5) : on ? 1 : 0.7,
                    }}
                  >
                    {t === 0 && <span className="h-full w-full" style={{ background: "var(--line)" }} />}
                    {d.segments.map(
                      (g) =>
                        g.value > 0 && (
                          <span
                            key={g.label}
                            className={`w-full ${g.className}`}
                            style={{ height: `${(g.value / t) * 100}%` }}
                          />
                        )
                    )}
                  </span>
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
            {days[i].date.slice(5)}
          </span>
        ))}
      </div>

      {item && (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-panel2 px-2.5 py-1.5 text-[11px]">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`} />
          <span className="tabular-nums text-ink">
            {item.date.slice(5)} {weekday(item.date)}
          </span>
          <span className="tabular-nums text-muted">{item.detail}</span>
          {item.detailExtra && (
            <span className="ml-auto tabular-nums text-faint">{item.detailExtra}</span>
          )}
        </div>
      )}
    </div>
  );
}
