import React from "react";
import { ArrowUpRight, Check, GitBranch, Layers, ShieldCheck } from "lucide-react";
import { LogoMark } from "@/components/ui/Logo";

export default function AuthMarketing({ title, description, note, badge = "为复杂工作，组建你的 AI 团队" }: {
  title: React.ReactNode;
  description: React.ReactNode;
  note?: React.ReactNode;
  badge?: string;
}) {
  return (
    <aside className="auth-story relative hidden min-w-0 flex-col overflow-hidden p-10 lg:flex xl:p-14">
      <div className="relative flex items-center gap-3"><LogoMark size={37} /><span className="text-[25px] font-semibold tracking-tight">Botly</span><span className="ml-auto text-[10px] tracking-[0.16em] text-muted">TEAM INTELLIGENCE</span></div>
      <div className="relative my-auto py-10">
        <p className="mb-6 flex items-center gap-2 text-[11px] font-medium tracking-wide text-muted"><span className="h-1.5 w-1.5 rounded-full bg-accent" />{badge}</p>
        <h1 className="max-w-[520px] text-[clamp(2.4rem,3.3vw,3.5rem)] font-semibold leading-[1.22] tracking-[-0.04em]">{title}</h1>
        <p className="mt-5 max-w-[430px] text-[13px] leading-7 text-muted">{description}</p>
        <div className="auth-process mt-10 max-w-[440px] overflow-hidden rounded-xl" aria-label="多智能体协作流程示意">
          <div className="flex items-center justify-between border-b border-line px-5 py-3 text-[10px] tracking-wide text-muted"><span>从一个目标，到完整交付</span><span className="text-faint">协作流程示意</span></div>
          {[
            { icon: GitBranch, title: "规划与拆解", desc: "总指挥理解目标，组织执行计划", label: "01" },
            { icon: Layers, title: "多角色协同", desc: "研究、写作、开发，各自专注所长", label: "02" },
            { icon: Check, title: "汇总与交付", desc: "整合成员成果，形成可用的结果", label: "03" },
          ].map(({ icon: Icon, title: name, desc, label }) => <div key={name} className="auth-process-step flex items-center gap-4 px-5 py-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-panel2 text-muted"><Icon size={17} strokeWidth={1.6} /></span>
            <div><p className="text-[12px] font-medium">{name}</p><p className="mt-1 text-[11px] text-muted">{desc}</p></div>
            <span className="ml-auto font-mono text-[10px] text-faint">{label}</span>
          </div>)}
        </div>
      </div>
      <div className="relative flex items-center gap-2 border-t border-line pt-5 text-[11px] text-muted"><ShieldCheck size={15} /><span>自主部署 · 过程可见 · 关键操作可控</span><ArrowUpRight size={15} className="ml-auto" /></div>
      {note && <p className="relative mt-3 text-[10px] leading-relaxed text-muted">{note}</p>}
    </aside>
  );
}
