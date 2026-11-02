/**
 * Botly 标识：由控制台提示符「>」构成的双层 B。
 * 左侧竖线是会话主干，两层折线既是字母 B 的两个腔体，
 * 也像两段命令提示；端点上的青色节点表示被调度的 Agent。
 */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      aria-label="Botly"
      role="img"
    >
      <rect width="64" height="64" rx="12" fill="var(--accent)" />
      <g
        stroke="var(--accent-ink)"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M20 15 V49" />
        <path d="M23 16 L43 24.5 L23 33" />
        <path d="M23 33 L48 43.5 L23 53" />
      </g>
      <circle cx="43" cy="24.5" r="3.4" fill="#2dd4bf" />
      <circle cx="48" cy="43.5" r="3.6" fill="#2dd4bf" />
    </svg>
  );
}
