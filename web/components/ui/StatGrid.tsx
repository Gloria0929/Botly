import type { LucideIcon } from "lucide-react";

export default function StatGrid({ items }: { items: { label: string; value: number | string; note?: string; icon: LucideIcon }[] }) {
  return <dl className="stats-grid">
    {items.map(({ label, value, note, icon: Icon }) => <div className="stat-item" key={label}>
      <div className="flex items-center justify-between gap-3">
        <dt className="text-[12px] font-medium text-muted">{label}</dt>
        <Icon size={16} strokeWidth={1.7} className="shrink-0 text-accent" aria-hidden="true" />
      </div>
      <dd className="mt-3"><span className="text-[28px] font-semibold leading-none tabular-nums tracking-tight">{value}</span>{note && <span className="mt-2.5 block text-[11px] text-faint">{note}</span>}</dd>
    </div>)}
  </dl>;
}
