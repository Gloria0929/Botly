import React from "react";
import { Eye, ShieldCheck, Workflow } from "lucide-react";
import { LogoMark } from "@/components/ui/Logo";

const FEATURES = [
  { icon: Workflow, k: "派任务", v: "总指挥拆解并调度" },
  { icon: Eye, k: "可见", v: "每个子任务独立线程" },
  { icon: ShieldCheck, k: "可控", v: "审批、限额与停止" },
];

/**
 * 登录 / 注册页左侧的品牌区：同一套光晕、网格与能力卡片，
 * 避免两页各写一份导致排版漂移。
 */
export default function AuthMarketing({
  title,
  description,
  note,
  badge = "多智能体协作",
}: {
  title: React.ReactNode;
  description: React.ReactNode;
  note?: React.ReactNode;
  badge?: string;
}) {
  return (
    <div className="relative hidden flex-col justify-between overflow-hidden border-r border-white/10 bg-[#0b0b0c] p-12 text-white lg:flex">
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(720px 460px at 8% 2%, rgba(255,92,92,.16), transparent 62%), radial-gradient(560px 420px at 94% 96%, rgba(20,184,166,.06), transparent 60%)",
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.28]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage: "linear-gradient(to bottom, black, transparent 78%)",
        }}
      />

      <div className="relative">
        <div className="flex items-center gap-2.5">
          <LogoMark size={32} />
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.08em] text-white">Botly</span>
        </div>

        <span className="mt-14 inline-flex items-center gap-1.5 rounded border border-accent/30 bg-accent/10 px-2.5 py-1 font-mono text-[9.5px] uppercase tracking-[0.16em] text-accent">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          {badge}
        </span>
        <h1 className="mt-5 max-w-md text-[40px] font-bold leading-[1.15] tracking-[-0.04em] text-white">
          {title}
        </h1>
        <p className="mt-4 max-w-sm text-[13px] leading-relaxed text-white/50">{description}</p>
      </div>

      <div className="relative">
        <div className="grid grid-cols-3 gap-3">
          {FEATURES.map(({ icon: Icon, k, v }) => (
            <div
              key={k}
              className="rounded-lg border border-white/10 bg-white/[0.025] p-3.5"
            >
              <Icon size={15} className="text-accent" />
              <div className="mt-2.5 text-[12px] font-medium text-white/90">{k}</div>
              <div className="mt-1 text-[11px] leading-relaxed text-white/40">{v}</div>
            </div>
          ))}
        </div>
        {note && (
          <p className="mt-5 max-w-sm text-[11px] leading-relaxed text-white/30">{note}</p>
        )}
      </div>
    </div>
  );
}
