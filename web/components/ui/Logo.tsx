/**
 * Animated multi-agent collaboration mark:
 * three agents orbit and pulse around a breathing shared core,
 * connected by flowing arcs.
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
      <defs>
        <linearGradient
          id="bg"
          x1="0"
          y1="0"
          x2="64"
          y2="64"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#EEF2FF" />
          <stop offset="1" stopColor="#FDF2F8" />
        </linearGradient>
        <linearGradient
          id="core"
          x1="22"
          y1="22"
          x2="42"
          y2="42"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#6366F1" />
          <stop offset="1" stopColor="#EC4899" />
        </linearGradient>
        <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
          <stop stopColor="#818CF8" stopOpacity="0.45" />
          <stop offset="1" stopColor="#818CF8" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* 柔和浅色底 */}
      <rect width="64" height="64" rx="18" fill="url(#bg)" />

      {/* 背景柔光，随核心呼吸 */}
      <circle cx="32" cy="32" r="20" fill="url(#glow)">
        <animate
          attributeName="r"
          values="18;22;18"
          dur="3s"
          repeatCount="indefinite"
        />
        <animate
          attributeName="opacity"
          values="0.6;1;0.6"
          dur="3s"
          repeatCount="indefinite"
        />
      </circle>

      {/* 流动的协同轨道：虚线缓慢旋转 */}
      <circle
        cx="32"
        cy="32"
        r="17"
        stroke="#C7D2FE"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="6 10"
        fill="none"
      >
        <animateTransform
          attributeName="transform"
          type="rotate"
          from="0 32 32"
          to="360 32 32"
          dur="14s"
          repeatCount="indefinite"
        />
      </circle>

      {/* 公转的三个 agent：整组绕核心旋转 */}
      <g>
        <animateTransform
          attributeName="transform"
          type="rotate"
          from="0 32 32"
          to="360 32 32"
          dur="8s"
          repeatCount="indefinite"
        />
        {/* agent 1 */}
        <circle cx="32" cy="15" r="4.5" fill="#6366F1">
          <animate
            attributeName="r"
            values="4.5;5.5;4.5"
            dur="2s"
            repeatCount="indefinite"
          />
        </circle>
        {/* agent 2 */}
        <circle cx="46.7" cy="40.5" r="4.5" fill="#EC4899">
          <animate
            attributeName="r"
            values="4.5;5.5;4.5"
            dur="2s"
            begin="0.6s"
            repeatCount="indefinite"
          />
        </circle>
        {/* agent 3 */}
        <circle cx="17.3" cy="40.5" r="4.5" fill="#10B981">
          <animate
            attributeName="r"
            values="4.5;5.5;4.5"
            dur="2s"
            begin="1.2s"
            repeatCount="indefinite"
          />
        </circle>
      </g>

      {/* 共享核心：呼吸脉动的渐变圆 */}
      <circle cx="32" cy="32" r="8" fill="url(#core)">
        <animate
          attributeName="r"
          values="7.5;8.8;7.5"
          dur="3s"
          repeatCount="indefinite"
        />
      </circle>
      <circle cx="29.5" cy="29.5" r="2.2" fill="#FFFFFF" opacity="0.75" />
    </svg>
  );
}
